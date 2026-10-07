import pytest
from django.contrib.auth import get_user_model

from catalog.models import ShopSettings
from orders import services
from orders.models import OrderStatus as S, Payment
from orders.payments import MockPaymentProvider
from printing.models import MockPrinterJob, PrintJob


@pytest.mark.django_db
def test_successful_payment_runs_pipeline_to_awaiting_approval(make_awaiting_payment):
    order = make_awaiting_payment()
    payment = order.payments.get()
    result = MockPaymentProvider().process_callback(payment.external_id, "success")
    assert result.success and not result.already_processed
    order.refresh_from_db()
    assert order.status == S.AWAITING_APPROVAL
    statuses = [e.to_status for e in order.status_events.all()]
    assert statuses == [S.AWAITING_PAYMENT, S.PAID, S.PROCESSING, S.AWAITING_APPROVAL]
    assert order.item.print_file is not None
    assert order.item.asset.expires_at is None


@pytest.mark.django_db
def test_payment_callback_is_idempotent(make_awaiting_payment):
    order = make_awaiting_payment()
    payment = order.payments.get()
    MockPaymentProvider().process_callback(payment.external_id, "success")
    events_before = order.status_events.count()
    result = MockPaymentProvider().process_callback(payment.external_id, "success")
    assert result.already_processed
    order.refresh_from_db()
    assert order.status_events.count() == events_before
    assert order.status_events.filter(to_status=S.PAID).count() == 1


@pytest.mark.django_db
def test_failed_payment_keeps_awaiting_payment_and_allows_retry(make_awaiting_payment):
    order = make_awaiting_payment()
    payment = order.payments.get()
    result = MockPaymentProvider().process_callback(payment.external_id, "fail")
    assert not result.success
    order.refresh_from_db()
    payment.refresh_from_db()
    assert order.status == S.AWAITING_PAYMENT
    assert payment.status == Payment.Status.FAILED
    url = MockPaymentProvider().create(order)
    assert order.payments.count() == 2 and url.startswith("/payments/mock/")


@pytest.mark.django_db
def test_flagged_content_goes_on_hold_after_pipeline(make_awaiting_payment):
    order = make_awaiting_payment(name="my_logo.png")
    MockPaymentProvider().process_callback(order.payments.get().external_id, "success")
    order.refresh_from_db()
    assert order.status == S.ON_HOLD
    assert "znaku towarowego" in order.hold_reason
    assert order.item.print_file is not None  # plik przetworzony, operator widzi przed/po


@pytest.mark.django_db
def test_pipeline_error_goes_on_hold_with_technical_reason(make_awaiting_payment, monkeypatch):
    def boom(item):
        raise RuntimeError("upscale API timeout")

    monkeypatch.setattr(services, "run_pipeline", boom)
    order = make_awaiting_payment()
    MockPaymentProvider().process_callback(order.payments.get().external_id, "success")
    order.refresh_from_db()
    assert order.status == S.ON_HOLD
    assert "RuntimeError" in order.hold_reason and "timeout" in order.hold_reason


@pytest.mark.django_db
def test_reprocess_from_on_hold(make_awaiting_payment, monkeypatch):
    calls = {"n": 0}
    real = services.run_pipeline

    def flaky(item):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("chwilowy błąd")
        return real(item)

    monkeypatch.setattr(services, "run_pipeline", flaky)
    order = make_awaiting_payment()
    MockPaymentProvider().process_callback(order.payments.get().external_id, "success")
    order.refresh_from_db()
    assert order.status == S.ON_HOLD
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    services.reprocess_order(order, user)
    order.refresh_from_db()
    assert order.status == S.AWAITING_APPROVAL


@pytest.mark.django_db
def test_auto_approve_sends_to_printer(make_awaiting_payment):
    shop = ShopSettings.get()
    shop.auto_approve = True
    shop.save()
    order = make_awaiting_payment()
    MockPaymentProvider().process_callback(order.payments.get().external_id, "success")
    order.refresh_from_db()
    assert order.status == S.SENT_TO_PRINTER
    assert MockPrinterJob.objects.count() == 1


@pytest.mark.django_db
def test_operator_reject_refunds_and_cancels(make_awaiting_payment):
    order = make_awaiting_payment(name="brand_thing.png")
    MockPaymentProvider().process_callback(order.payments.get().external_id, "success")
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    order.refresh_from_db()
    services.reject_order(order, user, "cudze logo")
    order.refresh_from_db()
    assert order.status == S.CANCELLED
    assert order.payments.get().status == Payment.Status.REFUNDED
    assert order.item.asset.expires_at is not None


@pytest.mark.django_db
def test_reprint_and_complaint_flow(make_awaiting_payment):
    from printing.services import handle_printer_event

    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    order = make_awaiting_payment()
    MockPaymentProvider().process_callback(order.payments.get().external_id, "success")
    order.refresh_from_db()
    services.approve_order(order, actor_user=user)
    job = PrintJob.objects.get(order_item__order=order)
    handle_printer_event({"external_id": job.external_id, "status": "printed"})
    handle_printer_event({"external_id": job.external_id, "status": "shipped", "tracking_number": "MK123"})
    order.refresh_from_db()
    assert order.status == S.SHIPPED and order.latest_shipment.tracking_number == "MK123"
    services.mark_delivered(order)
    complaint = services.file_complaint(order, "zagięty róg")
    assert order.needs_attention
    services.resolve_complaint(complaint, user, "reprint", "uznana, wysyłamy nowy")
    order.refresh_from_db()
    assert order.status == S.SENT_TO_PRINTER
    jobs = list(PrintJob.objects.filter(order_item__order=order).order_by("created_at"))
    assert len(jobs) == 2 and jobs[1].reprint_of == jobs[0]
    assert MockPrinterJob.objects.count() == 2
