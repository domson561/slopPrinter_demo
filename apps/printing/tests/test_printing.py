import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.assets.services import create_asset_from_upload
from apps.catalog.models import DeliveryMethod, Format, PaperOption
from apps.orders import services as order_services
from apps.orders.models import OrderStatus as S
from apps.orders.payments import MockPaymentProvider
from apps.printing.models import MockPrinterJob, PrintJob
from apps.printing.providers import MockPrintProvider
from apps.printing.services import PrintSubmitError, build_spec, create_print_job, sign_payload, submit_print_job


@pytest.fixture
def approved_order(catalog_data, image_upload):
    """Zamówienie po płatności i pipeline, w awaiting_approval."""
    asset = create_asset_from_upload(image_upload(size=(1448, 2048)))
    order = order_services.save_draft(
        asset, Format.objects.get(code="A2"), PaperOption.objects.get(code="matte"), white_margin=False, quantity=1, crop=None, enhancer_enabled=False
    )
    order_services.start_checkout(
        order,
        email="k@example.com",
        full_name="Jan Kowalski",
        phone="+48 600",
        address={"line1": "Długa 1", "postal_code": "00-001", "city": "Warszawa", "country": "PL"},
        delivery_method=DeliveryMethod.objects.get(code="courier"),
        consents={"terms": True},
    )
    MockPaymentProvider().process_callback(order.payments.get().external_id, "success")
    order.refresh_from_db()
    assert order.status == S.AWAITING_APPROVAL
    return order


@pytest.fixture
def operator(db):
    return get_user_model().objects.create_user("op", password="x", is_staff=True)


@pytest.mark.django_db
def test_spec_matches_contract(approved_order):
    job = create_print_job(approved_order.item)
    spec = build_spec(job)
    assert spec["product"] == "poster"
    assert spec["order_number"] == approved_order.number
    assert spec["format"] == {"code": "A2", "width_mm": 420, "height_mm": 594, "bleed_mm": 3, "orientation": "portrait"}
    assert spec["paper"] == "matte" and spec["quantity"] == 1 and spec["white_margin"] is False
    assert spec["file"]["url"].endswith(".pdf") and len(spec["file"]["sha256"]) == 64
    assert spec["shipping"]["address"]["city"] == "Warszawa" and spec["shipping"]["method"] == "courier"
    assert spec["reprint_of"] is None


@pytest.mark.django_db
def test_submit_is_idempotent(approved_order):
    job = create_print_job(approved_order.item)
    provider = MockPrintProvider()
    first = provider.submit(job)
    second = provider.submit(job)
    assert first == second
    assert MockPrinterJob.objects.count() == 1


@pytest.mark.django_db
def test_submit_retries_with_backoff_then_succeeds(approved_order):
    MockPrintProvider.fail_next_submits = 2
    job = create_print_job(approved_order.item)
    submit_print_job(job)
    assert job.status == PrintJob.Status.SENT and job.attempts == 3 and job.external_id


@pytest.mark.django_db
def test_submit_gives_up_after_max_attempts_and_order_goes_on_hold(approved_order, operator):
    MockPrintProvider.fail_next_submits = 5
    order_services.approve_order(approved_order, actor_user=operator)
    approved_order.refresh_from_db()
    assert approved_order.status == S.ON_HOLD
    job = PrintJob.objects.get(order_item__order=approved_order)
    assert job.status == PrintJob.Status.FAILED and job.attempts == 5
    with pytest.raises(PrintSubmitError):
        MockPrintProvider.fail_next_submits = 5
        submit_print_job(create_print_job(approved_order.item))


