"""
Zgodność sklepu z kontraktem v1: body POST (pkt 3-4), brak danych kupującego, idempotencja,
błędy 400/401/409 i przejściowe (pkt 7), polling i mapowanie statusów (pkt 5-6), dosłanie linku.
Sprawdza rdzeń razem z produktami sklepu; opis uruchomienia w `conftest.py`.
"""

import json
from decimal import Decimal

import pytest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.gh_mock import services as gh_services
from apps.gh_mock.models import GhOrder, GhOrderFile
from apps.orders import services as order_services
from apps.orders.models import OrderStatus as S
from apps.orders.models import Shipment
from apps.printing import services as print_services
from apps.printing.models import PollCursor, PrintJob
from apps.printing.providers import GhApiProvider, ProviderRejected
from apps.printing.services import (
    PrintSubmitError,
    build_request,
    create_print_job,
    poll_changes,
    submit_order,
    submit_print_job,
)
from apps.products import dictionary

from .helpers import QUANTITY, customer_values, gh_order_of, keys_of, sha256_of

BODY_KEYS = {"shop_order_number", "title", "total_value", "currency", "items", "label"}
ITEM_KEYS = {"position", "name", "product_code", "quantity", "unit_price", "file"}
FILE_KEYS = {"url", "sha256", "size", "expires_at"}

J = PrintJob.Status

pytestmark = pytest.mark.django_db


def _items(order):
    return list(order.items.order_by("pk"))


# ---------------------------------------------------------------------------
# Body POST (pkt 3 i 4)
# ---------------------------------------------------------------------------


def test_body_is_one_order_with_a_line_per_cart_item(contract_order):
    body = build_request(create_print_job(contract_order))
    assert set(body) == BODY_KEYS  # bez `note_for_production`, bo to nie dodruk
    assert body["shop_order_number"] == contract_order.number
    assert body["title"] and body["currency"] == "PLN"
    items = _items(contract_order)
    assert [line["position"] for line in body["items"]] == list(range(1, len(items) + 1))
    for line, item in zip(body["items"], items, strict=True):
        assert set(line) == ITEM_KEYS
        assert line["quantity"] == item.quantity == QUANTITY


def test_lines_come_from_product_and_dictionary(contract_order):
    """Kod, nazwa i stawka z `gh_line` produktu; kod ze słownika ma nazwę i stawkę ze słownika."""
    body = build_request(create_print_job(contract_order))
    for line, item in zip(body["items"], _items(contract_order), strict=True):
        gh_line = item.definition.gh_line(item)
        assert (line["product_code"], line["name"]) == (gh_line.product_code, gh_line.name)
        assert line["name"].strip()
        if dictionary.known(line["product_code"]):
            entry = dictionary.get(line["product_code"])
            assert line["name"] == entry.name and item.print_rate == entry.print_rate
    total = sum(Decimal(line["unit_price"]) * line["quantity"] for line in body["items"])
    assert Decimal(body["total_value"]) == total


def test_gh_gets_print_rates_not_retail_prices(contract_order):
    """`unit_price` to stawka GH za druk; cena detaliczna, obrót i marża zostają w sklepie."""
    body = build_request(create_print_job(contract_order))
    items = _items(contract_order)
    for line, item in zip(body["items"], items, strict=True):
        assert line["unit_price"] == str(item.print_rate) and item.print_rate > 0
    assert body["total_value"] == str(contract_order.print_total)

    sent = {line["unit_price"] for line in body["items"]} | {body["total_value"]}
    retail = {str(item.unit_price) for item in items}
    retail |= {str(contract_order.subtotal), str(contract_order.total)}
    dump = json.dumps(body)
    assert [value for value in retail - sent if value in dump] == []


def test_files_and_label_are_signed_links_with_checksum(contract_order):
    job = create_print_job(contract_order)
    body = build_request(job)
    now = timezone.now()
    files = [
        (line["file"], item.print_files[0])
        for line, item in zip(body["items"], _items(contract_order), strict=True)
    ]
    for obj, source in [*files, (body["label"], job.shipment)]:
        assert set(obj) == FILE_KEYS
        assert obj["url"].startswith("http") and "/media/" not in obj["url"]
        assert (obj["sha256"], obj["size"]) == sha256_of(source)
        assert parse_datetime(obj["expires_at"]) > now


def test_body_has_no_customer_data(contract_order):
    body = build_request(create_print_job(contract_order))
    dump = json.dumps(body, ensure_ascii=False)
    assert [value for value in customer_values() if value in dump] == []
    forbidden = {"email", "phone", "address", "shipping_address", "full_name", "customer"}
    assert keys_of(body) & forbidden == set()


def test_gh_accepts_the_cart_and_downloads_every_file(sent_job):
    order = sent_job.order
    gh_order = gh_order_of(sent_job)
    assert GhOrder.objects.count() == 1 and gh_order.items.count() == order.items.count()
    assert gh_order.total_value == order.print_total
    unknown = [
        line.product_code
        for line in gh_order.items.all()
        if not dictionary.known(line.product_code)
    ]
    assert gh_order.needs_review == bool(unknown)

    assert gh_services.download_pending_files() == order.items.count() + 1  # pozycje i etykieta
    assert gh_order.files.exclude(state=GhOrderFile.State.DOWNLOADED).count() == 0
    poll_changes()
    sent_job.refresh_from_db()
    assert sent_job.status == J.ACCEPTED and sent_job.all_files_downloaded


