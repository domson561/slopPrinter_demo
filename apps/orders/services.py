"""
Logika zamówień: maszyna stanów, tworzenie zamówienia, płatność, moderacja, dodruk, reklamacje.
Widoki są cienkie i wołają wyłącznie funkcje z tego modułu.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.assets import quality
from apps.assets.services import FLAG_LABELS, run_pipeline
from apps.catalog.models import ShopSettings
from apps.catalog.services import quote

from . import notifications
from .models import Actor, Complaint, ComplaintPhoto, Order, OrderItem, OrderStatus, OrderStatusEvent, Payment

log = logging.getLogger("slop.orders")

S = OrderStatus


class TransitionError(Exception):
    pass


# Tabela dozwolonych przejść [DECYZJA] - zgodna z diagramem w seedzie §6.2.
TRANSITIONS: dict[str, set[str]] = {
    S.DRAFT: {S.AWAITING_PAYMENT},
    S.AWAITING_PAYMENT: {S.PAID, S.CANCELLED},
    S.PAID: {S.PROCESSING},
    S.PROCESSING: {S.AWAITING_APPROVAL, S.ON_HOLD},
    S.AWAITING_APPROVAL: {S.SENT_TO_PRINTER, S.ON_HOLD},
    S.ON_HOLD: {S.PROCESSING, S.AWAITING_APPROVAL, S.CANCELLED},
    S.SENT_TO_PRINTER: {S.PRINTED, S.ON_HOLD},
    S.PRINTED: {S.SHIPPED, S.REPRINT},
    S.SHIPPED: {S.DELIVERED},
    S.DELIVERED: {S.REPRINT},
    S.REPRINT: {S.SENT_TO_PRINTER},
    S.CANCELLED: set(),
}

# Etykiety dla klienta (S7, e-maile): tytuł + zdanie dla laika.
CUSTOMER_LABELS: dict[str, tuple[str, str]] = {
    S.DRAFT: ("Konfiguracja", "Zamówienie nie zostało jeszcze złożone."),
    S.AWAITING_PAYMENT: ("Oczekujemy na płatność", "Dokończ płatność, żebyśmy mogli zacząć."),
    S.PAID: ("Płatność przyjęta", "Dziękujemy. Zaraz zajmiemy się Twoim plikiem."),
    S.PROCESSING: ("Przygotowujemy plik do druku", "Poprawiamy jakość i przygotowujemy plik produkcyjny."),
    S.AWAITING_APPROVAL: ("Sprawdzamy plik", "Nasz operator sprawdza, czy wszystko wydrukuje się dobrze."),
    S.ON_HOLD: ("Zamówienie wymaga dodatkowej weryfikacji", "Skontaktujemy się, jeśli będziemy czegoś potrzebować."),
    S.SENT_TO_PRINTER: ("W drukarni", "Plik został przekazany do druku."),
    S.PRINTED: ("Wydrukowane", "Twój plakat jest gotowy i czeka na pakowanie."),
    S.SHIPPED: ("Nadane", "Paczka jest w drodze. Numer śledzenia znajdziesz poniżej."),
    S.DELIVERED: ("Doręczone", "Mamy nadzieję, że plakat wygląda świetnie na ścianie."),
    S.REPRINT: ("Drukujemy ponownie", "Zleciliśmy dodruk. Nowa paczka jest w drodze do produkcji."),
    S.CANCELLED: ("Anulowane", "Zamówienie zostało anulowane. Jeśli zapłaciłeś, zwrot jest w drodze."),
}

# Stany, przy których klient dostaje e-mail.
NOTIFY_STATUSES = {
    S.PAID,
    S.ON_HOLD,
    S.SENT_TO_PRINTER,
    S.PRINTED,
    S.SHIPPED,
    S.DELIVERED,
    S.REPRINT,
    S.CANCELLED,
}


def customer_label(status):
    return CUSTOMER_LABELS.get(status, (status, ""))


def can_transition(from_status, to_status) -> bool:
    return to_status in TRANSITIONS.get(from_status, set())


def transition(order: Order, to_status: str, actor: str, reason: str = "", actor_user=None, notify: bool = True) -> OrderStatusEvent:
    """Jedyna droga zmiany stanu. Przejścia spoza tabeli rzucają TransitionError."""
    if not can_transition(order.status, to_status):
        raise TransitionError(f"Niedozwolone przejście {order.status} -> {to_status} dla {order}")
    return _apply_transition(order, to_status, actor, reason, actor_user, forced=False, notify=notify)


def force_transition(order: Order, to_status: str, actor_user, reason: str) -> OrderStatusEvent:
    """Ręczne, wymuszone przejście operatora. Powód obowiązkowy, zawsze logowane jako operator."""
    if not reason or not reason.strip():
        raise TransitionError("Wymuszone przejście wymaga powodu.")
    if to_status not in S.values:
        raise TransitionError(f"Nieznany stan: {to_status}")
    if to_status == order.status:
        raise TransitionError("Zamówienie jest już w tym stanie.")
    return _apply_transition(order, to_status, Actor.OPERATOR, reason, actor_user, forced=True, notify=True)


def _apply_transition(order, to_status, actor, reason, actor_user, forced, notify):
    with transaction.atomic():
        from_status = order.status
        order.status = to_status
        if to_status == S.ON_HOLD:
            order.hold_reason = (reason or "")[:500]
        elif from_status == S.ON_HOLD:
            order.hold_reason = ""
        order.save(update_fields=["status", "hold_reason", "updated_at"])
        event = OrderStatusEvent.objects.create(
            order=order,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            actor_user=actor_user if getattr(actor_user, "pk", None) else None,
            reason=(reason or "")[:500],
            forced=forced,
        )
        _after_transition(order, event)
    log.info("order %s: %s -> %s (%s%s) %s", order.number, from_status, to_status, actor, " forced" if forced else "", reason)
    if notify and to_status in NOTIFY_STATUSES:
        notifications.send_status_email(order, event)
    return event


def _after_transition(order, event):
    """Skutki uboczne niezależne od aktora: retencja plików."""
    shop = ShopSettings.get()
    if event.to_status in (S.DELIVERED, S.CANCELLED):
        expires = timezone.now() + timedelta(days=shop.retention_days_after_close)
        for item in order.items.all():
            item.asset.expires_at = expires
            item.asset.save(update_fields=["expires_at"])
    elif event.to_status == S.PAID:
        for item in order.items.all():
            item.asset.expires_at = None
            item.asset.save(update_fields=["expires_at"])


# ---------------------------------------------------------------------------
# Szkic zamówienia (S2)
# ---------------------------------------------------------------------------


def recalc_totals(order: Order) -> Order:
    subtotal = Decimal("0.00")
    for item in order.items.select_related("format", "paper"):
        q = quote(item.format, item.paper, item.quantity, item.white_margin)
        item.unit_price = q.unit_price
        item.save(update_fields=["unit_price"])
        subtotal += q.subtotal
    order.subtotal = subtotal
    order.delivery_price = Decimal(order.delivery_method.price) if order.delivery_method else Decimal("0.00")
    order.total = (order.subtotal + order.delivery_price).quantize(Decimal("0.01"))
    order.currency = ShopSettings.get().currency
    order.save(update_fields=["subtotal", "delivery_price", "total", "currency", "updated_at"])
    return order


def save_draft(asset, fmt, paper, *, white_margin, quantity, crop, enhancer_enabled, order: Order | None = None) -> Order:
    """Tworzy lub aktualizuje szkic z jedną pozycją. Kadr jest sanityzowany."""
    crop = quality.sanitize_crop(crop, asset.width, asset.height, fmt)
    q = quality.quality_for(asset.width, asset.height, fmt, ShopSettings.get(), crop)
    if q.blocked:
        raise TransitionError(f"Format {fmt.name} jest zablokowany dla tego pliku (za słaby plik).")
    with transaction.atomic():
        if order is None or not order.is_draft:
            order = Order.objects.create(status=S.DRAFT)
        item = order.items.first()
        if item is None:
            item = OrderItem(order=order, asset=asset)
        item.asset = asset
        item.format = fmt
        item.paper = paper
        item.white_margin = bool(white_margin)
        item.quantity = max(int(quantity or 1), 1)
        item.crop = crop
        item.enhancer_enabled = bool(enhancer_enabled)
        item.save()
        recalc_totals(order)
    return order


# ---------------------------------------------------------------------------
# Checkout i płatność (S4 -> S5)
# ---------------------------------------------------------------------------


def start_checkout(order: Order, *, email, full_name, phone, address: dict, delivery_method, consents: dict) -> str:
    """Zapisuje dane, przechodzi do awaiting_payment i zwraca URL bramki (mock)."""
    from .payments import get_payment_provider

    with transaction.atomic():
        order.email = email
        order.full_name = full_name
        order.phone = phone
        order.shipping_address = address
        order.delivery_method = delivery_method
        now = timezone.now().isoformat()
        order.consents = {key: now for key, value in consents.items() if value}
        order.save()
        recalc_totals(order)
        if order.status == S.DRAFT:
            transition(order, S.AWAITING_PAYMENT, Actor.SYSTEM, "klient przeszedł do płatności")
    return get_payment_provider().create(order)


def mark_paid(order: Order, payment: Payment | None = None):
    """Wołane z callbacku płatności. Idempotentne: drugi callback nic nie zmienia."""
    if order.status != S.AWAITING_PAYMENT:
        log.info("mark_paid: %s już w stanie %s, pomijam", order.number, order.status)
        return
    transition(order, S.PAID, Actor.SYSTEM, f"płatność {payment.external_id}" if payment else "płatność potwierdzona")
    from .tasks import process_order_task

    process_order_task(order.pk)


def process_order(order: Order):
    """
    Pipeline w tle po płatności: paid/on_hold -> processing -> awaiting_approval | on_hold.
    Wyjątek w przetwarzaniu -> on_hold z powodem technicznym, poprzednie wersje zostają.
    """
    order.refresh_from_db()
    if order.status in (S.PAID, S.ON_HOLD):
        transition(order, S.PROCESSING, Actor.SYSTEM, "start pipeline")
    elif order.status != S.PROCESSING:
        raise TransitionError(f"process_order: nieoczekiwany stan {order.status}")

    try:
        for item in order.items.select_related("asset", "format"):
            run_pipeline(item)
    except Exception as exc:  # noqa: BLE001 - każdy błąd pipeline ląduje w on_hold
        log.exception("pipeline failed for %s", order.number)
        transition(order, S.ON_HOLD, Actor.SYSTEM, f"Błąd przetwarzania pliku: {type(exc).__name__}: {exc}"[:500])
        return

    flags = []
    for item in order.items.select_related("asset"):
        analysis = getattr(item.asset, "analysis", None)
        if analysis and analysis.content_flags:
            flags.extend(analysis.content_flags)
    if flags:
        labels = ", ".join(FLAG_LABELS.get(f, f) for f in dict.fromkeys(flags))
        transition(order, S.ON_HOLD, Actor.SYSTEM, f"Treść do weryfikacji: {labels}")
        return

    transition(order, S.AWAITING_APPROVAL, Actor.SYSTEM, "plik gotowy")
    if ShopSettings.get().auto_approve:
        approve_order(order, actor=Actor.SYSTEM, reason="auto-zatwierdzanie włączone")


# ---------------------------------------------------------------------------
# Operator (O3/O4)
# ---------------------------------------------------------------------------


def approve_order(order: Order, actor=Actor.OPERATOR, actor_user=None, reason: str = ""):
    """Zatwierdzenie: wysyła zlecenia do drukarni; sukces -> sent_to_printer, porażka -> on_hold."""
    from apps.printing.services import PrintSubmitError, submit_order

    order.refresh_from_db()
    if order.status == S.ON_HOLD:
        transition(order, S.AWAITING_APPROVAL, actor, reason or "zatwierdzone po weryfikacji", actor_user)
    if order.status != S.AWAITING_APPROVAL:
        raise TransitionError(f"Nie można zatwierdzić zamówienia w stanie {order.status}.")
    if any(item.print_file is None for item in order.items.all()):
        raise TransitionError("Brak pliku do druku. Uruchom ponowne przetworzenie.")
    try:
        submit_order(order)
    except PrintSubmitError as exc:
        transition(order, S.ON_HOLD, Actor.SYSTEM, f"Drukarnia nie przyjęła zlecenia: {exc}"[:500])
        return
    transition(order, S.SENT_TO_PRINTER, actor, reason or "zatwierdzone", actor_user)


def reject_order(order: Order, actor_user, reason: str):
    """Odrzucenie przez operatora = anulowanie ze zwrotem. Powód obowiązkowy."""
    if not reason.strip():
        raise TransitionError("Odrzucenie wymaga powodu.")
    cancel_order(order, Actor.OPERATOR, reason, actor_user)


def cancel_order(order: Order, actor, reason: str, actor_user=None):
    """Anulowanie ze zwrotem (symulacja). Z niedozwolonych stanów tylko operator, jako wymuszenie."""
    from .payments import get_payment_provider

    order.refresh_from_db()
    if can_transition(order.status, S.CANCELLED):
        transition(order, S.CANCELLED, actor, reason, actor_user)
    else:
        if actor != Actor.OPERATOR:
            raise TransitionError(f"Nie można anulować zamówienia w stanie {order.status}.")
        force_transition(order, S.CANCELLED, actor_user, reason)
    payment = order.payments.filter(status=Payment.Status.SUCCEEDED).first()
    if payment:
        get_payment_provider().refund(payment, payment.amount)
    for job in _open_print_jobs(order):
        from apps.printing.services import cancel_print_job

        cancel_print_job(job, reason)


def _open_print_jobs(order):
    from apps.printing.models import PrintJob

    return PrintJob.objects.filter(order_item__order=order).exclude(
        status__in=[PrintJob.Status.SHIPPED, PrintJob.Status.REJECTED, PrintJob.Status.FAILED, PrintJob.Status.CANCELLED]
    )


def reprocess_order(order: Order, actor_user, reason: str = "ponowne przetworzenie"):
    """Wymuszenie ponownego pipeline z on_hold (lub awaiting_approval przez on_hold)."""
    from .tasks import process_order_task

    order.refresh_from_db()
    if order.status == S.AWAITING_APPROVAL:
        transition(order, S.ON_HOLD, Actor.OPERATOR, reason, actor_user, notify=False)
    if order.status != S.ON_HOLD:
        raise TransitionError(f"Ponowne przetworzenie możliwe tylko z on_hold (jest {order.status}).")
    OrderStatusEvent.objects.create(
        order=order, from_status=order.status, to_status=order.status, actor=Actor.OPERATOR, actor_user=actor_user, reason=reason
    )
    process_order_task(order.pk)


def request_reprint(order: Order, actor, reason: str, actor_user=None):
    """Dodruk: nowe zlecenie do drukarni z tym samym plikiem, powiązane z pierwotnym."""
    from apps.printing.services import PrintSubmitError, submit_reprint

    order.refresh_from_db()
    if can_transition(order.status, S.REPRINT):
        transition(order, S.REPRINT, actor, reason, actor_user)
    else:
        if actor != Actor.OPERATOR:
            raise TransitionError(f"Nie można zlecić dodruku w stanie {order.status}.")
        force_transition(order, S.REPRINT, actor_user, reason)
    try:
        submit_reprint(order, reason)
    except PrintSubmitError as exc:
        transition(order, S.ON_HOLD, Actor.SYSTEM, f"Drukarnia nie przyjęła dodruku: {exc}"[:500])
        return
    transition(order, S.SENT_TO_PRINTER, Actor.SYSTEM, "dodruk wysłany do drukarni")


def mark_delivered(order: Order, actor=Actor.SYSTEM, actor_user=None, reason="doręczone"):
    transition(order, S.DELIVERED, actor, reason, actor_user)


# ---------------------------------------------------------------------------
# Reklamacje (S7 / O3)
# ---------------------------------------------------------------------------


def file_complaint(order: Order, description: str, photos=()) -> Complaint:
    if not order.can_complain:
        raise TransitionError("Reklamację można zgłosić po doręczeniu.")
    with transaction.atomic():
        complaint = Complaint.objects.create(order=order, description=description)
        for photo in photos:
            ComplaintPhoto.objects.create(complaint=complaint, image=photo)
        OrderStatusEvent.objects.create(
            order=order, from_status=order.status, to_status=order.status, actor=Actor.CUSTOMER, reason="zgłoszono reklamację"
        )
    notifications.send_complaint_received_email(order, complaint)
    return complaint


def resolve_complaint(complaint: Complaint, actor_user, decision: str, resolution: str):
    if complaint.status != Complaint.Status.OPEN:
        raise TransitionError("Reklamacja jest już rozpatrzona.")
    if not resolution.strip():
        raise TransitionError("Rozstrzygnięcie wymaga uzasadnienia.")
    if decision not in (Complaint.Status.REPRINT, Complaint.Status.REJECTED):
        raise TransitionError("Nieznana decyzja.")
    complaint.status = decision
    complaint.resolution = resolution
    complaint.resolved_by = actor_user if getattr(actor_user, "pk", None) else None
    complaint.resolved_at = timezone.now()
    complaint.save()
    if decision == Complaint.Status.REPRINT:
        request_reprint(complaint.order, Actor.OPERATOR, f"reklamacja uznana: {resolution}"[:500], actor_user)
    else:
        notifications.send_complaint_rejected_email(complaint.order, complaint)
