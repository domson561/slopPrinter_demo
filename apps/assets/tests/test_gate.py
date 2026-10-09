"""Bramka plików: obrazy. Typ po zawartości, limity, zapis na nowo, prywatny katalog."""

import io
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageCms

from apps.assets.gate import IMAGE_MIME, UploadError, check_upload
from apps.assets.services import create_asset_from_upload

PDF_BYTES = b"%PDF-1.4\n%%EOF\n"


def image_bytes(size=(200, 300), fmt="JPEG", **save_kwargs) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(buf, fmt, **save_kwargs)
    return buf.getvalue()


def upload(data: bytes, name="plik.jpg"):
    return SimpleUploadedFile(name, data, content_type="application/octet-stream")


def test_image_is_rewritten_without_metadata_and_trailing_data():
    exif = Image.Exif()
    exif[0x010E] = "sekretny opis"  # ImageDescription
    exif[0x0131] = "Edytor 1.0"  # Software
    data = image_bytes(exif=exif.tobytes()) + b"<script>alert(1)</script>"
    clean = check_upload(upload(data))
    assert clean.mime == "image/jpeg" and clean.ext == ".jpg"
    assert b"sekretny" not in clean.data and b"<script>" not in clean.data
    out = Image.open(io.BytesIO(clean.data))
    assert out.size == (200, 300) and not out.getexif()


def test_exif_orientation_is_applied_before_rewrite():
    exif = Image.Exif()
    exif[0x0112] = 6  # obrót o 90 stopni
    clean = check_upload(upload(image_bytes(size=(100, 200), exif=exif.tobytes())))
    assert (clean.width, clean.height) == (200, 100)
    assert 0x0112 not in Image.open(io.BytesIO(clean.data)).getexif()


def test_color_profile_survives_rewrite():
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    clean = check_upload(upload(image_bytes(fmt="PNG", icc_profile=icc), name="a.png"))
    assert Image.open(io.BytesIO(clean.data)).info.get("icc_profile") == icc


@pytest.mark.parametrize(
    ("data", "name", "message"),
    [
        (PDF_BYTES, "zdjecie.jpg", "Obsługujemy tylko pliki JPG, PNG i WebP"),
        (b"<svg onload='alert(1)'/>", "logo.svg", "Obsługujemy tylko pliki JPG, PNG i WebP"),
        (b"<html><script>x</script>", "obraz.png", "uszkodzony"),
        (b"\x89PNG\r\n\x1a\nobciete", "obraz.png", "uszkodzony"),
    ],
)
def test_type_comes_from_content_not_name(data, name, message):
    with pytest.raises(UploadError, match=message):
        check_upload(upload(data, name=name), allowed_mime=IMAGE_MIME)


def test_pixel_limit_is_checked_before_decoding(settings):
    settings.PIL_MAX_IMAGE_PIXELS = 1000
    with pytest.raises(UploadError, match="za dużo pikseli"):
        check_upload(upload(image_bytes()))


def test_size_limits(settings):
    with pytest.raises(UploadError, match="za mały"):
        check_upload(upload(image_bytes(size=(40, 300))))
    with pytest.raises(UploadError, match="za duży"):
        check_upload(upload(image_bytes()), max_bytes=100)
    settings.UPLOAD_MAX_BYTES = 100  # limit sklepu wygrywa z większym limitem wołającego
    with pytest.raises(UploadError, match="za duży"):
        check_upload(upload(image_bytes()), max_bytes=10**9)


def test_product_can_only_narrow_shop_types(settings):
    settings.UPLOAD_ALLOWED_MIME = {"image/jpeg"}
    with pytest.raises(UploadError, match="tylko pliki JPG"):
        check_upload(upload(image_bytes(fmt="PNG"), name="a.png"), allowed_mime=IMAGE_MIME)


@pytest.mark.django_db
def test_original_is_stored_privately(settings, catalog_data):
    asset = create_asset_from_upload(upload(image_bytes(size=(600, 900)), name="moje wakacje.jpg"))
    path = Path(asset.original_file.path).resolve()
    assert path.is_relative_to(Path(settings.PRIVATE_MEDIA_ROOT).resolve())
    assert not path.is_relative_to(Path(settings.MEDIA_ROOT).resolve())
    assert "wakacje" not in asset.original_file.name
    assert asset.original_filename == "moje wakacje.jpg"
    with pytest.raises(ValueError):
        _ = asset.original_file.url


@pytest.mark.django_db
def test_poster_shop_refuses_pdf_upload(client, catalog_data):
    from django.urls import reverse

    resp = client.post(reverse("web:upload"), {"file": upload(PDF_BYTES, name="druk.pdf")})
    assert (
        resp.status_code == 200
        and "Obsługujemy tylko pliki JPG, PNG i WebP" in resp.content.decode()
    )
