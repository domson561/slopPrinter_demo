"""
Serwisy pipeline'u pliku. Każdy etap ma interfejs (Protocol) i implementację.
Implementacje demo mają w nazwie `Mock` i są oznaczane etykietą "symulacja" w UI.

Etapy: analiza (prawdziwa) -> moderacja (mock) -> upscale (mock) -> kadr (prawdziwy)
-> PDF ze spadami (prawdziwy, uproszczony) -> mockup (podgląd).
"""

import hashlib
import io
import logging
import random
import time
from datetime import timedelta
from typing import Protocol

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

from apps.catalog.models import Format, ShopSettings
from apps.catalog.services import active_formats

from . import quality
from .models import Asset, AssetAnalysis, AssetVersion

log = logging.getLogger("slop.assets")

PREVIEW_MAX_PX = 1600
FRAGMENT_SOURCE_PX = 75  # fragment 1:1 pobierany z oryginału; x4 daje 300 px
WHITE_MARGIN_MM = 15  # [PROPOZYCJA]


class UploadError(Exception):
    """Błąd walidacji wgranego pliku, komunikat po polsku dla klienta."""


# ---------------------------------------------------------------------------
# Upload i analiza
# ---------------------------------------------------------------------------

_MIME_BY_PIL_FORMAT = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_EXT_BY_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def create_asset_from_upload(uploaded_file) -> Asset:
    """Waliduje plik po stronie serwera, zapisuje Asset, podgląd i analizę."""
    if uploaded_file.size > settings.UPLOAD_MAX_BYTES:
        limit_mb = settings.UPLOAD_MAX_BYTES // (1024 * 1024)
        raise UploadError(f"Plik jest za duży. Maksymalny rozmiar to {limit_mb} MB.")

    data = uploaded_file.read()
    try:
        img = Image.open(io.BytesIO(data))
        pil_format = img.format
        img.load()
    except Image.DecompressionBombError as exc:
        raise UploadError(
            "Obraz ma za dużo pikseli, żebyśmy mogli go bezpiecznie otworzyć."
        ) from exc
    except Exception as exc:
        raise UploadError("Nie udało się otworzyć obrazu. Plik może być uszkodzony.") from exc

    mime = _MIME_BY_PIL_FORMAT.get(pil_format)
    if mime not in settings.UPLOAD_ALLOWED_MIME:
        raise UploadError("Obsługujemy tylko pliki JPG, PNG i WebP.")

    img = ImageOps.exif_transpose(img)
    width, height = img.size
    if width < 64 or height < 64:
        raise UploadError("Obraz jest za mały (minimum 64 x 64 px).")

    sha = hashlib.sha256(data).hexdigest()
    shop = ShopSettings.get()

    with transaction.atomic():
        asset = Asset(
            original_filename=getattr(uploaded_file, "name", "")[:255],
            mime=mime,
            width=width,
            height=height,
            size_bytes=len(data),
            sha256=sha,
            expires_at=timezone.now() + timedelta(days=shop.draft_retention_days),
        )
        asset.original_file.save(f"upload{_EXT_BY_MIME[mime]}", ContentFile(data), save=False)
        asset.save()
        _save_preview(asset, img)
        AnalysisService().analyze(asset, image=img)
    return asset


def _save_preview(asset: Asset, img: Image.Image) -> AssetVersion:
    preview = img.convert("RGB")
    preview.thumbnail((PREVIEW_MAX_PX, PREVIEW_MAX_PX), Image.LANCZOS)
    return save_version(asset, AssetVersion.Kind.PREVIEW, preview, provider="Pillow", fmt="JPEG")


