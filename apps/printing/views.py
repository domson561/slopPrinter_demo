"""
Webhook drukarni (POST /api/printer/events/) i symulowany pulpit drukarni D1 (/mock-printer/).
D1 woła webhook przez HTTP, tą samą drogą co prawdziwa drukarnia.
"""

import json
import secrets

import requests
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.catalog.models import ShopSettings

from .models import MockPrinterJob, PrintJob
from .services import UnknownPrintJob, handle_printer_event, serialize_event, sign_payload, verify_signature

# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


@csrf_exempt
@require_POST
def printer_webhook(request):
    body = request.body
    signature = request.headers.get(settings.PRINTER_WEBHOOK_SIGNATURE_HEADER)
    if not verify_signature(body, signature):
        return JsonResponse({"ok": False, "error": "invalid signature"}, status=403)
    try:
        payload = json.loads(body.decode() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(payload, dict) or "external_id" not in payload or "status" not in payload:
        return JsonResponse({"ok": False, "error": "external_id and status are required"}, status=400)
    try:
        job, applied, note = handle_printer_event(payload)
    except UnknownPrintJob:
        return JsonResponse({"ok": False, "error": "unknown external_id"}, status=404)
    return JsonResponse(
        {
            "ok": True,
            "applied": applied,
            "note": note,
            "job_status": job.status,
            "order_number": job.order_item.order.number,
            "order_status": job.order_item.order.status,
        }
    )


# ---------------------------------------------------------------------------
# Mock drukarni (D1)
# ---------------------------------------------------------------------------

ACTION_TO_STATUS = {
    "accept": "accepted",
    "reject": "rejected",
    "printed": "printed",
    "shipped": "shipped",
    "defect": "defect",
    "delivered": "delivered",
}
ACTION_LABELS = {
    "accept": "Przyjmij",
    "reject": "Odrzuć",
    "printed": "Oznacz jako wydrukowane",
    "shipped": "Oznacz jako nadane",
    "defect": "Zgłoś wadę",
    "delivered": "Kurier: doręczone",
}
_LOCAL_STATUS = {
    "accepted": MockPrinterJob.Status.ACCEPTED,
    "rejected": MockPrinterJob.Status.REJECTED,
    "printed": MockPrinterJob.Status.PRINTED,
    "shipped": MockPrinterJob.Status.SHIPPED,
    "defect": MockPrinterJob.Status.DEFECT,
}


def webhook_url(request=None) -> str:
    if settings.PRINTER_WEBHOOK_URL:
        return settings.PRINTER_WEBHOOK_URL
    path = reverse("printing:webhook")
    if request is not None:
        return request.build_absolute_uri(path)
    return settings.SITE_URL + path


def send_printer_event(mock_job: MockPrinterJob, status: str, reason: str = "", request=None) -> dict:
    """Aktualizuje rekord drukarni i woła webhook serwisu przez HTTP z podpisem HMAC."""
    tracking = ""
    if status == "shipped":
        tracking = mock_job.tracking_number or f"MK{secrets.randbelow(10**10):010d}PL"
        mock_job.tracking_number = tracking
    if status in _LOCAL_STATUS:
        mock_job.status = _LOCAL_STATUS[status]
    mock_job.log(f"akcja: {status}", reason=reason)
    mock_job.save()

    body = serialize_event(mock_job.external_id, status, tracking_number=tracking or None, reason=reason or None)
    headers = {settings.PRINTER_WEBHOOK_SIGNATURE_HEADER: sign_payload(body), "Content-Type": "application/json"}
    url = webhook_url(request)
    try:
        resp = requests.post(url, data=body, headers=headers, timeout=15)
        result = {"http_status": resp.status_code, "response": _safe_json(resp)}
    except requests.RequestException as exc:
        result = {"http_status": None, "response": {"error": str(exc)}}
    mock_job.log("webhook", url=url, **result)
    mock_job.save(update_fields=["history", "updated_at"])
    return result


def _safe_json(resp):
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:300]}


def mock_printer_dashboard(request):
    shop = ShopSettings.get()
    jobs = MockPrinterJob.objects.all()
    return render(
        request,
        "printing/d1_dashboard.html",
        {
            "jobs": jobs,
            "shop": shop,
            "action_labels": ACTION_LABELS,
            "webhook_url": webhook_url(request),
        },
    )


def mock_printer_job(request, external_id):
    mock_job = get_object_or_404(MockPrinterJob, external_id=external_id)
    return render(
        request,
        "printing/d1_job.html",
        {
            "job": mock_job,
            "spec_json": json.dumps(mock_job.spec, indent=2, ensure_ascii=False),
            "history_json": json.dumps(mock_job.history, indent=2, ensure_ascii=False),
            "action_labels": ACTION_LABELS,
        },
    )


@require_POST
def mock_printer_action(request, external_id):
    mock_job = get_object_or_404(MockPrinterJob, external_id=external_id)
    action = request.POST.get("action", "")
    if action not in ACTION_TO_STATUS or action not in mock_job.next_actions:
        messages.error(request, "Ta akcja nie jest dostępna dla tego zlecenia.")
        return redirect("printing:mock_job", external_id=external_id)
    reason = request.POST.get("reason", "").strip()
    if action in ("reject", "defect") and not reason:
        messages.error(request, "Podaj powód.")
        return redirect("printing:mock_job", external_id=external_id)
    result = send_printer_event(mock_job, ACTION_TO_STATUS[action], reason=reason, request=request)
    if result["http_status"] == 200:
        messages.success(request, f"Webhook przyjęty: {result['response'].get('note') or 'ok'} (zamówienie: {result['response'].get('order_status')})")
    else:
        messages.error(request, f"Webhook odrzucony: HTTP {result['http_status']} {result['response']}")
    return redirect("printing:mock_job", external_id=external_id)


@require_POST
def mock_printer_toggle_auto(request):
    shop = ShopSettings.get()
    shop.printer_auto_simulate = not shop.printer_auto_simulate
    try:
        interval = int(request.POST.get("interval", shop.printer_auto_interval_seconds))
        shop.printer_auto_interval_seconds = max(2, min(interval, 600))
    except ValueError:
        pass
    shop.save()
    messages.info(request, "Auto-symulacja " + ("włączona." if shop.printer_auto_simulate else "wyłączona."))
    return redirect("printing:mock_dashboard")


@require_POST
def mock_printer_tick(request):
    """Jeden krok auto-symulacji: każde otwarte zlecenie posuwa się o jeden status."""
    from .tasks import advance_all_mock_jobs

    advanced = advance_all_mock_jobs(request=request)
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"advanced": advanced, "auto": ShopSettings.get().printer_auto_simulate})
    messages.info(request, f"Posunięto zleceń: {advanced}")
    return redirect("printing:mock_dashboard")


def mock_printer_file(request, external_id):
    """Plik do pobrania dla drukarni - ten sam PDF, który wskazuje spec.file.url."""
    mock_job = get_object_or_404(MockPrinterJob, external_id=external_id)
    job = PrintJob.objects.filter(external_id=external_id).select_related("print_file").first()
    if job is None or job.print_file is None:
        raise Http404("Brak pliku")
    return FileResponse(job.print_file.file.open("rb"), as_attachment=True, filename=f"{mock_job.spec.get('order_number', 'print')}.pdf")
