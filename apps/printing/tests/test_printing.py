"""
Sklep jako klient GH na plakacie demo: etykieta, koszyk, dodruk, anulowanie, panel. Ogólne
testy kontraktu v1 (body, błędy, polling, dosłanie linku) są w `contract/`.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.assets.services import create_asset_from_upload
from apps.catalog.models import DeliveryMethod
from apps.gh_mock import services as gh_services
from apps.gh_mock.models import GhOrder
from apps.orders import services as order_services
from apps.orders import shipping
from apps.orders.models import Actor, OrderItem, Shipment
from apps.orders.models import OrderStatus as S
from apps.orders.payments import MockPaymentProvider
from apps.poster.models import Format, PaperOption
from apps.poster.options import poster_options
from apps.poster.services import save_poster_draft
from apps.printing import links
from apps.printing.models import PrintJob
from apps.printing.providers import GhApiProvider
from apps.printing.services import poll_changes


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
    ).order
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
# Etykieta
# ---------------------------------------------------------------------------


class BrokenCarrier:
    name = "BrokenCarrier"

    def create_label(self, order):
        raise shipping.CarrierError("API kuriera nie odpowiada")

    def cancel_label(self, shipment):
        raise shipping.CarrierError("API kuriera nie odpowiada")


@pytest.mark.django_db
def test_gh_downloads_label_through_signed_link(approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get(order=approved_order)
    label_url = job.spec["label"]["url"]
    with job.shipment.label.open("rb") as fh:
        assert gh_services.fetch_url(label_url) == fh.read()

    gh_services.download_pending_files()
    poll_changes()
    job.refresh_from_db()
    assert {"kind": "label", "state": "downloaded"} in job.files_state
    assert job.status == PrintJob.Status.ACCEPTED

    shipping.cancel_shipment(job.shipment)  # anulowana etykieta nie wychodzi już do GH
    with pytest.raises(links.LinkError):
        gh_services.fetch_url(label_url)


@pytest.mark.django_db
def test_gh_mock_in_process_passes_public_server_security(approved_order, operator, settings):
    """Demo (`demo.py`): ALLOWED_HOSTS i przekierowanie na https nie blokują atrapy w procesie."""
    settings.SITE_URL = "https://sklep.example.pl"
    settings.ALLOWED_HOSTS = ["sklep.example.pl"]
    settings.SECURE_SSL_REDIRECT = True
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get(order=approved_order)
    assert _gh(job).items.count() == 1


@pytest.mark.django_db
def test_failed_submit_reuses_label_and_cancel_releases_it(approved_order, operator):
    GhApiProvider.fail_next_requests = 5
    order_services.approve_order(approved_order, actor_user=operator)
    approved_order.refresh_from_db()
    assert approved_order.status == S.ON_HOLD
    shipment = Shipment.objects.get(order=approved_order)

    order_services.approve_order(approved_order, actor_user=operator)  # drugie podejście
    approved_order.refresh_from_db()
    assert approved_order.status == S.SENT_TO_PRINTER
    assert Shipment.objects.filter(order=approved_order).count() == 1
    assert [j.shipment for j in PrintJob.objects.filter(order=approved_order)] == [shipment] * 2

    order_services.cancel_order(approved_order, Actor.OPERATOR, "klient rezygnuje", operator)
    shipment.refresh_from_db()
    assert shipment.status == Shipment.Status.CANCELLED


@pytest.mark.django_db
def test_carrier_failure_puts_order_on_hold_without_gh_order(approved_order, operator, settings):
    settings.CARRIER_PROVIDER = "apps.printing.tests.test_printing.BrokenCarrier"
    order_services.approve_order(approved_order, actor_user=operator)
    approved_order.refresh_from_db()
    assert approved_order.status == S.ON_HOLD
    assert approved_order.hold_reason.startswith("Przewoźnik nie wystawił etykiety")
    assert not PrintJob.objects.exists() and not GhOrder.objects.exists()


@pytest.mark.django_db
def test_failed_label_download_triggers_fresh_link(approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    job = PrintJob.objects.get(order=approved_order)
    gh_services.expire_file(_gh(job).files.get(kind="label"))

    assert poll_changes()["resent"] == 1
    job.refresh_from_db()
    assert {"kind": "label", "state": "pending"} in job.files_state
    assert "etykiety" in job.events.get(status="file_resend").note
    gh_services.download_pending_files()
    poll_changes()
    job.refresh_from_db()
    assert job.status == PrintJob.Status.ACCEPTED


@pytest.mark.django_db
def test_operator_downloads_label_from_panel(client, approved_order, operator):
    order_services.approve_order(approved_order, actor_user=operator)
    shipment = Shipment.objects.get(order=approved_order)
    url = reverse("ops:shipment_label", args=[approved_order.number, shipment.pk])
    assert client.get(url).status_code == 302  # bez logowania na stronę logowania

    client.force_login(operator)
    resp = client.get(url)
    assert resp.status_code == 200 and b"".join(resp.streaming_content).startswith(b"%PDF")
    html = client.get(reverse("ops:order_detail", args=[approved_order.number])).content.decode()
    assert shipment.tracking_number in html and "etykieta PDF" in html


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
    assert [(f["kind"], f["state"]) for f in job.files_state] == [
        ("item", "pending"),
        ("item", "pending"),
        ("label", "pending"),
    ]
    assert [i.quantity for i in gh_order.items.all()] == [1, 2]
    assert gh_order.total_value == approved_cart.print_total
    assert gh_order.items.last().product_code == "poster-a3-matte"


# ---------------------------------------------------------------------------
# Dodruk, anulowanie, panel
# ---------------------------------------------------------------------------


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
    assert jobs[1].shipment != jobs[0].shipment  # dodruk to nowa paczka z nową etykietą
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
    assert job.shipment.status == Shipment.Status.CANCELLED  # etykieta zwolniona u kuriera


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