def save_version(
    asset,
    kind,
    image=None,
    *,
    provider="",
    params=None,
    fmt="JPEG",
    order_item=None,
    pdf_bytes=None,
):
    """Zapisuje AssetVersion. Nadpisuje poprzednie wersje tego rodzaju (pipeline jest idempotentny)."""
    old = asset.versions.filter(kind=kind, order_item=order_item)
    for v in old:
        v.file.delete(save=False)
    old.delete()

    version = AssetVersion(
        asset=asset, kind=kind, provider=provider, params=params or {}, order_item=order_item
    )
    if pdf_bytes is not None:
        version.file.save("print.pdf", ContentFile(pdf_bytes), save=False)
    else:
        buf = io.BytesIO()
        if fmt == "JPEG":
            image.convert("RGB").save(buf, "JPEG", quality=92, optimize=True)
            ext = ".jpg"
        else:
            image.save(buf, "PNG", optimize=True)
            ext = ".png"
        version.width, version.height = image.size
        version.file.save(f"{kind}{ext}", ContentFile(buf.getvalue()), save=False)
    version.save()
    return version


class ContentSafetyService(Protocol):
    def check(self, asset: Asset, image: Image.Image) -> list[str]: ...


class MockContentSafety:
    """
    Symulacja moderacji. Docelowo: zewnętrzny klasyfikator NSFW + wykrywanie znanych IP.
    Flaguje pliki, których nazwa zawiera `nsfw`, `logo` lub `brand` (do pokazania ścieżki on_hold).
    """

    name = "MockContentSafety"

    def check(self, asset, image=None):
        name = (asset.original_filename or "").lower()
        flags = []
        if "nsfw" in name:
            flags.append("nsfw_suspected")
        if "logo" in name or "brand" in name:
            flags.append("trademark_suspected")
        return flags


FLAG_LABELS = {
    "nsfw_suspected": "Podejrzenie treści dla dorosłych",
    "trademark_suspected": "Podejrzenie cudzego znaku towarowego / logo",
    "manual": "Oznaczone ręcznie przez operatora",
}


class AnalysisService:
    """Prawdziwa analiza na Pillow: wymiary, proporcje, DPI i werdykt dla każdego formatu."""

    def __init__(self, content_safety: ContentSafetyService | None = None):
        self.content_safety = content_safety or MockContentSafety()

    def analyze(self, asset: Asset, image: Image.Image | None = None) -> AssetAnalysis:
        shop = ShopSettings.get()
        per_format = {}
        for fmt in active_formats():
            per_format[fmt.code] = quality.quality_for(
                asset.width, asset.height, fmt, shop
            ).as_dict()
        flags = self.content_safety.check(asset, image)
        analysis, _ = AssetAnalysis.objects.update_or_create(
            asset=asset,
            defaults={
                "aspect_ratio": round(asset.aspect_ratio, 4),
                "content_flags": flags,
                "quality_per_format": per_format,
            },
        )
        return analysis


def quality_for_selection(asset: Asset, fmt: Format, crop: dict | None) -> quality.Quality:
    """Ocena dla konkretnego kadru (np. po przybliżeniu w S2)."""
    return quality.quality_for(asset.width, asset.height, fmt, ShopSettings.get(), crop)


# ---------------------------------------------------------------------------
# Upscale / kadr / outpaint
# ---------------------------------------------------------------------------


class UpscaleService(Protocol):
    name: str

    def upscale(self, image: Image.Image, factor: int) -> Image.Image: ...


class MockUpscale:
    """
    Symulacja: resize LANCZOS x factor z lekkim wyostrzeniem i sztucznym opóźnieniem.
    Docelowo: model klasy Real-ESRGAN przez zewnętrzne API.
    """

    name = "MockUpscale"

    def __init__(self, simulate_delay: bool = True):
        self.simulate_delay = simulate_delay

    def upscale(self, image, factor):
        if self.simulate_delay:
            lo, hi = settings.MOCK_UPSCALE_DELAY_SECONDS
            delay = random.uniform(lo, hi)
            if delay > 0:
                log.info("MockUpscale: symulowane opóźnienie %.1fs", delay)
                time.sleep(delay)
        w, h = image.size
        target = (w * factor, h * factor)
        if target[0] * target[1] > settings.PIL_MAX_IMAGE_PIXELS:
            scale = (settings.PIL_MAX_IMAGE_PIXELS / (w * h)) ** 0.5
            target = (int(w * scale), int(h * scale))
        out = image.resize(target, Image.LANCZOS)
        return out.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=2))


