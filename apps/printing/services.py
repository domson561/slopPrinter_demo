"""
Zlecenia do drukarni: budowa specyfikacji (§8.2), submit z retry, webhook statusów,
mapowanie stanów zlecenia na stany zamówienia.
"""

import hashlib
import hmac
import json
import logging
import time

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.orders.models import Actor, OrderStatus, Shipment

from .models import PrintJob, PrintJobEvent
from .providers import ProviderError, get_print_provider

log = logging.getLogger("slop.printing")

S = OrderStatus
J = PrintJob.Status


class PrintSubmitError(Exception):
    pass


class UnknownPrintJob(Exception):
    pass


# ---------------------------------------------------------------------------
# Specyfikacja i submit
# ---------------------------------------------------------------------------


def build_spec(job: PrintJob) -> dict:
    item = job.order_item
    order = item.order
    fmt = item.format
    print_file = job.print_file
    address = order.shipping_address or {}
    file_url = ""
    sha = ""
    if print_file is not None:
        file_url = settings.SITE_URL + print_file.file.url
        sha = _sha256_of(print_file)
    return {
        "job_id": job.job_id,
        "order_number": order.number,
        "product": "poster",
        "format": {
            "code": fmt.code,
            "width_mm": fmt.width_mm,
            "height_mm": fmt.height_mm,
            "bleed_mm": fmt.bleed_mm,
            "orientation": item.orientation,
        },
        "paper": item.paper.code if item.paper else None,
        "quantity": item.quantity,
        "white_margin": item.white_margin,
        "file": {"url": file_url, "sha256": sha, "color_space": "RGB"},
        "shipping": {
            "method": order.delivery_method.code if order.delivery_method else None,
            "name": order.full_name,
            "address": {
                "line1": address.get("line1", ""),
                "line2": address.get("line2", ""),
                "postal_code": address.get("postal_code", ""),
                "city": address.get("city", ""),
                "country": address.get("country", "PL"),
            },
            "phone": order.phone,
            "email": order.email,
        },
        "reprint_of": job.reprint_of.job_id if job.reprint_of else None,
        "created_at": (job.created_at or timezone.now()).isoformat(),
    }


def _sha256_of(version) -> str:
    h = hashlib.sha256()
    try:
        version.file.open("rb")
        for chunk in version.file.chunks():
            h.update(chunk)
        version.file.close()
    except (OSError, ValueError):
        return ""
    return h.hexdigest()


def create_print_job(order_item, reprint_of: PrintJob | None = None, reason: str = "") -> PrintJob:
    job = PrintJob.objects.create(
        order_item=order_item,
        print_file=order_item.print_file,
        reprint_of=reprint_of,
        reason=reason[:500],
    )
    job.spec = build_spec(job)
    job.save(update_fields=["spec"])
    return job


def submit_print_job(job: PrintJob, provider=None) -> PrintJob:
    """Submit z retry i backoffem (max PRINT_SUBMIT_MAX_ATTEMPTS). Po wyczerpaniu: PrintSubmitError."""
    provider = provider or get_print_provider()
    max_attempts = settings.PRINT_SUBMIT_MAX_ATTEMPTS
    backoff = settings.PRINT_SUBMIT_RETRY_BACKOFF_SECONDS
    last_error = None
    for attempt in range(1, max_attempts + 1):
        job.attempts = attempt
        try:
            external_id = provider.submit(job)
        except ProviderError as exc:
            last_error = exc
            log.warning("submit %s próba %d/%d nieudana: %s", job, attempt, max_attempts, exc)
            job.save(update_fields=["attempts", "updated_at"])
            if attempt < max_attempts and backoff:
                time.sleep(backoff * (2 ** (attempt - 1)))
            continue
        job.external_id = external_id
        job.status = J.SENT
        job.sent_at = timezone.now()
        job.save(update_fields=["external_id", "status", "sent_at", "attempts", "updated_at"])
        PrintJobEvent.objects.create(
            print_job=job,
            status=J.SENT,
            payload={"external_id": external_id, "provider": provider.name},
        )
        return job
    job.status = J.FAILED
    job.reason = f"submit nieudany po {max_attempts} próbach: {last_error}"[:500]
    job.save(update_fields=["status", "reason", "attempts", "updated_at"])
    raise PrintSubmitError(job.reason)


def submit_order(order) -> list[PrintJob]:
    jobs = []
    for item in order.items.all():
        existing = item.print_jobs.filter(status__in=[J.SENT, J.ACCEPTED, J.PRINTED]).first()
        if existing:
            jobs.append(existing)
            continue
        job = create_print_job(item)
        jobs.append(submit_print_job(job))
    return jobs


def submit_reprint(order, reason: str) -> list[PrintJob]:
    jobs = []
    for item in order.items.all():
        original = item.print_jobs.exclude(status=J.CANCELLED).order_by("-created_at").first()
        job = create_print_job(item, reprint_of=original, reason=reason)
        jobs.append(submit_print_job(job))
    return jobs


