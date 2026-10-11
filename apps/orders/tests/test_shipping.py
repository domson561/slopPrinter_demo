"""Przesyłka po stronie sklepu: atrapa kuriera, etykieta PDF w prywatnym katalogu, stany przesyłki."""

from pathlib import Path

import pytest
from pypdf import PdfReader

from apps.catalog.models import DeliveryMethod
from apps.orders import shipping
from apps.orders.models import Order, OrderStatus, Shipment


@pytest.fixture
def addressed_order(catalog_data):
    return Order.objects.create(
        status=OrderStatus.AWAITING_APPROVAL,
        email="k@example.com",
        full_name="Jan Łącki",
        phone="+48 600 100 200",
        shipping_address={
            "line1": "ul. Źródlana 5",
            "postal_code": "90-001",
            "city": "Łódź",
            "country": "PL",
        },
        delivery_method=DeliveryMethod.objects.get(code="parcel_locker"),
    )


def _label_text(shipment) -> str:
    with shipment.label.open("rb") as fh:
        return PdfReader(fh).pages[0].extract_text()


@pytest.mark.django_db
def test_mock_carrier_makes_tracking_number_and_private_pdf_label(addressed_order, settings):
    shipment = shipping.create_shipment(addressed_order)
    assert shipment.status == Shipment.Status.CREATED and shipment.is_mock
    assert shipment.tracking_number.startswith("MOCK") and shipment.external_id
    assert shipment.carrier == shipping.MockCarrierProvider.carrier

    path = Path(shipment.label.path)
    assert path.is_relative_to(Path(settings.PRIVATE_MEDIA_ROOT))
    assert path.read_bytes().startswith(b"%PDF")
    with pytest.raises(ValueError):
        shipment.label.url  # noqa: B018 - prywatny plik nie ma publicznego adresu

    text = _label_text(shipment)
    for part in (shipment.tracking_number, "90-001", "+48 600 100 200", addressed_order.number):
        assert part in text
    if shipping._label_font():  # czcionka z polskimi znakami jest w systemie
        assert "Łódź" in text and "Jan Łącki" in text and "Paczkomat" in text


@pytest.mark.django_db
def test_label_needs_recipient_and_a_real_pdf(addressed_order):
    class NotPdfCarrier(shipping.MockCarrierProvider):
        def create_label(self, order):
            return shipping.LabelResult("X", "X1", "x1", b"<html>")

    with pytest.raises(shipping.CarrierError):
        shipping.create_shipment(addressed_order, provider=NotPdfCarrier())
    addressed_order.full_name = ""
    with pytest.raises(shipping.CarrierError):
        shipping.create_shipment(addressed_order)
    assert Shipment.objects.count() == 0


@pytest.mark.django_db
def test_cancel_and_mark_shipped_are_idempotent(addressed_order):
    unused = shipping.create_shipment(addressed_order)
    shipping.cancel_shipment(unused)
    shipping.cancel_shipment(unused)
    assert unused.status == Shipment.Status.CANCELLED and unused.cancelled_at

    sent = shipping.create_shipment(addressed_order)
    assert addressed_order.latest_shipment is None  # etykieta gotowa to jeszcze nie nadanie
    shipping.mark_shipped(sent)
    first = sent.shipped_at
    shipping.mark_shipped(sent)
    shipping.cancel_shipment(sent)  # nadanej paczki się nie anuluje
    sent.refresh_from_db()
    assert sent.status == Shipment.Status.SHIPPED and sent.shipped_at == first
    assert addressed_order.latest_shipment == sent
