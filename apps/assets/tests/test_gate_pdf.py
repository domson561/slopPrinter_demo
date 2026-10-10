"""Bramka plików: PDF-y. Szyfrowanie, treść aktywna, limit stron; zwykłe pliki do druku przechodzą."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from pypdf import PdfWriter
from pypdf.actions import JavaScript
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

from apps.assets.gate import PDF_MIME, UploadError, check_upload
from apps.poster.models import Format
from apps.poster.pipeline import PrepressService


def make_pdf(pages=1, prepare=None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    if prepare:
        prepare(writer)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def upload(data: bytes, name="druk.pdf"):
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def launch_on_open(writer):
    writer.root_object[NameObject("/OpenAction")] = DictionaryObject(
        {NameObject("/S"): NameObject("/Launch"), NameObject("/F"): TextStringObject("calc.exe")}
    )


def goto_on_open(writer):
    """Zwykłe „otwórz na pierwszej stronie, dopasuj”: nie jest treścią aktywną."""
    writer.root_object[NameObject("/OpenAction")] = ArrayObject(
        [writer.pages[0].indirect_reference, NameObject("/Fit")]
    )


def test_plain_pdf_passes_unchanged():
    data = make_pdf(pages=3, prepare=goto_on_open)
    clean = check_upload(upload(data))
    assert (clean.mime, clean.ext, clean.pages) == (PDF_MIME, ".pdf", 3)
    assert clean.data == data


def test_our_own_print_pdf_passes():
    a2 = Format(code="A2", name="A2", width_mm=420, height_mm=594, bleed_mm=3, base_price=1)
    data = PrepressService().build_pdf(Image.new("RGB", (100, 141)), a2, "portrait", False)
    assert check_upload(upload(data)).pages == 1


@pytest.mark.parametrize(
    ("prepare", "message"),
    [
        (lambda w: w.add_open_action(JavaScript("app.alert('x');")), "JavaScript"),
        (lambda w: w.add_attachment("faktura.exe", b"MZ"), "załączniki"),
        (launch_on_open, "uruchamianie programów"),
        (lambda w: w.encrypt("haslo"), "zaszyfrowany"),
    ],
)
def test_active_or_protected_pdf_is_rejected(prepare, message):
    with pytest.raises(UploadError, match=message):
        check_upload(upload(make_pdf(prepare=prepare)))


def test_pdf_limits_and_broken_files(settings):
    settings.UPLOAD_MAX_PDF_PAGES = 2
    with pytest.raises(UploadError, match="za dużo stron"):
        check_upload(upload(make_pdf(pages=3)))
    with pytest.raises(UploadError):
        check_upload(upload(b"%PDF-1.7\nto nie jest pdf\n%%EOF"))
    with pytest.raises(UploadError, match="uszkodzony"):
        check_upload(upload(b"zwykly tekst"))
