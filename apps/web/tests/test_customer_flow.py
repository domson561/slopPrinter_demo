"""Pełna ścieżka klienta S1 -> S7 przez klienta testowego, potem operator i atrapa GH."""

import json

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse

from apps.assets.models import Asset
from apps.catalog.models import DeliveryMethod
from apps.gh_mock import services as gh_services
from apps.gh_mock.models import GhOrder
from apps.orders.models import Order
from apps.orders.models import OrderStatus as S
from apps.poster.models import Format, PaperOption
from apps.printing.models import PrintJob
from apps.printing.services import poll_changes


@pytest.mark.django_db
def test_full_customer_path(client, catalog_data, image_upload):
    # S1
    resp = client.get(reverse("web:upload"))
    assert resp.status_code == 200 and "drukuj-to.me" in resp.content.decode()
    resp = client.post(reverse("web:upload"), {"file": image_upload(size=(1448, 2048))})
    assert resp.status_code == 302
    asset = Asset.objects.get()
    assert resp.url == reverse("web:configure", args=[asset.pk])

    # S2
    resp = client.get(resp.url)
    html = resp.content.decode()
    assert resp.status_code == 200 and "wymaga poprawy" in html and "ostry" in html or "ok" in html
    resp = client.get(
        reverse("web:price_partial", args=[asset.pk]),
        {
            "format": Format.objects.get(code="A2").pk,
            "paper": PaperOption.objects.get(code="satin").pk,
            "quantity": 2,
        },
    )
    data = resp.json()
    assert (
        data["quality"]["verdict"] == "needs_enhance"
        and data["unit_price"] == "99.00"
        and data["subtotal"] == "198.00"
    )

    resp = client.post(
        reverse("web:configure", args=[asset.pk]),
        {
            "format": Format.objects.get(code="A2").pk,
            "paper": PaperOption.objects.get(code="matte").pk,
            "quantity": 1,
            "enhancer_enabled": "on",
            "crop": json.dumps({"x": 0, "y": 0.05, "w": 1, "h": 0.9, "orientation": "portrait"}),
        },
    )
    assert resp.status_code == 302
    order = Order.objects.get()
    item = order.item
    assert order.status == S.DRAFT and item.options["enhancer"] and item.options["crop"]["h"] == 0.9
    assert resp.url == reverse("web:preview", args=[order.status_token, item.pk])

    # S3
    resp = client.get(resp.url)
    assert resp.status_code == 200 and "symulacja" in resp.content.decode()
    assert (
        client.get(reverse("web:mockup_image", args=[order.status_token, item.pk]))["Content-Type"]
        == "image/png"
    )
    assert (
        client.get(
            reverse("web:fragment_image", args=[order.status_token, item.pk, "after"])
        ).status_code
        == 200
    )

    # Koszyk: drugi plik trafia do tego samego szkicu, potem go usuwamy
    client.post(reverse("web:upload"), {"file": image_upload(name="drugi.png", size=(1448, 2048))})
    second_asset = Asset.objects.exclude(pk=asset.pk).get()
    resp = client.post(
        reverse("web:configure", args=[second_asset.pk]),
        {
            "format": Format.objects.get(code="A3").pk,
            "paper": PaperOption.objects.get(code="matte").pk,
            "quantity": 2,
        },
    )
    assert resp.status_code == 302 and Order.objects.count() == 1
    order.refresh_from_db()
    assert order.items.count() == 2 and order.subtotal == item.line_total + 2 * 59
    resp = client.get(reverse("web:cart", args=[order.status_token]))
    html = resp.content.decode()
    assert resp.status_code == 200 and "Koszyk (2)" in html and "drugi.png" in html
    second_item = order.items.exclude(pk=item.pk).get()
    resp = client.post(reverse("web:cart", args=[order.status_token]), {"item": second_item.pk})
    assert resp.status_code == 302 and order.items.count() == 1
    order.refresh_from_db()
    assert order.subtotal == item.line_total

    # S4
    resp = client.post(
        reverse("web:checkout", args=[order.status_token]),
        {
            "email": "klient@example.com",
            "full_name": "Jan Testowy",
            "phone": "600",
            "line1": "Testowa 1",
            "postal_code": "00-001",
            "city": "Warszawa",
            "delivery_method": DeliveryMethod.objects.get(code="parcel_locker").pk,
            "consent_terms": "on",
            "consent_no_withdrawal": "on",
            "consent_rights": "on",
        },
    )
    assert resp.status_code == 302 and resp.url.startswith("/payments/mock/")
    order.refresh_from_db()
    assert order.status == S.AWAITING_PAYMENT and order.total == order.subtotal + 14

    # S5: błąd, potem sukces
    resp = client.get(resp.url)
    assert "SYMULACJA" in resp.content.decode()
    payment = order.payments.get()
    resp = client.post(
        reverse("orders:payment_callback"), {"external_id": payment.external_id, "result": "fail"}
    )
    assert resp.status_code == 302 and resp.url == reverse(
        "web:checkout", args=[order.status_token]
    )
    resp = client.get(reverse("orders:retry_payment", args=[order.status_token]))
    assert resp.status_code == 302
    payment2 = order.payments.order_by("-created_at").first()
    resp = client.post(
        reverse("orders:payment_callback"),
        {"external_id": payment2.external_id, "result": "success"},
    )
    assert resp.url == reverse("web:order_confirmation", args=[order.status_token])
    order.refresh_from_db()
    assert order.status == S.AWAITING_APPROVAL
    assert any("Płatność przyjęta" in m.subject for m in mail.outbox)

    # S6, S7
    assert client.get(resp.url).status_code == 200
    resp = client.get(reverse("web:order_status", args=[order.status_token]))
    assert resp.status_code == 200 and "Sprawdzamy plik" in resp.content.decode()
    assert client.get(reverse("web:order_status", args=["x" * 32])).status_code == 404

    # Operator
    get_user_model().objects.create_user("op", password="pw", is_staff=True)
    assert client.get(reverse("ops:queue")).status_code == 302  # wymaga logowania
    client.login(username="op", password="pw")
    resp = client.get(reverse("ops:queue"))
    assert resp.status_code == 200 and order.number in resp.content.decode()
    resp = client.get(reverse("ops:order_detail", args=[order.number]))
    assert resp.status_code == 200 and "Pobierz PDF" in resp.content.decode()
    resp = client.get(reverse("ops:order_print_file", args=[order.number, item.pk]))
    assert resp["Content-Type"] == "application/pdf"
    resp = client.post(reverse("ops:order_action", args=[order.number]), {"action": "approve"})
    order.refresh_from_db()
    assert order.status == S.SENT_TO_PRINTER and GhOrder.objects.count() == 1
    resp = client.get(reverse("ops:order_list"), {"attention": "on"})
    assert resp.status_code == 200 and order.number not in resp.content.decode()

    # Biuro GH pobiera pliki i realizuje; sklep odpytuje o zmiany (przycisk w panelu)
    job = PrintJob.objects.get()
    gh_order = GhOrder.objects.get(shop_order_number=job.shop_order_number)
    gh_services.download_pending_files()
    for status in ("in_progress", "ready", "shipped"):
        gh_services.set_status(gh_order, status)
    resp = client.post(
        reverse("ops:poll_gh"), {"next": reverse("ops:order_detail", args=[order.number])}
    )
    assert resp.status_code == 302
    order.refresh_from_db()
    assert order.status == S.SHIPPED
    assert poll_changes()["changed"] == 0
    resp = client.get(reverse("web:order_status", args=[order.status_token]))
    assert "Nadane" in resp.content.decode()

    # Doręczenie i reklamacja
    client.post(reverse("ops:order_action", args=[order.number]), {"action": "deliver"})
    resp = client.post(
        reverse("web:order_status", args=[order.status_token]), {"description": "zagięty róg"}
    )
    assert resp.status_code == 302
    order.refresh_from_db()
    assert order.complaints.count() == 1 and order.needs_attention
    resp = client.get(reverse("ops:order_detail", args=[order.number]))
    assert "zagięty róg" in resp.content.decode()


