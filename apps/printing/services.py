"""
Zlecenia do GH po stronie sklepu: body POST-a z kontraktu v1, wysłanie z retry, polling
zmian (sklep pyta, GH nie woła) i mapowanie statusów GH na stan zlecenia i zamówienia.
"""

import hashlib
import logging
import time

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.orders.models import Actor, OrderStatus, Shipment

from . import links
from .models import PollCursor, PrintJob, PrintJobEvent
from .providers import (
    ProviderAuthError,
    ProviderError,
    ProviderRejected,
    RemoteOrderState,
    get_print_provider,
)

log = logging.getLogger("slop.printing")

S = OrderStatus
J = PrintJob.Status


class PrintSubmitError(Exception):
    pass


# ---------------------------------------------------------------------------
# Body żądania (kontrakt pkt 3 i 4)
# ---------------------------------------------------------------------------


def build_request(job: PrintJob) -> dict:
    """
    Jedno zlecenie na koszyk, pozycje listą. Bez danych kupującego: adres, telefon i e-mail
    idą wyłącznie na etykietę. `total_value` to na razie wartość detaliczna pozycji; cena
    hurtowa wejdzie z taskiem „Katalog”. Etykieta (`label`) dojdzie z taskiem „Etykieta”.
    """
    order = job.order
    items = order.items.select_related("asset").order_by("pk")
    body = {
        "shop_order_number": job.shop_order_number,
        "title": f"Zamówienie {job.shop_order_number}",
        "total_value": str(order.subtotal),
        "currency": order.currency,
        "items": [_item_body(position, item) for position, item in enumerate(items, start=1)],
    }
    note = ""
    if job.reprint_of is not None:
        note = f"Dodruk zamówienia {job.reprint_of.shop_order_number}. {job.reason}".strip()
    if note:
        body["note_for_production"] = note[:500]
    return body


def _item_body(position: int, item) -> dict:
    line = item.definition.gh_line(item)
    files = item.print_files
    return {
        "position": position,
        "name": line.name,
        "product_code": line.product_code,
        "quantity": item.quantity,
        "unit_price": str(item.unit_price),
        "file": file_object(files[0] if files else None),
    }


def check_print_files(order) -> None:
    """
    Kontrakt v1: dokładnie jeden plik do druku na pozycję. Pozycja z kilkoma plikami
    (np. okładka i blok książki) czeka na v1.1/v2 kontraktu.
    """
    for item in order.items.order_by("pk"):
        count = len(item.print_files)
        if count != 1:
            raise PrintSubmitError(
                f"Pozycja {item.summary}: plików do druku {count}, kontrakt v1 przyjmuje jeden."
            )