def cancel_print_job(job: PrintJob, reason: str = ""):
    if job.external_id:
        try:
            get_print_provider().cancel(job.external_id)
        except (ProviderError, NotImplementedError) as exc:
            log.warning("cancel %s: %s", job, exc)
    job.status = J.CANCELLED
    job.reason = reason[:500]
    job.save(update_fields=["status", "reason", "updated_at"])
    PrintJobEvent.objects.create(print_job=job, status=J.CANCELLED, payload={"reason": reason})


# ---------------------------------------------------------------------------
# Webhook: podpis i obsługa zdarzeń
# ---------------------------------------------------------------------------


def sign_payload(body: bytes) -> str:
    return hmac.new(settings.PRINTER_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign_payload(body), signature.strip())


def serialize_event(external_id, status, tracking_number=None, reason=None) -> bytes:
    payload = {
        "external_id": external_id,
        "status": status,
        "timestamp": timezone.now().isoformat(),
    }
    if tracking_number:
        payload["tracking_number"] = tracking_number
    if reason:
        payload["reason"] = reason
    return json.dumps(payload).encode()


# Ranga statusów zlecenia: zdarzenia "wstecz" są zapisywane, ale nie zmieniają nic.
_RANK = {J.CREATED: 0, J.SENT: 1, J.ACCEPTED: 2, J.PRINTED: 3, J.SHIPPED: 4}
_EVENT_TO_JOB_STATUS = {
    "accepted": J.ACCEPTED,
    "rejected": J.REJECTED,
    "printed": J.PRINTED,
    "shipped": J.SHIPPED,
    "defect": J.FAILED,
    "delivered": None,  # tylko stan zamówienia
}


def handle_printer_event(payload: dict) -> tuple[PrintJob, bool, str]:
    """
    Obsługa zdarzenia z drukarni. Zwraca (job, applied, note).
    Idempotentne: powtórzone zdarzenie zapisuje PrintJobEvent, ale nie zmienia stanów.
    """
    from apps.orders import services as order_services

    external_id = str(payload.get("external_id", ""))
    status = str(payload.get("status", ""))
    job = (
        PrintJob.objects.select_related("order_item__order").filter(external_id=external_id).first()
    )
    if job is None:
        raise UnknownPrintJob(external_id)
    if status not in _EVENT_TO_JOB_STATUS:
        event = PrintJobEvent.objects.create(
            print_job=job, status=status, payload=payload, applied=False, note="nieznany status"
        )
        return job, False, event.note

    order = job.order_item.order
    new_job_status = _EVENT_TO_JOB_STATUS[status]
    reason = str(payload.get("reason", "") or "")
    note = ""
    applied = True

    with transaction.atomic():
        if new_job_status is not None:
            if job.status == new_job_status:
                applied, note = False, "powtórzone zdarzenie, bez zmian"
            elif (
                new_job_status in _RANK
                and job.status in _RANK
                and _RANK[new_job_status] <= _RANK[job.status]
            ):
                applied, note = (
                    False,
                    f"zdarzenie wstecz ({job.status} -> {new_job_status}), bez zmian",
                )
            elif job.status in (J.REJECTED, J.FAILED, J.CANCELLED):
                applied, note = False, f"zlecenie zamknięte ({job.status}), bez zmian"
        if applied:
            try:
                note = _apply_event(
                    job, order, status, new_job_status, reason, payload, order_services
                )
            except order_services.TransitionError as exc:
                applied, note = False, f"stan zamówienia nie pozwala: {exc}"[:300]
        PrintJobEvent.objects.create(
            print_job=job, status=status, payload=payload, applied=applied, note=note
        )
    return job, applied, note


def _apply_event(job, order, status, new_job_status, reason, payload, order_services) -> str:
    if new_job_status is not None:
        job.status = new_job_status
        if reason:
            job.reason = reason[:500]
        job.save(update_fields=["status", "reason", "updated_at"])

    if status == "accepted":
        return "zlecenie przyjęte przez drukarnię"

    if status == "rejected":
        order_services.transition(
            order,
            S.ON_HOLD,
            Actor.PRINTER,
            f"Drukarnia odrzuciła zlecenie: {reason or 'bez powodu'}"[:500],
        )
        return "zamówienie wstrzymane"

    if status == "printed":
        order_services.transition(order, S.PRINTED, Actor.PRINTER, "drukarnia zgłosiła wydruk")
        return "zamówienie: wydrukowane"

    if status == "shipped":
        tracking = str(payload.get("tracking_number", "") or "")
        Shipment.objects.create(
            order=order,
            carrier=str(payload.get("carrier", "") or "MockKurier"),
            tracking_number=tracking,
        )
        if order.status == S.SENT_TO_PRINTER:
            order_services.transition(
                order,
                S.PRINTED,
                Actor.PRINTER,
                "wydruk domniemany (nadanie bez zgłoszenia wydruku)",
                notify=False,
            )
        order_services.transition(order, S.SHIPPED, Actor.PRINTER, f"nadane, nr {tracking}"[:500])
        return "zamówienie: nadane"

    if status == "defect":
        order_services.request_reprint(
            order, Actor.PRINTER, f"Wada z produkcji: {reason or 'bez opisu'}"[:500]
        )
        return "zlecono dodruk"

    if status == "delivered":
        order_services.mark_delivered(order, Actor.PRINTER, reason="kurier potwierdził doręczenie")
        return "zamówienie: doręczone"

    return ""
