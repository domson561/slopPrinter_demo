"""
Przewoźnik za wymiennym interfejsem, jak płatności. Sklep zakłada przesyłkę, zanim wyśle
zlecenie do GH: numer przesyłki i PDF etykiety powstają tutaj, GH dostaje etykietę jako plik
i tylko ją nakleja (kontrakt pkt 1, 4 i 5). Dane kupującego są wyłącznie w tym PDF-ie.
W demo `MockCarrierProvider`; prawdziwy kurier albo pośrednik (InPost, DPD, Furgonetka…)
to osobna klasa wskazana w `CARRIER_PROVIDER`.
"""

import functools
import io
import logging
import os
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.module_loading import import_string
from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .models import Order, Shipment

log = logging.getLogger("slop.orders")


class CarrierError(Exception):
    """Przewoźnik nie założył albo nie anulował przesyłki."""


@dataclass
class LabelResult:
    carrier: str  # nazwa dla kupującego, np. „InPost”
    tracking_number: str
    external_id: str  # identyfikator u przewoźnika, potrzebny do anulowania
    pdf: bytes


class CarrierProvider(Protocol):
    name: str

    def create_label(self, order: Order) -> LabelResult: ...

    def cancel_label(self, shipment: Shipment) -> None: ...


def get_carrier_provider() -> CarrierProvider:
    return import_string(settings.CARRIER_PROVIDER)()


# ---------------------------------------------------------------------------
# Przesyłki
# ---------------------------------------------------------------------------


def create_shipment(order: Order, provider: CarrierProvider | None = None) -> Shipment:
    """Zakłada przesyłkę u przewoźnika i zapisuje etykietę w prywatnym katalogu."""
    if not order.full_name or not order.shipping_address:
        raise CarrierError("brak danych odbiorcy")
    provider = provider or get_carrier_provider()
    result = provider.create_label(order)
    if not result.tracking_number or not result.pdf.startswith(b"%PDF"):
        raise CarrierError(f"{provider.name}: brak numeru przesyłki albo etykieta nie jest PDF-em")
    shipment = Shipment(
        order=order,
        provider=provider.name,
        carrier=result.carrier,
        tracking_number=result.tracking_number,
        external_id=result.external_id,
    )
    shipment.label.save("label.pdf", ContentFile(result.pdf), save=False)
    shipment.save()
    log.info("order %s: przesyłka %s (%s)", order.number, shipment.tracking_number, provider.name)
    return shipment


def cancel_shipment(shipment: Shipment, provider: CarrierProvider | None = None) -> None:
    """Anuluje niewykorzystaną etykietę u przewoźnika. Nadanej paczki to nie dotyczy."""
    if shipment.status != Shipment.Status.CREATED:
        return
    provider = provider or get_carrier_provider()
    provider.cancel_label(shipment)
    shipment.status = Shipment.Status.CANCELLED
    shipment.cancelled_at = timezone.now()
    shipment.save(update_fields=["status", "cancelled_at"])


def mark_shipped(shipment: Shipment, shipped_at=None) -> None:
    """GH zgłosiło wysyłkę paczki z tą etykietą. Ponowne zgłoszenie nic nie zmienia."""
    if shipment.status == Shipment.Status.SHIPPED:
        return
    shipment.status = Shipment.Status.SHIPPED
    shipment.shipped_at = shipped_at or timezone.now()
    shipment.save(update_fields=["status", "shipped_at"])


# ---------------------------------------------------------------------------
# Atrapa kuriera
# ---------------------------------------------------------------------------


class MockCarrierProvider:
    """Atrapa kuriera: losowy numer przesyłki i etykieta A6 z kodem kreskowym, bez sieci."""

    name = "MockCarrierProvider"
    carrier = "Kurier (symulacja)"

    def create_label(self, order: Order) -> LabelResult:
        tracking = f"MOCK{secrets.randbelow(10**12):012d}"
        return LabelResult(
            carrier=self.carrier,
            tracking_number=tracking,
            external_id=f"mockship_{tracking}",
            pdf=render_mock_label(order, tracking, self.carrier),
        )

    def cancel_label(self, shipment: Shipment) -> None:
        """Atrapa niczego nie rezerwuje; prawdziwy przewoźnik zwalnia tu opłatę za etykietę."""


# Czcionki z polskimi znakami, jeśli są w systemie (pakiet DejaVu w Linuksie, Windows).
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


@functools.cache
def _label_font() -> str | None:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont("LabelSans", path))
            return "LabelSans"
    return None


def _plain(text: str) -> str:
    """Tekst bez ogonków dla wbudowanej Helvetiki, która nie ma ą, ę, ł…"""
    text = text.replace("ł", "l").replace("Ł", "L")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def render_mock_label(order: Order, tracking: str, carrier: str) -> bytes:
    """Etykieta A6: przewoźnik, odbiorca, nadawca, numer przesyłki z kodem Code 128."""
    font = _label_font()
    face = font or "Helvetica"
    width, height = A6
    margin = 7 * mm
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A6)
    pdf.setTitle(f"Etykieta {tracking}")
    y = height - margin

    def line(text, size=10, space_before=0):
        nonlocal y
        text = text if font else _plain(text)
        while size > 6 and pdf.stringWidth(text, face, size) > width - 2 * margin:
            size -= 0.5
        y -= space_before + size * 1.35
        pdf.setFont(face, size)
        pdf.drawString(margin, y, text)

    line(carrier, 14)
    line("SYMULACJA, nie nadawać", 8)
    service = order.delivery_method.name if order.delivery_method else "-"
    line(f"Usługa: {service}", 9)
    line(f"Data: {timezone.localdate():%Y-%m-%d}", 9)
    line("ODBIORCA", 8, space_before=4 * mm)
    line(order.full_name, 12)
    for text in order.shipping_address_lines:
        line(text, 11)
    if order.phone:
        line(f"tel. {order.phone}", 10)
    line("NADAWCA", 8, space_before=4 * mm)
    line(settings.SITE_NAME, 10)
    line(f"Zamówienie {order.number}", 9)

    barcode = code128.Code128(tracking, barHeight=18 * mm, barWidth=0.33 * mm)
    barcode.drawOn(pdf, (width - barcode.width) / 2, margin + 6 * mm)
    pdf.setFont(face, 11)
    pdf.drawCentredString(width / 2, margin, tracking)
    pdf.showPage()
    pdf.save()
    return buf.getvalue()
