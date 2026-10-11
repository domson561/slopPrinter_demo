"""
Testy zgodności sklepu z kontraktem v1 (`docs/kontrakt-gh-v1.md`) na atrapie GH. Nie znają
produktu: koszyk to próbki (`Product.contract_sample`) wszystkich produktów z `SHOP_PRODUCTS`,
dane produktów z ich `Product.fixtures`. Każdy sklep na rdzeniu uruchamia je bez zmian:

    .venv\\Scripts\\python -m pytest apps/printing/tests/contract
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from apps.assets.services import create_asset_from_upload
from apps.catalog.models import DeliveryMethod
from apps.gh_mock.services import ensure_demo_shop
from apps.orders import services as order_services
from apps.orders.models import OrderStatus
from apps.orders.payments import MockPaymentProvider
from apps.printing.models import PrintJob
from apps.printing.providers import GhApiProvider
from apps.products import all_products

from .helpers import CUSTOMER, QUANTITY


@pytest.fixture(autouse=True)
def _contract_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "private_media"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.PRINT_SUBMIT_RETRY_BACKOFF_SECONDS = 0
    GhApiProvider.fail_next_requests = 0
    yield
    GhApiProvider.fail_next_requests = 0


@pytest.fixture
def contract_data(db):
    """Ustawienia rdzenia, dane produktów i sklep w atrapie GH (token z `GH_API_TOKEN`)."""
    fixtures = [name for product in all_products() for name in product.fixtures]
    call_command("loaddata", "catalog.json", *fixtures, verbosity=0)
    ensure_demo_shop()


@pytest.fixture
def operator(db):
    return get_user_model().objects.create_user("operator", password="x", is_staff=True)


@pytest.fixture
def contract_order(contract_data):
    """Koszyk z jedną pozycją na produkt, po płatności i przygotowaniu plików do druku."""
    order = None
    for product in all_products():
        upload, options = product.contract_sample()
        asset = create_asset_from_upload(upload, allowed_mime=product.accepted_mime)
        item = order_services.save_draft(
            product.code, asset, options, quantity=QUANTITY, order=order
        )
        order = item.order
    order_services.start_checkout(
        order,
        email=CUSTOMER["email"],
        full_name=CUSTOMER["full_name"],
        phone=CUSTOMER["phone"],
        address=CUSTOMER["address"],
        delivery_method=DeliveryMethod.objects.filter(is_active=True).first(),
        consents={"terms": True},
    )
    MockPaymentProvider().process_callback(order.payments.get().external_id, "success")
    order.refresh_from_db()
    assert order.status == OrderStatus.AWAITING_APPROVAL, order.hold_reason
    return order


@pytest.fixture
def sent_job(contract_order, operator) -> PrintJob:
    """Zamówienie zatwierdzone przez operatora i przyjęte przez atrapę GH."""
    order_services.approve_order(contract_order, actor_user=operator)
    contract_order.refresh_from_db()
    assert contract_order.status == OrderStatus.SENT_TO_PRINTER, contract_order.hold_reason
    return PrintJob.objects.get(order=contract_order)
