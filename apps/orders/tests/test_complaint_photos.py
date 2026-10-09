"""Zdjęcia do reklamacji: przez bramkę plików, w prywatnym katalogu, wydawane tylko operatorowi."""

import io
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.assets.gate import UploadError
from apps.orders import services
from apps.orders.models import OrderStatus


def photo(name="zdjecie.jpg") -> SimpleUploadedFile:
    buf = io.BytesIO()
    Image.new("RGB", (120, 90), (10, 120, 200)).save(buf, "JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


@pytest.mark.django_db
def test_complaint_photos_go_through_gate(bare_order, settings):
    order = bare_order(status=OrderStatus.DELIVERED)
    svg = SimpleUploadedFile("x.svg", b"<svg onload='alert(1)'/>", content_type="image/svg+xml")
    with pytest.raises(UploadError):
        services.file_complaint(order, "zły druk", [photo(), svg])
    settings.COMPLAINT_PHOTOS_MAX = 1
    with pytest.raises(UploadError, match="najwyżej 1"):
        services.file_complaint(order, "zły druk", [photo(), photo()])
    assert order.complaints.count() == 0

    complaint = services.file_complaint(order, "zły druk", [photo("<b>moje</b>.jpg")])
    [stored] = complaint.photos.all()
    assert "moje" not in stored.image.name and stored.image.name.endswith(".jpg")
    assert (
        Path(stored.image.path)
        .resolve()
        .is_relative_to(Path(settings.PRIVATE_MEDIA_ROOT).resolve())
    )


@pytest.mark.django_db
def test_complaint_photo_is_served_only_to_operator(bare_order, client):
    order = bare_order(status=OrderStatus.DELIVERED)
    complaint = services.file_complaint(order, "zły druk", [photo()])
    url = reverse("ops:complaint_photo", args=[order.number, complaint.photos.get().pk])

    assert client.get(url).status_code == 302  # do logowania
    client.force_login(get_user_model().objects.create_user("op", password="x", is_staff=True))
    resp = client.get(url)
    assert resp.status_code == 200 and resp["Content-Type"] == "image/jpeg"
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert b"".join(resp.streaming_content).startswith(b"\xff\xd8\xff")
    other = reverse("ops:complaint_photo", args=["PA-INNE", complaint.photos.get().pk])
    assert client.get(other).status_code == 404
