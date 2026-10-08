from decimal import Decimal

import pytest

from apps.catalog.models import DeliveryMethod, Format, PaperOption
from apps.catalog.services import quote


@pytest.mark.django_db
def test_quote_sums_format_paper_quantity_and_delivery():
    fmt = Format.objects.create(
        code="A2", name="A2", width_mm=420, height_mm=594, base_price="89.00"
    )
    paper = PaperOption.objects.create(code="satin", name="Satynowy", price_delta="10.00")
    delivery = DeliveryMethod.objects.create(code="courier", name="Kurier", price="19.00")

    q = quote(fmt, paper, quantity=2, delivery=delivery)

    assert q.unit_price == Decimal("99.00")
    assert q.subtotal == Decimal("198.00")
    assert q.delivery_price == Decimal("19.00")
    assert q.total == Decimal("217.00")


@pytest.mark.django_db
def test_quote_quantity_minimum_is_one():
    fmt = Format.objects.create(
        code="A3", name="A3", width_mm=297, height_mm=420, base_price="59.00"
    )
    q = quote(fmt, None, quantity=0)
    assert q.quantity == 1
    assert q.total == Decimal("59.00")