def file_object(version) -> dict:
    """
    Obiekt pliku z pkt 4: podpisany, wygasający link (`links.issue`), suma i rozmiar.
    `expires_at` to dokładnie termin ważności podpisu. Każde wywołanie wystawia świeży link.
    """
    if version is None:
        return {}
    url, expires_at = links.issue(version)
    return {
        "url": url,
        "sha256": _sha256_of(version),
        "size": _size_of(version),
        "expires_at": expires_at.isoformat(),
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


def _size_of(version) -> int:
    try:
        return version.file.size
    except (OSError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Tworzenie i wysyłka
# ---------------------------------------------------------------------------


def _next_shop_order_number(order) -> str:
    """Numer zamówienia dla GH: numer sklepu, a dla dodruków z przyrostkiem `-D1`, `-D2`…"""
    earlier = order.print_jobs.count()
    return order.number if earlier == 0 else f"{order.number}-D{earlier}"


def create_print_job(order, reprint_of: PrintJob | None = None, reason: str = "") -> PrintJob:
    check_print_files(order)
    job = PrintJob.objects.create(
        order=order,
        shop_order_number=_next_shop_order_number(order),
        reprint_of=reprint_of,
        reason=reason[:500],
    )
    job.spec = build_request(job)
    job.save(update_fields=["spec"])
    return job


def submit_print_job(job: PrintJob, provider=None) -> PrintJob:
    """
    Wysłanie z retry i backoffem na błędy przejściowe (pkt 7). 400/404/409 i 401 kończą
    od razu: zlecenie `failed`, zamówienie trafi w `on_hold` u wołającego.
    """
    provider = provider or get_print_provider()
    max_attempts = settings.PRINT_SUBMIT_MAX_ATTEMPTS
    backoff = settings.PRINT_SUBMIT_RETRY_BACKOFF_SECONDS
    last_error = None
    for attempt in range(1, max_attempts + 1):
        job.attempts = attempt
        try:
            state = provider.submit(job.spec)
        except ProviderError as exc:
            last_error = exc
            log.warning("submit %s próba %d/%d nieudana: %s", job, attempt, max_attempts, exc)
            job.save(update_fields=["attempts", "updated_at"])
            if attempt < max_attempts and backoff:
                time.sleep(backoff * (2 ** (attempt - 1)))
            continue
        except ProviderAuthError as exc:
            log.error("submit %s: %s", job, exc)
            cursor = PollCursor.get()
            cursor.auth_failed = True
            cursor.last_error = str(exc)[:300]
            cursor.save(update_fields=["auth_failed", "last_error"])
            return _fail_submit(job, f"GH odrzuciło token sklepu: {exc}")
        except ProviderRejected as exc:
            return _fail_submit(job, f"GH odrzuciło zlecenie ({exc.status_code}): {exc.errors}")
        job.sent_at = timezone.now()
        job.status = J.SENT
        job.save(update_fields=["sent_at", "status", "attempts", "updated_at"])
        PrintJobEvent.objects.create(
            print_job=job, status="sent", payload=state.as_dict(), note="POST przyjęty"
        )
        apply_remote_state(job, state, note="odpowiedź na POST")
        return job
    return _fail_submit(job, f"submit nieudany po {max_attempts} próbach: {last_error}")


def _fail_submit(job, reason):
    job.status = J.FAILED
    job.reason = reason[:500]
    job.save(update_fields=["status", "reason", "attempts", "updated_at"])
    PrintJobEvent.objects.create(print_job=job, status="failed", note=job.reason[:300])
    raise PrintSubmitError(job.reason)


def submit_order(order) -> PrintJob:
    """Jedno zlecenie na zamówienie. Otwarte zlecenie w GH nie jest dublowane."""
    existing = order.print_jobs.filter(status__in=[J.SENT, J.ACCEPTED, J.PRINTED]).first()
    if existing:
        return existing
    return submit_print_job(create_print_job(order))


def submit_reprint(order, reason: str) -> PrintJob:
    original = order.print_jobs.exclude(status=J.CANCELLED).order_by("-created_at").first()
    return submit_print_job(create_print_job(order, reprint_of=original, reason=reason))


def cancel_print_job(job: PrintJob, reason: str = ""):
    """
    GH nie zna anulowania (pkt 6). Przed POST-em zlecenie znika lokalnie; po wysłaniu sklep
    tylko oznacza je u siebie, a wstrzymanie produkcji to telefon do biura.
    """
    note = "anulowane przed wysłaniem"
    if job.sent_at is not None:
        note = "anulowane lokalnie, zlecenie jest już w GH: wstrzymanie wymaga kontaktu z biurem"
        log.warning("cancel %s: %s", job, note)
    job.status = J.CANCELLED
    job.reason = reason[:500]
    job.save(update_fields=["status", "reason", "updated_at"])
    PrintJobEvent.objects.create(
        print_job=job, status="cancelled", payload={"reason": reason}, note=note
    )


# ---------------------------------------------------------------------------
# Polling i mapowanie statusów (pkt 5 i 6)
# ---------------------------------------------------------------------------

_RANK = {J.CREATED: 0, J.SENT: 1, J.ACCEPTED: 2, J.PRINTED: 3, J.SHIPPED: 4}


def map_remote_status(state: RemoteOrderState) -> str:
    """Tabela z pkt 6 kontraktu: status GH plus stany plików -> `PrintJob.Status`."""
    if state.status == "new":
        return J.ACCEPTED if state.all_files_downloaded else J.SENT
    if state.status == "in_progress":
        return J.ACCEPTED
    if state.status == "ready":
        return J.PRINTED
    if state.status == "shipped":
        return J.SHIPPED
    return J.SENT


def apply_remote_state(job: PrintJob, state: RemoteOrderState, note: str = "") -> bool:
    """
    Nanosi stan z GH na zlecenie i zamówienie. Idempotentne: ten sam stan nic nie zmienia,
    stan „wstecz” jest zapisany w historii, ale ignorowany. Zwraca, czy coś się zmieniło.
    """
    from apps.orders import services as order_services

    job.external_id = state.gh_number or job.external_id
    job.provider_status = state.status
    job.files_state = state.files
    job.last_polled_at = timezone.now()
    new_status = map_remote_status(state)
    changed = False
    applied_note = note

    with transaction.atomic():
        if job.status in (J.REJECTED, J.FAILED, J.CANCELLED):
            applied_note = f"zlecenie zamknięte ({job.status}), status GH tylko zapisany"
        elif _RANK.get(new_status, 0) > _RANK.get(job.status, 0):
            job.status = new_status
            changed = True
            try:
                applied_note = _advance_order(job, new_status, state, order_services) or note
            except order_services.TransitionError as exc:
                applied_note = f"stan zamówienia nie pozwala: {exc}"[:300]
        elif new_status != job.status:
            applied_note = f"stan wstecz ({job.status} -> {new_status}), bez zmian"
        job.save(
            update_fields=[
                "external_id",
                "provider_status",
                "files_state",
                "last_polled_at",
                "status",
                "updated_at",
            ]
        )
        if changed or applied_note != note:
            PrintJobEvent.objects.create(
                print_job=job,
                status=state.status,
                payload=state.as_dict(),
                applied=changed,
                note=applied_note[:300],
            )
    return changed


def _advance_order(job, new_status, state, order_services) -> str:
    order = job.order
    if new_status == J.ACCEPTED:
        return "GH ma komplet plików" if state.status == "new" else "GH: w realizacji"
    if new_status == J.PRINTED:
        if order.status == S.SENT_TO_PRINTER:
            order_services.transition(order, S.PRINTED, Actor.PRINTER, "GH zgłosiło gotowość")
        return "zamówienie: wydrukowane"
    if new_status == J.SHIPPED:
        shipped_at = parse_datetime(state.shipped_at) if state.shipped_at else None
        Shipment.objects.create(
            order=order, carrier="Graphic House", shipped_at=shipped_at or timezone.now()
        )
        if order.status == S.SENT_TO_PRINTER:
            order_services.transition(
                order,
                S.PRINTED,
                Actor.PRINTER,
                "wydruk domniemany (wysyłka bez zgłoszenia gotowości)",
                notify=False,
            )
        if order.status == S.PRINTED:
            order_services.transition(order, S.SHIPPED, Actor.PRINTER, "GH wysłało paczkę")
        return "zamówienie: nadane"
    return ""


def resend_failed_files(job: PrintJob, provider) -> int:
    """Plik `failed` w GH nie zmienia statusu w sklepie, tylko uruchamia dosłanie linku."""
    sent = 0
    items = {item["position"]: item for item in job.spec.get("items", [])}
    for failed in job.failed_files:
        if failed.get("kind") != "item" or failed.get("position") not in items:
            continue
        order_items = list(job.order.items.order_by("pk"))
        position = failed["position"]
        order_item = order_items[position - 1] if 0 < position <= len(order_items) else None
        files = order_item.print_files if order_item else []
        fresh = file_object(files[0]) if len(files) == 1 else {}
        if not fresh:
            continue
        body = {"kind": "item", "position": failed["position"], "file": fresh}
        try:
            state = provider.resend_file(job.shop_order_number, body)
        except (ProviderRejected, ProviderError) as exc:
            log.warning("resend %s poz. %s: %s", job, failed["position"], exc)
            PrintJobEvent.objects.create(
                print_job=job, status="file_resend", applied=False, note=str(exc)[:300]
            )
            continue
        job.files_state = state.files
        job.save(update_fields=["files_state", "updated_at"])
        PrintJobEvent.objects.create(
            print_job=job,
            status="file_resend",
            payload=body,
            note=f"dosłano świeży link do pozycji {failed['position']} ({failed.get('error')})",
        )
        sent += 1
    return sent


def poll_changes(provider=None) -> dict:
    """
    Jeden cykl pollingu (pkt 5): `GET changes?since=` z kursorem z poprzedniej odpowiedzi.
    Zwraca podsumowanie. Po 401 polling stoi, dopóki operator nie zresetuje kursora.
    """
    cursor = PollCursor.get()
    summary = {"orders": 0, "changed": 0, "resent": 0, "error": ""}
    if cursor.auth_failed:
        summary["error"] = "polling wstrzymany: GH odrzuciło token sklepu"
        return summary
    provider = provider or get_print_provider()
    try:
        result = provider.fetch_changes(cursor.since)
    except ProviderAuthError as exc:
        cursor.auth_failed = True
        cursor.last_error = str(exc)[:300]
        cursor.last_polled_at = timezone.now()
        cursor.save()
        log.error("polling GH: %s", exc)
        summary["error"] = str(exc)
        return summary
    except (ProviderError, ProviderRejected) as exc:
        cursor.last_error = str(exc)[:300]
        cursor.last_polled_at = timezone.now()
        cursor.save()
        log.warning("polling GH: %s", exc)
        summary["error"] = str(exc)
        return summary

    jobs = {
        job.shop_order_number: job
        for job in PrintJob.objects.filter(
            shop_order_number__in=[o.shop_order_number for o in result.orders]
        ).select_related("order", "reprint_of")
    }
    for state in result.orders:
        job = jobs.get(state.shop_order_number)
        if job is None:
            log.warning("polling GH: nieznane zamówienie %s", state.shop_order_number)
            continue
        summary["orders"] += 1
        if apply_remote_state(job, state, note="polling"):
            summary["changed"] += 1
        if job.failed_files and job.is_open:
            summary["resent"] += resend_failed_files(job, provider)

    cursor.since = parse_datetime(result.server_time) or timezone.now()
    cursor.last_polled_at = timezone.now()
    cursor.last_error = ""
    cursor.save()
    return summary


def reset_poll_auth():
    """Operator wymienił token: polling i wysyłka ruszają ponownie."""
    cursor = PollCursor.get()
    cursor.auth_failed = False
    cursor.last_error = ""
    cursor.save(update_fields=["auth_failed", "last_error"])
