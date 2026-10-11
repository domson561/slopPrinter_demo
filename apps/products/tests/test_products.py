"""Interfejs produktu: rejestr, sprawdzanie pozycji w rdzeniu i rdzeń bez odwołań do plakatu."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.assets.services import create_asset_from_upload
from apps.orders import services as order_services
from apps.poster.models import Format, PosterSettings
from apps.poster.options import poster_options
from apps.poster.product import PosterProduct
from apps.products import ProductError, all_products, get_product


def test_registry_loads_products_from_settings():
    assert isinstance(get_product("poster"), PosterProduct)
    assert [p.code for p in all_products()] == ["poster"]
    with pytest.raises(ProductError):
        get_product("mug")


def test_registry_rejects_duplicate_codes(settings):
    settings.SHOP_PRODUCTS = ["apps.poster.product.PosterProduct"] * 2
    with pytest.raises(ImproperlyConfigured):
        all_products()


@pytest.mark.django_db
def test_core_checks_file_type_and_options_through_product(catalog_data, image_upload, monkeypatch):
    asset = create_asset_from_upload(image_upload(size=(1448, 2048)))
    options = poster_options(Format.objects.get(code="A3"))
    order = order_services.save_draft("poster", asset, options, quantity=2).order
    item = order.item
    assert item.product == "poster" and item.options["crop"]["orientation"] == "portrait"
    assert order.subtotal == item.line_total and item.summary.startswith("Plakat A3")

    with pytest.raises(ProductError):
        order_services.save_draft("poster", asset, {"format": "XXL"}, quantity=1)
    monkeypatch.setattr(get_product("poster"), "accepted_mime", ("application/pdf",))
    with pytest.raises(ProductError):
        order_services.save_draft("poster", asset, options, quantity=1)


# Rdzeń zna produkt tylko przez interfejs: żadnych importów plakatu ani pól pozycji plakatu.
CORE_CODE = ["apps/orders", "apps/printing", "apps/ops"]
CORE_TEMPLATES = ["templates/ops", "templates/components", "templates/emails"]
FORBIDDEN_CODE = re.compile(
    r"apps\.poster|apps\.assets\.(services|quality)|apps\.catalog\.services|"
    r"\bFormat\b|PaperOption|plakat",
    re.IGNORECASE,
)
FORBIDDEN_TEMPLATE = re.compile(
    r"item\.(format|paper|crop|white_margin|enhancer|orientation)|plakat", re.IGNORECASE
)


def _sources(dirs, suffixes):
    for d in dirs:
        for path in sorted((Path(settings.BASE_DIR) / d).rglob("*")):
            if path.suffix in suffixes and not {"migrations", "tests"} & set(path.parts):
                yield path


def test_core_has_no_poster_references():
    hits = [
        f"{path.relative_to(settings.BASE_DIR)}:{n}: {line.strip()}"
        for dirs, suffixes, pattern in (
            (CORE_CODE, {".py"}, FORBIDDEN_CODE),
            (CORE_TEMPLATES, {".html", ".txt"}, FORBIDDEN_TEMPLATE),
        )
        for path in _sources(dirs, suffixes)
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert hits == []


def test_contract_suite_is_product_agnostic():
    """Testy zgodności z kontraktem uruchamia każdy sklep, więc nie znają plakatu ani jego kodów."""
    pattern = re.compile(r"apps\.poster|\bFormat\b|PaperOption|plakat|poster", re.IGNORECASE)
    suite = Path(settings.BASE_DIR) / "apps/printing/tests/contract"
    hits = [
        f"{path.name}:{n}: {line.strip()}"
        for path in sorted(suite.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert hits == []


@pytest.mark.django_db
def test_operator_settings_save_core_and_product_sections(client, catalog_data):
    from django.contrib.auth import get_user_model
    from django.urls import reverse

    from apps.catalog.models import ShopSettings
    from apps.poster.models import PaperOption

    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    client.force_login(user)
    data = {
        "auto_approve": "on",
        "lead_time_days": "9",
        "retention_days_after_close": "30",
        "draft_retention_days": "7",
        "dpi_sharp": "250",
        "dpi_ok": "130",
        "upscale_factor": "4",
    }
    for prefix, model, price_field in (
        ("formats", Format, "base_price"),
        ("papers", PaperOption, "price_delta"),
    ):
        rows = list(model.objects.order_by("sort_order", "code"))
        data |= {f"{prefix}-TOTAL_FORMS": len(rows), f"{prefix}-INITIAL_FORMS": len(rows)}
        for i, row in enumerate(rows):
            data |= {
                f"{prefix}-{i}-id": row.pk,
                f"{prefix}-{i}-code": row.code,
                f"{prefix}-{i}-name": row.name,
                f"{prefix}-{i}-{price_field}": getattr(row, price_field),
                f"{prefix}-{i}-is_active": "on",
            }
    data["formats-0-base_price"] = "99.00"

    resp = client.post(reverse("ops:settings"), data)
    assert resp.status_code == 302
    shop = ShopSettings.get()
    assert shop.auto_approve and shop.lead_time_days == 9
    poster = PosterSettings.get()
    assert poster.dpi_sharp == 250 and poster.dpi_ok == 130
    assert Format.objects.order_by("sort_order", "code").first().base_price == 99
