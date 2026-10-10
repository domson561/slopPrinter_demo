"""Szkieletowy produkt przechodzi ścieżkę rdzenia: szkic, płatność, PDF, zatwierdzenie, GH, panel."""

import io
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from reportlab.pdfgen import canvas

from apps.assets.models import Asset
from apps.assets.services import create_asset_from_upload
from apps.catalog.models import DeliveryMethod
from apps.gh_mock import services as gh_services
from apps.gh_mock.models import GhOrder
from apps.orders import services as order_services
from apps.orders.models import OrderItem
from apps.orders.models import OrderStatus as S
from apps.orders.payments import MockPaymentProvider
from apps.poster.models import Format, PaperOption
from apps.poster.services import save_poster_draft
from apps.printing.services import poll_changes
from apps.products import ProductError
from apps.ready_pdf.product import ReadyPdfProduct


def _pdf() -> bytes:
    buf = io.BytesIO()
    page = canvas.Canvas(buf)
    page.drawString(100, 750, "gotowy plik od klienta")
    page.showPage()
    page.save()
    return buf.getvalue()


PDF = _pdf()


@pytest.fixture
def ready_pdf(settings, catalog_data):
    settings.SHOP_PRODUCTS = [*settings.SHOP_PRODUCTS, "apps.ready_pdf.product.ReadyPdfProduct"]


@pytest.fixture
def operator(db):
    return get_user_model().objects.create_user("op", password="x", is_staff=True)


def make_pdf_asset() -> Asset:
    """Wgranie przez bramkę plików, z typami przyjmowanymi przez produkt."""
    return create_asset_from_upload(
        SimpleUploadedFile("gotowy.pdf", PDF, content_type="application/pdf"),
        allowed_mime=ReadyPdfProduct.accepted_mime,
    )


def pay(order):
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


@pytest.mark.django_db
def test_ready_pdf_goes_through_core_to_gh(ready_pdf, operator, client):
    order = order_services.save_draft("ready_pdf", make_pdf_asset(), {}, quantity=2).order
    assert order.subtotal == Decimal("50.00") and order.item.summary == "Gotowy PDF od klienta"

    pay(order)
    assert order.status == S.AWAITING_APPROVAL
    [pdf] = order.item.print_files
    with pdf.file.open("rb") as fh:
        assert fh.read() == PDF

    order_services.approve_order(order, actor_user=operator)
    order.refresh_from_db()
    assert order.status == S.SENT_TO_PRINTER
    gh_order = GhOrder.objects.get(shop_order_number=order.number)
    [line] = gh_order.items.all()
    assert (line.product_code, line.name, line.quantity) == (
        "ready-pdf",
        "Gotowy PDF od klienta",
        2,
    )
    assert gh_order.needs_review  # kod spoza słownika atrapy: „do weryfikacji”

    gh_services.download_pending_files()
    assert gh_order.files.get(kind="item").state == "downloaded"
    gh_services.set_status(gh_order, "in_progress")
    gh_services.set_status(gh_order, "ready")
    poll_changes()
    order.refresh_from_db()
    assert order.status == S.PRINTED

    client.force_login(operator)
    assert client.get(reverse("ops:order_detail", args=[order.number])).status_code == 200
    resp = client.get(reverse("ops:order_print_file", args=[order.number, order.item.pk]))
    assert b"".join(resp.streaming_content) == PDF


@pytest.mark.django_db
def test_mixed_cart_is_one_gh_order_with_lines_from_each_product(ready_pdf, operator, image_upload):
    order = save_poster_draft(
        create_asset_from_upload(image_upload(size=(1448, 2048))),
        Format.objects.get(code="A2"),
        PaperOption.objects.get(code="matte"),
        white_margin=False,
        quantity=1,
        crop=None,
        enhancer_enabled=False,
    ).order
    OrderItem.objects.create(order=order, product="ready_pdf", asset=make_pdf_asset(), quantity=3)
    order_services.recalc_totals(order)
    pay(order)
    order_services.approve_order(order, actor_user=operator)

    gh_order = GhOrder.objects.get(shop_order_number=order.number)
    lines = [(i.position, i.product_code, i.quantity) for i in gh_order.items.all()]
    assert lines == [(1, "poster-a2-matte", 1), (2, "ready-pdf", 3)]
    assert gh_order.total_value == order.subtotal


@pytest.mark.django_db
def test_ready_pdf_rejects_images_and_options(ready_pdf, image_upload):
    image = create_asset_from_upload(image_upload())
    with pytest.raises(ProductError):
        order_services.save_draft("ready_pdf", image, {}, quantity=1)
    with pytest.raises(ProductError):
        order_services.save_draft("ready_pdf", make_pdf_asset(), {"format": "A2"}, quantity=1)