@pytest.mark.django_db
def test_upload_errors_are_shown(client, catalog_data):
    from django.core.files.uploadedfile import SimpleUploadedFile

    resp = client.post(
        reverse("web:upload"),
        {"file": SimpleUploadedFile("x.png", b"garbage", content_type="image/png")},
    )
    assert resp.status_code == 200 and "uszkodzony" in resp.content.decode()


@pytest.mark.django_db
def test_format_from_home_page_is_preselected(client, catalog_data, image_upload):
    html = client.get(reverse("web:upload")).content.decode()
    assert "29,7 × 42 cm" in html and "od 59 zł" in html and 'data-format="B2"' in html

    resp = client.post(
        reverse("web:upload"), {"file": image_upload(size=(1448, 2048)), "format": "A2"}
    )
    asset = Asset.objects.get()
    assert resp.url == reverse("web:configure", args=[asset.pk]) + "?format=A2"
    resp = client.get(resp.url)
    assert resp.context["form"].initial["format"] == Format.objects.get(code="A2").pk

    # Nieznany format nie trafia do adresu, zablokowany nie jest podstawiany
    resp = client.post(
        reverse("web:upload"), {"file": image_upload(size=(362, 512)), "format": "XXL"}
    )
    small = Asset.objects.exclude(pk=asset.pk).get()
    assert resp.url == reverse("web:configure", args=[small.pk])
    resp = client.get(reverse("web:configure", args=[small.pk]), {"format": "B2"})
    assert resp.context["form"].initial["format"] != Format.objects.get(code="B2").pk