class CropService:
    @staticmethod
    def crop(image: Image.Image, crop: dict) -> Image.Image:
        box = quality.crop_box_px(crop, image.width, image.height)
        return image.crop(box)


class OutpaintService:
    """Dorysowanie brzegów. W demo tylko interfejs."""

    name = "OutpaintService"

    def outpaint(self, image: Image.Image, target_ratio: float) -> Image.Image:
        raise NotImplementedError("Outpainting nie jest częścią demo.")


# ---------------------------------------------------------------------------
# Prepress (PDF ze spadami)
# ---------------------------------------------------------------------------

MM_TO_PT = 72.0 / 25.4


class PrepressService:
    """
    Prawdziwa, uproszczona implementacja: strona = format + spady, obraz skalowany do pola
    ze spadem; przy białym marginesie obraz pomniejszony wewnątrz. W demo RGB [ZAŁOŻENIE: CMYK, ICC].
    """

    name = "PrepressService"

    def build_pdf(
        self,
        image: Image.Image,
        fmt: Format,
        orientation: str,
        white_margin: bool,
        meta: dict | None = None,
    ) -> bytes:
        trim_w_mm, trim_h_mm = fmt.dims_mm(orientation)
        bleed = fmt.bleed_mm
        page_w_pt = (trim_w_mm + 2 * bleed) * MM_TO_PT
        page_h_pt = (trim_h_mm + 2 * bleed) * MM_TO_PT

        buf = io.BytesIO()
        c = pdf_canvas.Canvas(buf, pagesize=(page_w_pt, page_h_pt))
        meta = meta or {}
        c.setTitle(meta.get("title", f"{fmt.code} poster"))
        c.setAuthor(settings.SITE_NAME)
        c.setSubject(meta.get("subject", "print file"))
        c.setKeywords(", ".join(f"{k}={v}" for k, v in meta.items()))

        reader = ImageReader(image.convert("RGB"))
        if white_margin:
            c.setFillColorRGB(1, 1, 1)
            c.rect(0, 0, page_w_pt, page_h_pt, stroke=0, fill=1)
            m = (bleed + WHITE_MARGIN_MM) * MM_TO_PT
            box_w = page_w_pt - 2 * m
            box_h = page_h_pt - 2 * m
            img_ratio = image.width / image.height
            box_ratio = box_w / box_h
            if img_ratio > box_ratio:
                draw_w, draw_h = box_w, box_w / img_ratio
            else:
                draw_w, draw_h = box_h * img_ratio, box_h
            x = (page_w_pt - draw_w) / 2
            y = (page_h_pt - draw_h) / 2
            c.drawImage(reader, x, y, draw_w, draw_h)
        else:
            c.drawImage(reader, 0, 0, page_w_pt, page_h_pt)

        # Znaczniki cięcia (proste kreski w rogach, poza polem ze spadem).
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(0.25)
        b = bleed * MM_TO_PT
        mark = min(b * 0.8, 6)
        if mark > 0:
            for x in (b, page_w_pt - b):
                c.line(x, 0, x, mark)
                c.line(x, page_h_pt, x, page_h_pt - mark)
            for y in (b, page_h_pt - b):
                c.line(0, y, mark, y)
                c.line(page_w_pt, y, page_w_pt - mark, y)

        c.showPage()
        c.save()
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Mockup i podgląd wycinka
# ---------------------------------------------------------------------------


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # starsze Pillow
        return ImageFont.load_default()


