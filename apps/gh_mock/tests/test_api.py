"""Atrapa GH jako serwer kontraktu: token, idempotencja, walidacja, pliki, zmiany, statusy."""

import hashlib
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.gh_mock import services
from apps.gh_mock.models import GhOrder, GhOrderFile, GhShop

TOKEN = "demo-shop-token"
PDF = b"%PDF-1.4 test\n%%EOF\n"


def file_obj(data=PDF, url="http://localhost:8000/media/x.pdf", hours=24):
    return {
        "url": url,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "expires_at": (timezone.now() + timedelta(hours=hours)).isoformat(),
    }


def order_body(number="SP-1", items=1, **extra):
    body = {
        "shop_order_number": number,
        "title": f"Zamówienie {number}",
        "total_value": str(61 * items),
        "currency": "PLN",
        "items": [
            {
                "position": i + 1,
                "name": f"Plakat A2 ({i + 1})",
                "product_code": "poster-a2-matte",
                "quantity": 1,
                "unit_price": "61.00",
                "file": file_obj(url=f"http://localhost:8000/media/{i}.pdf"),
            }
            for i in range(items)
        ],
    }
    body.update(extra)
    return body


@pytest.fixture
def shop(db, settings):
    settings.GH_API_TOKEN = TOKEN
    return services.ensure_demo_shop()


def post(client, url, body, token=TOKEN):
    return client.post(
        url,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Token {token}",
    )


def get(client, url, token=TOKEN, **params):
    return client.get(url, params, HTTP_AUTHORIZATION=f"Token {token}")


@pytest.mark.django_db
def test_token_required_and_revocable(client, shop):
    url = reverse("gh_mock:api_orders")
    assert client.post(url, data="{}", content_type="application/json").status_code == 401
    assert post(client, url, order_body(), token="zly").status_code == 401
    assert post(client, url, order_body()).status_code == 201
    shop.is_active = False
    shop.save()
    assert post(client, url, order_body("SP-2")).status_code == 401
    assert GhShop.authenticate("") is None


@pytest.mark.django_db
def test_post_is_idempotent_and_conflicts_on_changed_body(client, shop):
    url = reverse("gh_mock:api_orders")
    body = order_body(items=2)
    first = post(client, url, body)
    assert first.status_code == 201
    data = first.json()
    assert data["status"] == "new" and data["gh_number"].endswith(f"/{timezone.now().year}")
    assert [f["state"] for f in data["files"]] == ["pending", "pending"]
    assert [f["position"] for f in data["files"]] == [1, 2]

    again = post(client, url, body)  # retry po timeoucie: identyczne body
    assert again.status_code == 200 and again.json()["gh_number"] == data["gh_number"]
    assert GhOrder.objects.count() == 1

    changed = post(client, url, {**body, "note_for_production": "inaczej"})
    assert changed.status_code == 409 and GhOrder.objects.count() == 1


@pytest.mark.django_db
def test_validation_rejects_customer_data_and_bad_totals(client, shop):
    url = reverse("gh_mock:api_orders")
    resp = post(client, url, order_body(email="a@b.pl", shipping={"address": {"city": "W"}}))
    assert resp.status_code == 400
    errors = resp.json()["errors"]
    assert "email" in errors and "shipping" in errors and "shipping.address" in errors

    resp = post(client, url, order_body(total_value="1.00"))
    assert resp.status_code == 400 and "total_value" in resp.json()["errors"]

    body = order_body()
    body["items"][0]["file"]["sha256"] = "zle"
    body["items"][0]["position"] = 7
    resp = post(client, url, body)
    errors = resp.json()["errors"]
    assert resp.status_code == 400
    assert "items.0.file.sha256" in errors and "items.0.position" in errors

    resp = post(client, url, order_body(currency="EUR", items=0))
    assert resp.status_code == 400 and {"currency", "items"} <= set(resp.json()["errors"])
    assert (
        client.post(
            url,
            data="not json",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {TOKEN}",
        ).status_code
        == 400
    )
    assert GhOrder.objects.count() == 0


@pytest.mark.django_db
def test_unknown_product_code_flags_review(client, shop):
    body = order_body()
    body["items"][0]["product_code"] = "mug-xl"
    assert post(client, reverse("gh_mock:api_orders"), body).status_code == 201
    assert GhOrder.objects.get().needs_review is True


