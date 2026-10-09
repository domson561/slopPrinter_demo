import pytest

from apps.assets.services import create_asset_from_upload
from apps.catalog.models import DeliveryMethod, Format, PaperOption
from apps.orders import services
from apps.orders.models import Order, OrderStatus
from apps.poster.services import save_poster_draft


@pytest.fixture
def make_draft(catalog_data, image_upload):
    def _make(name="test.png", size=(1448, 2048), fmt_code="A2", enhancer=True, quantity=1):
        asset = create_asset_from_upload(image_upload(name=name, size=size))
        fmt = Format.objects.get(code=fmt_code)
        paper = PaperOption.objects.get(code="matte")
        return save_poster_draft(
            asset,
            fmt,
            paper,
            white_margin=False,
            quantity=quantity,
            crop=None,
            enhancer_enabled=enhancer,
        )

    return _make


@pytest.fixture
def make_awaiting_payment(make_draft):
    def _make(**kwargs):
        order = make_draft(**kwargs)
        services.start_checkout(
            order,
            email="klient@example.com",
            full_name="Jan Testowy",
            phone="+48 600 000 000",
            address={
                "line1": "ul. Testowa 1",
                "postal_code": "00-001",
                "city": "Warszawa",
                "country": "PL",
            },
            delivery_method=DeliveryMethod.objects.get(code="courier"),
            consents={"terms": True, "no_withdrawal": True, "image_rights": True},
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.AWAITING_PAYMENT
        return order

    return _make


@pytest.fixture
def bare_order(db):
    """Zamówienie bez pozycji - do testów samej tabeli przejść."""

    def _make(status=OrderStatus.DRAFT):
        return Order.objects.create(status=status, email="x@example.com")

    return _make
