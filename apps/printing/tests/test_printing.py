"""Sklep jako klient kontraktu v1: body POST-a, wysyłka z retry, polling i mapowanie statusów."""

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.assets.services import create_asset_from_upload
from apps.catalog.models import DeliveryMethod
from apps.gh_mock import services as gh_services
from apps.gh_mock.models import GhOrder, GhOrderFile
from apps.orders import services as order_services
from apps.orders.models import Actor, OrderItem
from apps.orders.models import OrderStatus as S
from apps.orders.payments import MockPaymentProvider
from apps.poster.models import Format, PaperOption
from apps.poster.options import poster_options
from apps.poster.services import save_poster_draft
from apps.printing import services as print_services
from apps.printing.models import PollCursor, PrintJob
from apps.printing.providers import GhApiProvider
from apps.printing.services import (
    PrintSubmitError,
    build_request,
    create_print_job,
    poll_changes,
    submit_print_job,
)


def _approve_ready_order(image_upload, extra_items=0):
    """Zamówienie po płatności i pipeline, w awaiting_approval. `extra_items` dokłada pozycje A3."""
    asset = create_asset_from_upload(image_upload(size=(1448, 2048)))
    order = save_poster_draft(
        asset,
        Format.objects.get(code="A2"),
        PaperOption.objects.get(code="matte"),
        white_margin=False,
        quantity=1,
        crop=None,
        enhancer_enabled=False,
    )
    for _ in range(extra_items):
        OrderItem.objects.create(
            order=order,
            asset=create_asset_from_upload(image_upload(size=(1448, 2048))),
            product="poster",
            options=poster_options(
                Format.objects.get(code="A3"),
                PaperOption.objects.get(code="matte"),
                white_margin=True,
            ),
            quantity=2,
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
def approved_order(catalog_data, image_upload):
    return _approve_ready_order(image_upload)


@pytest.fixture
def approved_cart(catalog_data, image_upload):
    """Zamówienie z dwiema pozycjami (A2 ×1, A3 ×2) w awaiting_approval."""
    return _approve_ready_order(image_upload, extra_items=1)


@pytest.fixture
def operator(db):
    return get_user_model().objects.create_user("op", password="x", is_staff=True)


def _gh(job) -> GhOrder:
    return GhOrder.objects.get(shop_order_number=job.shop_order_number)


def _gh_advance(job, *statuses):
    """Biuro GH: pobiera pliki i przesuwa zlecenie przez podane statusy."""
    gh_order = _gh(job)
    gh_services.download_pending_files()
    for status in statuses:
        gh_services.set_status(gh_order, status)
    return gh_order


# ---------------------------------------------------------------------------
# Body żądania
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_request_matches_contract(approved_order):
    job = create_print_job(approved_order)
    body = build_request(job)
    assert body["shop_order_number"] == approved_order.number == job.shop_order_number
    assert body["title"] == f"Zamówienie {approved_order.number}"
    assert body["currency"] == "PLN" and body["total_value"] == str(approved_order.subtotal)
    assert "label" not in body and "note_for_production" not in body
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["position"] == 1 and item["product_code"] == "poster-a2-matte"
    assert item["name"].startswith("Plakat A2") and item["quantity"] == 1
    assert item["unit_price"] == str(approved_order.item.unit_price)
    assert set(item["file"]) == {"url", "sha256", "size", "expires_at"}
    assert item["file"]["url"].endswith(".pdf") and len(item["file"]["sha256"]) == 64
    assert item["file"]["size"] > 0
    assert set(item) == {"position", "name", "product_code", "quantity", "unit_price", "file"}


@pytest.mark.django_db
def test_request_has_no_customer_data(approved_order):
    dump = json.dumps(build_request(create_print_job(approved_order)), ensure_ascii=False)
    for secret in ("Jan Kowalski", "k@example.com", "+48 600", "Długa 1", "Warszawa", "00-001"):
        assert secret not in dump
    assert "address" not in dump and "shipping" not in dump


# ---------------------------------------------------------------------------
# Wysyłka
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cart_is_one_order_in_gh(approved_cart, operator):
    assert approved_cart.items.count() == 2
    order_services.approve_order(approved_cart, actor_user=operator)
    approved_cart.refresh_from_db()
    assert approved_cart.status == S.SENT_TO_PRINTER
    job = PrintJob.objects.get(order=approved_cart)
    gh_order = _gh(job)
    assert GhOrder.objects.count() == 1 and gh_order.items.count() == 2
    assert job.external_id == gh_order.gh_number and job.provider_status == "new"
    assert job.status == PrintJob.Status.SENT  # pliki jeszcze nie pobrane
    assert [f["state"] for f in job.files_state] == ["pending", "pending"]
    assert [i.quantity for i in gh_order.items.all()] == [1, 2]
    assert gh_order.items.last().product_code == "poster-a3-matte"


@pytest.mark.django_db
def test_submit_is_idempotent(approved_order):
    job = create_print_job(approved_order)
    provider = GhApiProvider()
    first = provider.submit(job.spec)
    second = provider.submit(job.spec)
    assert first.gh_number == second.gh_number
    assert GhOrder.objects.count() == 1


@pytest.mark.django_db
def test_submit_retries_with_backoff_then_succeeds(approved_order):
    GhApiProvider.fail_next_requests = 2
    job = create_print_job(approved_order)
    submit_print_job(job)
    assert job.status == PrintJob.Status.SENT and job.attempts == 3 and job.external_id


@pytest.mark.django_db
def test_submit_gives_up_after_max_attempts_and_order_goes_on_hold(approved_order, operator):
    GhApiProvider.fail_next_requests = 5
    order_services.approve_order(approved_order, actor_user=operator)
    approved_order.refresh_from_db()
    assert approved_order.status == S.ON_HOLD
    job = PrintJob.objects.get(order=approved_order)
    assert job.status == PrintJob.Status.FAILED and job.attempts == 5
    with pytest.raises(PrintSubmitError):
        GhApiProvider.fail_next_requests = 5
        submit_print_job(create_print_job(approved_order))


@pytest.mark.django_db
def test_rejected_request_is_not_retried(approved_order, operator, monkeypatch):
    original = print_services.build_request

    def leaky(job):
        return {**original(job), "email": "k@example.com"}

    monkeypatch.setattr(print_services, "build_request", leaky)
    order_services.approve_order(approved_order, actor_user=operator)
    approved_order.refresh_from_db()
    job = PrintJob.objects.get(order=approved_order)
    assert approved_order.status == S.ON_HOLD and "400" in approved_order.hold_reason
    assert job.status == PrintJob.Status.FAILED and job.attempts == 1
    assert GhOrder.objects.count() == 0


@pytest.mark.django_db
def test_auth_error_stops_submit_and_polling(approved_order, operator, settings):
    settings.GH_API_TOKEN = "cofniety"
    order_services.approve_order(approved_order, actor_user=operator)
    approved_order.refresh_from_db()
    assert approved_order.status == S.ON_HOLD and "token" in approved_order.hold_reason
    assert PollCursor.get().auth_failed is True
    assert "wstrzymany" in poll_changes()["error"]

    settings.GH_API_TOKEN = "demo-shop-token"
    print_services.reset_poll_auth()
    assert poll_changes()["error"] == ""
    assert PollCursor.get().since is not None


# ---------------------------------------------------------------------------
# Polling i statusy
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_polling_maps_gh_statuses_to_order(approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get(order=approved_order)
    gh_order = _gh(job)

    assert poll_changes()["changed"] == 0  # nic nowego od POST-a
    gh_services.download_pending_files()
    assert poll_changes()["changed"] == 1
    job.refresh_from_db()
    assert job.status == PrintJob.Status.ACCEPTED and job.all_files_downloaded
    approved_order.refresh_from_db()
    assert approved_order.status == S.SENT_TO_PRINTER

    gh_services.set_status(gh_order, "in_progress")
    poll_changes()
    job.refresh_from_db()
    assert job.status == PrintJob.Status.ACCEPTED and job.provider_status == "in_progress"

    gh_services.set_status(gh_order, "ready")
    poll_changes()
    job.refresh_from_db()
    approved_order.refresh_from_db()
    assert job.status == PrintJob.Status.PRINTED and approved_order.status == S.PRINTED

    gh_services.set_status(gh_order, "shipped")
    poll_changes()
    job.refresh_from_db()
    approved_order.refresh_from_db()
    assert job.status == PrintJob.Status.SHIPPED and approved_order.status == S.SHIPPED
    assert approved_order.latest_shipment.carrier == "Graphic House"

    before = job.events.count()
    summary = poll_changes()
    assert summary["orders"] == 0 and summary["changed"] == 0
    assert job.events.count() == before
    assert PollCursor.get().since is not None


@pytest.mark.django_db
def test_shipped_without_ready_implies_printed(approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get(order=approved_order)
    _gh_advance(job, "in_progress", "ready", "shipped")
    assert poll_changes()["changed"] == 1
    approved_order.refresh_from_db()
    assert approved_order.status == S.SHIPPED
    statuses = list(approved_order.status_events.values_list("to_status", flat=True))
    assert statuses[-2:] == [S.PRINTED, S.SHIPPED]


@pytest.mark.django_db
def test_failed_file_triggers_fresh_link(approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get(order=approved_order)
    gh_file = _gh(job).files.get()
    gh_services.expire_file(gh_file)
    old_expiry = gh_file.expires_at

    summary = poll_changes()
    assert summary["resent"] == 1 and summary["changed"] == 0
    gh_file.refresh_from_db()
    assert gh_file.state == GhOrderFile.State.PENDING and gh_file.expires_at >= old_expiry
    job.refresh_from_db()
    assert job.status == PrintJob.Status.SENT
    assert job.files_state == [{"kind": "item", "position": 1, "state": "pending"}]
    assert job.events.filter(status="file_resend", applied=True).count() == 1

    gh_services.download_pending_files()
    poll_changes()
    job.refresh_from_db()
    assert job.status == PrintJob.Status.ACCEPTED


@pytest.mark.django_db
def test_reprint_gets_new_shop_order_number(approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get(order=approved_order)
    _gh_advance(job, "in_progress", "ready")
    poll_changes()
    approved_order.refresh_from_db()
    assert approved_order.status == S.PRINTED

    order_services.request_reprint(approved_order, Actor.OPERATOR, "smuga", operator)
    approved_order.refresh_from_db()
    jobs = list(PrintJob.objects.filter(order=approved_order).order_by("created_at"))
    assert len(jobs) == 2 and jobs[1].reprint_of == jobs[0]
    assert jobs[1].shop_order_number == f"{approved_order.number}-D1"
    assert GhOrder.objects.count() == 2
    assert "Dodruk" in _gh(jobs[1]).note_for_production
    assert approved_order.status == S.SENT_TO_PRINTER


@pytest.mark.django_db
def test_cancel_after_send_is_local_only(approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get(order=approved_order)
    order_services.cancel_order(approved_order, Actor.OPERATOR, "klient rezygnuje", operator)
    job.refresh_from_db()
    approved_order.refresh_from_db()
    assert approved_order.status == S.CANCELLED and job.status == PrintJob.Status.CANCELLED
    assert "biurem" in job.events.last().note
    assert _gh(job).status == "new"  # GH nie zna anulowania


@pytest.mark.django_db
def test_ops_poll_button_and_job_table(client, approved_order, operator):
    client.force_login(operator)
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get(order=approved_order)
    _gh_advance(job, "in_progress")

    resp = client.post(reverse("ops:poll_gh"), {"next": f"/ops/orders/{approved_order.number}/"})
    assert resp.status_code == 302 and resp.url.endswith(f"/ops/orders/{approved_order.number}/")
    resp = client.get(resp.url)
    html = resp.content.decode()
    assert "Zlecenia w Graphic House" in html and "GH: in_progress" in html
    assert "item 1: downloaded" in html
    job.refresh_from_db()
    assert job.status == PrintJob.Status.ACCEPTED