@pytest.mark.django_db
def test_approve_sends_to_printer_and_creates_mock_job(approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    approved_order.refresh_from_db()
    assert approved_order.status == S.SENT_TO_PRINTER
    mock_job = MockPrinterJob.objects.get()
    assert mock_job.spec["order_number"] == approved_order.number
    assert mock_job.status == MockPrinterJob.Status.RECEIVED


def _post_event(client, payload, secret_ok=True):
    body = json.dumps(payload).encode()
    sig = sign_payload(body) if secret_ok else "deadbeef"
    return client.post(reverse("printing:webhook"), data=body, content_type="application/json", headers={"X-Printer-Signature": sig})


@pytest.mark.django_db
def test_webhook_rejects_bad_signature(client, approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get()
    resp = _post_event(client, {"external_id": job.external_id, "status": "printed"}, secret_ok=False)
    assert resp.status_code == 403
    resp = client.post(reverse("printing:webhook"), data=b"{}", content_type="application/json")
    assert resp.status_code == 403
    approved_order.refresh_from_db()
    assert approved_order.status == S.SENT_TO_PRINTER


@pytest.mark.django_db
def test_webhook_flow_and_idempotency(client, approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get()
    ext = job.external_id

    resp = _post_event(client, {"external_id": ext, "status": "accepted"})
    assert resp.status_code == 200 and resp.json()["applied"] is True
    resp = _post_event(client, {"external_id": ext, "status": "accepted"})
    assert resp.json()["applied"] is False  # powtórka

    _post_event(client, {"external_id": ext, "status": "printed"})
    approved_order.refresh_from_db()
    assert approved_order.status == S.PRINTED

    resp = _post_event(client, {"external_id": ext, "status": "shipped", "tracking_number": "MK1"})
    approved_order.refresh_from_db()
    assert approved_order.status == S.SHIPPED and approved_order.latest_shipment.tracking_number == "MK1"

    resp = _post_event(client, {"external_id": ext, "status": "accepted"})  # wstecz
    assert resp.json()["applied"] is False
    assert job.events.count() == 6  # sent + 5 zdarzeń webhooka

    resp = _post_event(client, {"external_id": "MOCK-NOPE", "status": "printed"})
    assert resp.status_code == 404
    resp = client.post(reverse("printing:webhook"), data=b"not json", content_type="application/json", headers={"X-Printer-Signature": sign_payload(b"not json")})
    assert resp.status_code == 400


@pytest.mark.django_db
def test_webhook_rejected_puts_order_on_hold(client, approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get()
    _post_event(client, {"external_id": job.external_id, "status": "rejected", "reason": "uszkodzony PDF"})
    approved_order.refresh_from_db()
    assert approved_order.status == S.ON_HOLD and "uszkodzony PDF" in approved_order.hold_reason
    job.refresh_from_db()
    assert job.status == PrintJob.Status.REJECTED


@pytest.mark.django_db
def test_webhook_defect_triggers_reprint(client, approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get()
    _post_event(client, {"external_id": job.external_id, "status": "printed"})
    _post_event(client, {"external_id": job.external_id, "status": "defect", "reason": "smuga"})
    approved_order.refresh_from_db()
    assert approved_order.status == S.SENT_TO_PRINTER
    jobs = list(PrintJob.objects.order_by("created_at"))
    assert len(jobs) == 2 and jobs[1].reprint_of == jobs[0] and jobs[0].status == PrintJob.Status.FAILED
    assert MockPrinterJob.objects.get(external_id=jobs[1].external_id).spec["reprint_of"] == jobs[0].job_id


@pytest.mark.django_db
def test_mock_printer_dashboard_action_calls_webhook_over_http(client, approved_order, operator, monkeypatch):
    """D1 woła webhook przez requests.post; tu przekierowujemy to na klienta testowego."""
    import requests

    order_services.approve_order(approved_order, actor_user=operator)
    mock_job = MockPrinterJob.objects.get()
    captured = {}

    class FakeResponse:
        def __init__(self, resp):
            self.status_code = resp.status_code
            self._resp = resp
            self.text = resp.content.decode()

        def json(self):
            return self._resp.json()

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse(client.post(reverse("printing:webhook"), data=data, content_type="application/json", headers={"X-Printer-Signature": headers["X-Printer-Signature"]}))

    monkeypatch.setattr(requests, "post", fake_post)
    resp = client.post(reverse("printing:mock_action", args=[mock_job.external_id]), {"action": "accept"})
    assert resp.status_code == 302
    assert captured["url"].endswith("/api/printer/events/")
    mock_job.refresh_from_db()
    assert mock_job.status == MockPrinterJob.Status.ACCEPTED
    assert mock_job.history[-1]["http_status"] == 200
    PrintJob.objects.get().refresh_from_db()
    assert PrintJob.objects.get().status == PrintJob.Status.ACCEPTED

    resp = client.get(reverse("printing:mock_dashboard"))
    assert resp.status_code == 200 and mock_job.external_id in resp.content.decode()
    resp = client.get(reverse("printing:mock_job", args=[mock_job.external_id]))
    assert resp.status_code == 200