@pytest.mark.django_db
def test_download_verifies_header_size_and_checksum(shop):
    bodies = {n: order_body(n) for n in ("SP-OK", "SP-SHA", "SP-TXT", "SP-EXP", "SP-NET")}
    for number, body in bodies.items():
        body["items"][0]["file"]["url"] = f"http://localhost:8000/media/{number}.pdf"
    bodies["SP-SHA"]["items"][0]["file"]["sha256"] = "0" * 64
    bodies["SP-EXP"]["items"][0]["file"] = file_obj(hours=-1)
    for body in bodies.values():
        services.accept_order(shop, body)

    def fetch(url):
        if "SP-NET" in url:
            raise OSError("brak sieci")
        return b"not a pdf" if "SP-TXT" in url else PDF

    assert services.download_pending_files(fetch) == 5
    states = {
        f.order.shop_order_number: (f.state, f.error)
        for f in GhOrderFile.objects.select_related("order")
    }
    assert states["SP-OK"] == ("downloaded", "")
    assert states["SP-SHA"] == ("failed", "checksum_mismatch")
    assert states["SP-TXT"] == ("failed", "not_pdf")
    assert states["SP-EXP"] == ("failed", "expired")
    assert states["SP-NET"] == ("failed", "unreachable")
    assert GhOrder.objects.get(shop_order_number="SP-OK").all_files_downloaded is True
    assert services.download_pending_files(fetch) == 0  # failed nie są ponawiane same


@pytest.mark.django_db
def test_status_progression_requires_downloaded_files(shop):
    order, _ = services.accept_order(shop, order_body())
    with pytest.raises(services.NotAllowed):
        services.set_status(order, "in_progress")
    with pytest.raises(services.NotAllowed):
        services.set_status(order, "ready")
    services.download_pending_files(fetch=lambda url: PDF)
    order.refresh_from_db()
    assert order.all_files_downloaded
    services.set_status(order, "in_progress")
    services.set_status(order, "ready")
    services.set_status(order, "shipped")
    assert order.shipped_at is not None
    with pytest.raises(services.NotAllowed):
        services.set_status(order, "shipped")


@pytest.mark.django_db
def test_changes_since_uses_server_time_cursor(client, shop):
    services.accept_order(shop, order_body("SP-A"))
    resp = get(client, reverse("gh_mock:api_changes"))
    assert resp.status_code == 200
    data = resp.json()
    assert [o["shop_order_number"] for o in data["orders"]] == ["SP-A"]
    since = data["server_time"]

    resp = get(client, reverse("gh_mock:api_changes"), since=since)
    assert resp.json()["orders"] == []

    services.accept_order(shop, order_body("SP-B"))
    services.download_pending_files(fetch=lambda url: PDF)
    resp = get(client, reverse("gh_mock:api_changes"), since=since)
    numbers = [o["shop_order_number"] for o in resp.json()["orders"]]
    assert numbers == ["SP-A", "SP-B"]  # A zmieniło stan pliku, B jest nowe
    assert all(f["state"] == "downloaded" for o in resp.json()["orders"] for f in o["files"])
    assert get(client, reverse("gh_mock:api_changes"), since="zle").status_code == 400

    detail = get(client, reverse("gh_mock:api_order", args=["SP-A"]))
    assert detail.status_code == 200 and detail.json()["status"] == "new"
    assert get(client, reverse("gh_mock:api_order", args=["SP-X"])).status_code == 404


@pytest.mark.django_db
def test_file_resend_only_for_pending_or_failed(client, shop):
    order, _ = services.accept_order(shop, order_body("SP-F"))
    file = order.files.get()
    services.expire_file(file)
    url = reverse("gh_mock:api_files", args=["SP-F"])

    resp = post(client, url, {"kind": "item", "position": 1, "file": file_obj()})
    assert resp.status_code == 200
    assert resp.json()["files"] == [{"kind": "item", "position": 1, "state": "pending"}]

    assert post(client, url, {"kind": "item", "position": 9, "file": file_obj()}).status_code == 400
    assert post(client, url, {"kind": "item", "position": 1, "file": {}}).status_code == 400
    assert post(client, reverse("gh_mock:api_files", args=["SP-X"]), {}).status_code == 404

    services.download_pending_files(fetch=lambda url: PDF)
    resp = post(client, url, {"kind": "item", "position": 1, "file": file_obj()})
    assert resp.status_code == 400 and "file" in resp.json()["errors"]

    # Etykietę można dosłać, choć POST jej nie miał (do czasu taska „Etykieta”).
    resp = post(client, url, {"kind": "label", "file": file_obj()})
    assert resp.status_code == 200 and {"kind": "label", "state": "pending"} in resp.json()["files"]


@pytest.mark.django_db
def test_dashboard_renders_and_actions_work(client, shop):
    order, _ = services.accept_order(shop, order_body("SP-D", items=2))
    resp = client.get(reverse("gh_mock:dashboard"))
    assert resp.status_code == 200 and "SP-D" in resp.content.decode()
    resp = client.get(reverse("gh_mock:order", args=[order.pk]))
    assert resp.status_code == 200 and "Plakat A2 (2)" in resp.content.decode()

    resp = client.post(reverse("gh_mock:action", args=[order.pk]), {"action": "in_progress"})
    assert resp.status_code == 302
    order.refresh_from_db()
    assert order.status == "new"  # pliki niepobrane

    file = order.files.first()
    client.post(
        reverse("gh_mock:action", args=[order.pk]), {"action": "expire", "file_id": file.pk}
    )
    file.refresh_from_db()
    assert file.state == "failed" and file.error == "expired"