class MockupService:
    """
    Prosty montaż w skali na rysowanej ścianie (bez zdjęć z sieci):
    ściana 400 x 280 cm, sofa 200 cm jako odniesienie wielkości, pasek skali 1 m.
    Tylko do podglądu, nie do druku.
    """

    name = "MockupService"
    PX_PER_CM = 2
    WALL_W_CM = 400
    WALL_H_CM = 280
    FLOOR_H_CM = 40
    SOFA_W_CM = 200
    SOFA_H_CM = 85
    POSTER_BOTTOM_CM = 150  # wysokość dolnej krawędzi plakatu nad podłogą

    def render(
        self, image: Image.Image, fmt: Format, orientation: str, white_margin: bool
    ) -> Image.Image:
        s = self.PX_PER_CM
        W, H = self.WALL_W_CM * s, self.WALL_H_CM * s
        scene = Image.new("RGB", (W, H), (235, 232, 226))
        d = ImageDraw.Draw(scene)

        floor_top = H - self.FLOOR_H_CM * s
        d.rectangle([0, floor_top, W, H], fill=(196, 176, 150))
        d.line([0, floor_top, W, floor_top], fill=(150, 135, 115), width=2)

        sofa_x0 = (W - self.SOFA_W_CM * s) // 2
        sofa_y1 = floor_top + 6 * s
        sofa_y0 = sofa_y1 - self.SOFA_H_CM * s
        d.rectangle([sofa_x0, sofa_y0, sofa_x0 + self.SOFA_W_CM * s, sofa_y1], fill=(120, 130, 145))
        d.rectangle(
            [sofa_x0, sofa_y0 + 35 * s, sofa_x0 + self.SOFA_W_CM * s, sofa_y1], fill=(105, 115, 130)
        )
        d.text((sofa_x0 + 6, sofa_y1 - 16), "sofa 200 cm", fill=(240, 240, 240), font=_font(12))

        pw_mm, ph_mm = fmt.dims_mm(orientation)
        pw, ph = int(pw_mm / 10 * s), int(ph_mm / 10 * s)
        px0 = (W - pw) // 2
        py1 = H - self.POSTER_BOTTOM_CM * s
        py0 = py1 - ph

        d.rectangle([px0 + 4, py0 + 4, px0 + pw + 4, py1 + 4], fill=(200, 196, 190))  # cień
        poster = Image.new("RGB", (pw, ph), (255, 255, 255))
        if white_margin:
            m = int(WHITE_MARGIN_MM / 10 * s)
            inner = ImageOps.contain(image, (pw - 2 * m, ph - 2 * m), Image.LANCZOS)
            poster.paste(inner, ((pw - inner.width) // 2, (ph - inner.height) // 2))
        else:
            poster = ImageOps.fit(image, (pw, ph), Image.LANCZOS)
        scene.paste(poster, (px0, py0))
        d.rectangle([px0, py0, px0 + pw, py1], outline=(40, 40, 40), width=1)

        # Pasek skali 1 m.
        bar_y = 20
        d.line([20, bar_y, 20 + 100 * s, bar_y], fill=(60, 60, 60), width=2)
        d.line([20, bar_y - 5, 20, bar_y + 5], fill=(60, 60, 60), width=2)
        d.line([20 + 100 * s, bar_y - 5, 20 + 100 * s, bar_y + 5], fill=(60, 60, 60), width=2)
        d.text((24, bar_y + 6), "1 m", fill=(60, 60, 60), font=_font(12))
        label = f"{fmt.name} ({pw_mm} x {ph_mm} mm)"
        d.text((px0, py1 + 6), label, fill=(60, 60, 60), font=_font(12))
        d.text((W - 110, H - 20), "symulacja", fill=(120, 120, 120), font=_font(12))
        return scene


def render_fragment(asset: Asset, crop: dict, enhanced: bool, out_px: int = 300) -> Image.Image:
    """
    Wycinek 1:1 ze środka kadru. 'before' = piksele oryginału powiększone bez interpolacji
    (tak wyglądałby druk z za małego pliku), 'after' = wynik MockUpscale na tym samym wycinku.
    Tanie: pracuje na ~75 x 75 px oryginału.
    """
    img = asset.open_image()
    box = quality.crop_box_px(crop, img.width, img.height)
    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2
    half = FRAGMENT_SOURCE_PX // 2
    frag = img.crop(
        (
            max(cx - half, 0),
            max(cy - half, 0),
            min(cx + half, img.width),
            min(cy + half, img.height),
        )
    )
    factor = max(1, out_px // max(frag.width, 1))
    if enhanced:
        out = MockUpscale(simulate_delay=False).upscale(frag, factor)
    else:
        out = frag.resize((frag.width * factor, frag.height * factor), Image.NEAREST)
    return ImageOps.fit(out, (out_px, out_px), Image.NEAREST)


def render_mockup(asset: Asset, fmt: Format, crop: dict, white_margin: bool) -> Image.Image:
    preview = asset.preview
    src = Image.open(preview.file) if preview else asset.open_image()
    src.load()
    if src.mode != "RGB":
        src = src.convert("RGB")
    cropped = CropService.crop(src, crop)
    return MockupService().render(cropped, fmt, crop.get("orientation", "portrait"), white_margin)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_pipeline(order_item, upscaler: UpscaleService | None = None) -> AssetVersion:
    """
    upscale (jeśli włączony) -> kadr -> PDF. Każdy etap zapisuje AssetVersion.
    Idempotentny: ponowne uruchomienie nadpisuje wersje danego rodzaju dla tej pozycji.
    Wyjątki propagują do wywołującego (orders.tasks), który przestawia zamówienie na on_hold.
    """
    asset = order_item.asset
    fmt = order_item.format
    shop = ShopSettings.get()
    upscaler = upscaler or MockUpscale()
    crop = quality.sanitize_crop(order_item.crop, asset.width, asset.height, fmt)

    img = asset.open_image()
    provider_upscale = ""
    if order_item.enhancer_enabled:
        img = upscaler.upscale(img, shop.upscale_factor)
        provider_upscale = upscaler.name
        save_version(
            asset,
            AssetVersion.Kind.UPSCALED,
            img,
            provider=upscaler.name,
            params={"factor": shop.upscale_factor},
            order_item=order_item,
        )

    cropped = CropService.crop(img, crop)
    dpi = quality.effective_dpi(
        cropped.width, cropped.height, {"w": 1, "h": 1, "orientation": crop["orientation"]}, fmt
    )
    save_version(
        asset,
        AssetVersion.Kind.CROPPED,
        cropped,
        provider="CropService",
        params={"crop": crop, "effective_dpi": round(dpi, 1), "upscaled_by": provider_upscale},
        order_item=order_item,
    )

    pdf_bytes = PrepressService().build_pdf(
        cropped,
        fmt,
        crop["orientation"],
        order_item.white_margin,
        meta={
            "title": f"{order_item.order.number} {fmt.code}",
            "subject": f"order={order_item.order.number} item={order_item.pk}",
            "format": fmt.code,
            "bleed_mm": fmt.bleed_mm,
            "effective_dpi": round(dpi, 1),
            "color_space": "RGB",
        },
    )
    version = save_version(
        asset,
        AssetVersion.Kind.PRINT_PDF,
        provider="PrepressService",
        params={
            "format": fmt.code,
            "orientation": crop["orientation"],
            "white_margin": order_item.white_margin,
            "bleed_mm": fmt.bleed_mm,
            "effective_dpi": round(dpi, 1),
            "color_space": "RGB",
            "px": [cropped.width, cropped.height],
        },
        order_item=order_item,
        pdf_bytes=pdf_bytes,
    )
    version.width, version.height = cropped.size
    version.save(update_fields=["width", "height"])
    return version