# ---------------------------------------------------------------------------
# Idempotencja i błędy (pkt 3 i 7)
# ---------------------------------------------------------------------------


def test_resubmit_is_idempotent(sent_job):
    again = GhApiProvider().submit(sent_job.spec)
    assert again.gh_number == sent_job.external_id
    assert submit_order(sent_job.order) == sent_job  # otwarte zlecenie nie jest dublowane
    assert GhOrder.objects.count() == 1


def test_same_number_with_changed_body_is_409_without_retry(contract_order):
    job = create_print_job(contract_order)
    GhApiProvider().submit({**job.spec, "title": "Inna treść pod tym samym numerem"})
    with pytest.raises(ProviderRejected) as exc:
        GhApiProvider().submit(job.spec)
    assert exc.value.status_code == 409

    with pytest.raises(PrintSubmitError):
        submit_print_job(job)
    assert job.status == J.FAILED and job.attempts == 1 and "409" in job.reason
    assert GhOrder.objects.count() == 1


def test_rejected_body_is_400_without_retry_and_order_goes_on_hold(
    contract_order, operator, monkeypatch
):
    original = print_services.build_request
    monkeypatch.setattr(
        print_services, "build_request", lambda job: {**original(job), "email": "x@example.com"}
    )
    order_services.approve_order(contract_order, actor_user=operator)
    contract_order.refresh_from_db()
    job = PrintJob.objects.get(order=contract_order)
    assert contract_order.status == S.ON_HOLD and "400" in contract_order.hold_reason
    assert job.status == J.FAILED and job.attempts == 1
    assert GhOrder.objects.count() == 0


def test_revoked_token_is_401_and_stops_submit_and_polling(contract_order, operator, settings):
    token = settings.GH_API_TOKEN
    settings.GH_API_TOKEN = "cofniety-token"
    order_services.approve_order(contract_order, actor_user=operator)
    contract_order.refresh_from_db()
    assert contract_order.status == S.ON_HOLD and "token" in contract_order.hold_reason
    assert PollCursor.get().auth_failed is True
    assert "wstrzymany" in poll_changes()["error"]

    settings.GH_API_TOKEN = token
    print_services.reset_poll_auth()
    assert poll_changes()["error"] == ""


def test_transient_errors_are_retried(contract_order):
    GhApiProvider.fail_next_requests = 2
    job = submit_print_job(create_print_job(contract_order))
    assert job.status == J.SENT and job.attempts == 3 and job.external_id


def test_transient_errors_past_the_limit_put_order_on_hold(contract_order, operator, settings):
    GhApiProvider.fail_next_requests = settings.PRINT_SUBMIT_MAX_ATTEMPTS
    order_services.approve_order(contract_order, actor_user=operator)
    contract_order.refresh_from_db()
    failed = PrintJob.objects.get(order=contract_order)
    assert contract_order.status == S.ON_HOLD
    assert failed.status == J.FAILED and failed.attempts == settings.PRINT_SUBMIT_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# Polling i statusy (pkt 5 i 6), dosłanie linku (pkt 4)
# ---------------------------------------------------------------------------


def test_polling_maps_gh_statuses_to_order(sent_job):
    order = sent_job.order
    gh_order = gh_order_of(sent_job)
    assert poll_changes()["changed"] == 0  # nic nowego od POST-a

    gh_services.download_pending_files()
    assert poll_changes()["changed"] == 1
    sent_job.refresh_from_db()
    order.refresh_from_db()
    assert sent_job.status == J.ACCEPTED and order.status == S.SENT_TO_PRINTER

    for gh_status, job_status, order_status in (
        ("in_progress", J.ACCEPTED, S.SENT_TO_PRINTER),
        ("ready", J.PRINTED, S.PRINTED),
        ("shipped", J.SHIPPED, S.SHIPPED),
    ):
        gh_services.set_status(gh_order, gh_status)
        poll_changes()
        sent_job.refresh_from_db()
        order.refresh_from_db()
        assert (sent_job.provider_status, sent_job.status) == (gh_status, job_status)
        assert order.status == order_status
    assert sent_job.shipment.status == Shipment.Status.SHIPPED and sent_job.shipment.shipped_at

    events = sent_job.events.count()
    assert poll_changes()["changed"] == 0 and sent_job.events.count() == events


def test_shipped_without_ready_implies_printed(sent_job):
    gh_order = gh_order_of(sent_job)
    gh_services.download_pending_files()
    for status in ("in_progress", "ready", "shipped"):
        gh_services.set_status(gh_order, status)
    assert poll_changes()["changed"] == 1
    order = sent_job.order
    order.refresh_from_db()
    statuses = list(order.status_events.values_list("to_status", flat=True))
    assert order.status == S.SHIPPED and statuses[-2:] == [S.PRINTED, S.SHIPPED]


def test_failed_download_gets_a_fresh_link(sent_job):
    gh_file = gh_order_of(sent_job).files.filter(kind="item").order_by("position").first()
    gh_services.expire_file(gh_file)
    old_expiry = gh_file.expires_at

    summary = poll_changes()
    assert summary["resent"] == 1 and summary["changed"] == 0
    gh_file.refresh_from_db()
    assert gh_file.state == GhOrderFile.State.PENDING and gh_file.expires_at >= old_expiry
    assert sent_job.events.filter(status="file_resend", applied=True).count() == 1

    gh_services.download_pending_files()
    poll_changes()
    sent_job.refresh_from_db()
    assert sent_job.status == J.ACCEPTED
