"""Podpisane, wygasające linki do plików (kontrakt pkt 4): wystawianie, wydawanie, odmowa."""

from datetime import datetime, timedelta

import pytest
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from apps.assets.models import AssetVersion
from apps.assets.services import create_asset_from_upload, save_version
from apps.gh_mock import services as gh_services
from apps.printing import links
from apps.printing.services import file_object

PDF = b"%PDF-1.4 test\n%%EOF\n"


@pytest.fixture
def print_pdf(db, image_upload):
    asset = create_asset_from_upload(image_upload())
    return save_version(asset, AssetVersion.Kind.PRINT_PDF, provider="test", pdf_bytes=PDF)


def _token(url):
    return url.rsplit("/", 1)[1].removesuffix(".pdf")


def test_file_object_links_are_signed_and_expiry_matches_token(print_pdf, settings):
    obj = file_object(print_pdf)
    assert obj["url"].startswith(settings.SITE_URL + "/files/signed/")
    assert "/media/" not in obj["url"]
    payload = signing.loads(_token(obj["url"]), salt=links.SALT)
    expires_at = datetime.fromisoformat(obj["expires_at"])
    assert payload["exp"] == int(expires_at.timestamp())
    assert timedelta(hours=23) < expires_at - timezone.now() <= timedelta(hours=24)
    assert obj["size"] == len(PDF) and len(obj["sha256"]) == 64


def test_each_issue_is_a_fresh_link(print_pdf):
    assert file_object(print_pdf)["url"] != file_object(print_pdf)["url"]


def test_signed_link_serves_the_pdf(client, print_pdf, settings):
    url, _ = links.issue(print_pdf)
    assert url.endswith(".pdf")
    resp = client.get(url.removeprefix(settings.SITE_URL))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert b"".join(resp.streaming_content) == PDF


def test_tampered_expired_or_missing_link_is_refused(client, print_pdf):
    url, _ = links.issue(print_pdf)
    good = _token(url)
    tampered = good[:-1] + ("A" if good[-1] != "A" else "B")
    assert client.get(reverse("printing:file", kwargs={"token": tampered})).status_code == 404

    past = int((timezone.now() - timedelta(seconds=1)).timestamp())
    expired = signing.dumps({"v": print_pdf.pk, "exp": past, "n": "0"}, salt=links.SALT)
    assert client.get(reverse("printing:file", kwargs={"token": expired})).status_code == 410

    print_pdf.delete()
    assert client.get(reverse("printing:file", kwargs={"token": good})).status_code == 404


def test_gh_mock_fetches_through_the_signed_link_and_rejects_bad_ones(print_pdf):
    url, _ = links.issue(print_pdf)
    assert gh_services.fetch_url(url) == PDF
    with pytest.raises(links.LinkError):
        gh_services.fetch_url(url.removesuffix(".pdf")[:-2] + "xx.pdf")