@pytest.mark.django_db
def test_home_background_switch_only_when_enabled(client, catalog_data, settings):
    """Domyślne jest ciemne tło 4; bez przełącznika `?tlo=` nic nie zmienia."""
    settings.HOME_BACKGROUND_SWITCH = False
    html = client.get(reverse("web:upload"), {"tlo": "3"}).content.decode()
    assert "page-home--tlo tlo-4" in html and "tlo-3" not in html and "tlo-switch" not in html

    settings.HOME_BACKGROUND_SWITCH = True
    html = client.get(reverse("web:upload"), {"tlo": "3"}).content.decode()
    assert "page-home--tlo tlo-3" in html and "data-carousel" not in html and "tlo-switch" in html
    # Wybór zostaje w sesji, nieznany numer wraca do domyślnego, 0 to karuzela
    assert "tlo-3" in client.get(reverse("web:upload")).content.decode()
    html = client.get(reverse("web:upload"), {"tlo": "9"}).content.decode()
    assert "page-home--tlo tlo-4" in html
    html = client.get(reverse("web:upload"), {"tlo": "0"}).content.decode()
    assert "page-home--tlo" not in html and "data-carousel" in html


@pytest.mark.django_db
def test_home_frame_scene_only_on_its_background(client, catalog_data, settings):
    """Tło 4 ma stos obrazów z klikalną ramą (manifest ze scripts/ramy.py), inne tła zwykły pasek."""
    settings.HOME_BACKGROUND_SWITCH = True
    html = client.get(reverse("web:upload"), {"tlo": "4"}).content.decode()
    assert "data-scena" in html and "img/tla/ramy/pusta.webp" in html and "js/rama.js" in html
    assert "fonts.googleapis.com/css2?family=" in html and 'data-caption="Dla ' in html
    assert 'data-prompt="' in html and "data-scena-kartka" in html
    assert "home-hero__band" not in html
    html = client.get(reverse("web:upload"), {"tlo": "3"}).content.decode()
    assert "data-scena" not in html and "home-hero__band" in html


@pytest.mark.django_db
def test_inspirations_gallery(client, catalog_data):
    """Galeria z manifestu ram: siatka z filtrami i podstrona z promptem; pusta rama i nieznane slugi poza."""
    html = client.get(reverse("web:inspirations")).content.decode()
    assert "img/galeria/rama-1.webp" in html and "pusta" not in html
    filtered = client.get(reverse("web:inspirations"), {"pasja": "koty"}).content.decode()
    assert "rama-1.webp" in filtered and "rama-3-2.webp" not in filtered
    both = client.get(reverse("web:inspirations"), {"pasja": "kosmos", "okazja": "Dzień Dziecka"})
    html = both.content.decode()
    assert "rama-4.webp" in html and "rama-0.webp" not in html and "?pasja=kosmos" in html
    assert client.get(reverse("web:inspirations"), {"pasja": "nic"}).status_code == 200
    detail = client.get(reverse("web:inspiration", args=["rama-1"])).content.decode()
    assert "Kopiuj prompt" in detail and "Pionowy format 2:3" in detail
    assert client.get(reverse("web:inspiration", args=["pusta"])).status_code == 404


@pytest.mark.django_db
def test_prompt_copied_is_logged(client, caplog):
    """Kopiowanie promptu z kartki przy ramie zostawia ślad w logu; tylko POST."""
    assert client.get(reverse("web:prompt_copied")).status_code == 405
    with caplog.at_level("INFO", logger="slop.poster"):
        resp = client.post(reverse("web:prompt_copied"), {"rama": "rama-1"})
    assert resp.status_code == 204 and "prompt skopiowany: rama-1" in caplog.text


@pytest.mark.django_db
def test_blocked_format_cannot_be_ordered(client, catalog_data, image_upload):
    client.post(reverse("web:upload"), {"file": image_upload(size=(362, 512))})
    asset = Asset.objects.get()
    resp = client.post(
        reverse("web:configure", args=[asset.pk]),
        {
            "format": Format.objects.get(code="B2").pk,
            "paper": PaperOption.objects.get(code="matte").pk,
            "quantity": 1,
        },
    )
    assert resp.status_code == 200 and "zablokowany" in resp.content.decode()
    resp = client.get(
        reverse("web:price_partial", args=[asset.pk]), {"format": Format.objects.get(code="B2").pk}
    )
    assert resp.json()["quality"]["blocked"] is True


@pytest.mark.django_db
def test_ops_settings_and_static_pages(client, catalog_data):
    get_user_model().objects.create_user("op", password="pw", is_staff=True)
    client.login(username="op", password="pw")
    assert client.get(reverse("ops:settings")).status_code == 200
    for slug in ("regulamin", "prywatnosc", "faq", "o-nas"):
        assert client.get(reverse("web:static_page", args=[slug])).status_code == 200
    assert client.get(reverse("web:static_page", args=["nope"])).status_code == 404
