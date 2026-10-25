"""
Obróbka pliku plakatu. Każdy etap ma interfejs (Protocol) i implementację.
Implementacje demo mają w nazwie `Mock` i są oznaczane etykietą "symulacja" w UI.

Etapy: ocena per format (prawdziwa) -> upscale (mock) -> kadr (prawdziwy)
-> PDF ze spadami (prawdziwy, uproszczony) -> mockup (podgląd).
Wgranie, podgląd, analiza treści i zapis wersji są w rdzeniu (`apps.assets.services`).
"""

import io
import logging
import math
import random
import time
from typing import Protocol

from django.conf import settings
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

from apps.assets.models import Asset, AssetAnalysis, AssetVersion
from apps.assets.services import save_version

from . import quality
from .models import Format, PosterSettings
from .options import format_of
from .pricing import active_formats

log = logging.getLogger("slop.poster")

FRAGMENT_SOURCE_PX = 75  # fragment 1:1 pobierany z oryginału; x4 daje 300 px
WHITE_MARGIN_MM = 15  # [PROPOZYCJA]


# ---------------------------------------------------------------------------
# Ocena jakości
# ---------------------------------------------------------------------------


def analyze_formats(asset: Asset) -> AssetAnalysis:
    """Werdykt dla każdego aktywnego formatu, zapisany w `AssetAnalysis.quality_per_format`."""
    poster_settings = PosterSettings.get()
    per_format = {
        fmt.code: quality.quality_for(asset.width, asset.height, fmt, poster_settings).as_dict()
        for fmt in active_formats()
    }
    # Aktualizacja na instancji z `asset.analysis`, żeby wywołujący nie trzymał starej kopii.
    analysis = getattr(asset, "analysis", None)
    if analysis is None:
        analysis = AssetAnalysis(asset=asset, aspect_ratio=round(asset.aspect_ratio, 4))
    analysis.quality_per_format = per_format
    analysis.save()
    return analysis


def quality_for_selection(asset: Asset, fmt: Format, crop: dict | None) -> quality.Quality:
    """Ocena dla konkretnego kadru (np. po przybliżeniu w S2)."""
    return quality.quality_for(asset.width, asset.height, fmt, PosterSettings.get(), crop)


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


def _vgradient(w, h, top, bottom):
    """Pionowy gradient RGB od `top` do `bottom`."""
    column = Image.new("RGB", (1, h))
    span = max(h - 1, 1)
    column.putdata(
        [
            tuple(round(a + (b - a) * y / span) for a, b in zip(top, bottom, strict=True))
            for y in range(h)
        ]
    )
    return column.resize((w, h))


def _tint(scene, mask, color, strength):
    """Nakłada kolor przez maskę (L) z siłą 0-1: cienie i poświaty."""
    alpha = mask.point(lambda v: int(v * strength))
    scene.paste(Image.new("RGB", scene.size, color), (0, 0), alpha)


def _leaf(x, y, length, width, angle):
    """Liść jak soczewka, od nasady (x, y) pod kątem w stopniach (0 = w prawo, 90 = w górę)."""
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    n = 14
    side = [(i / n * length, math.sin(math.pi * i / n) * width / 2) for i in range(n + 1)]
    outline = side + [(px, -py) for px, py in reversed(side)]
    return [(x + px * ca - py * sa, y - (px * sa + py * ca)) for px, py in outline]


class MockupService:
    """
    Montaż w skali na rysowanej ścianie (bez zdjęć z sieci): ściana 400 x 280 cm, sofa 200 cm,
    roślina i lampa jako odniesienie wielkości, pasek skali 1 m. Tylko do podglądu, nie do druku.
    Pokój rysujemy w podwójnej rozdzielczości i zmniejszamy (gładkie krawędzie bez antyaliasingu
    w ImageDraw), plakat, jego cień i opisy już w docelowej.
    """

    name = "MockupService"
    PX_PER_CM = 3
    SUPERSAMPLE = 2
    WALL_W_CM = 400
    WALL_H_CM = 280
    FLOOR_H_CM = 40
    SOFA_W_CM = 200
    SOFA_H_CM = 85
    POSTER_BOTTOM_CM = 150  # dolna krawędź plakatu, liczona od dołu obrazu

    WALL = ((238, 232, 223), (226, 218, 206))
    BASEBOARD = (246, 242, 236)
    FLOOR = ((200, 172, 140), (176, 145, 112))
    SOFA = {
        "back": (132, 143, 125),
        "base": (124, 135, 117),
        "cushion": (146, 157, 139),
        "seat": (160, 170, 152),
    }
    PILLOWS = ((197, 136, 99), (236, 227, 212))
    LEAVES = ((86, 112, 80), (106, 134, 96))
    POT = ((233, 225, 213), (209, 199, 186))
    WOOD = (92, 72, 56)
    INK = (118, 108, 97)

    def render(
        self, image: Image.Image, fmt: Format, orientation: str, white_margin: bool
    ) -> Image.Image:
        s = self.PX_PER_CM
        W, H = self.WALL_W_CM * s, self.WALL_H_CM * s
        scene = self._background(W, H)

        pw_mm, ph_mm = fmt.dims_mm(orientation)
        pw, ph = round(pw_mm / 10 * s), round(ph_mm / 10 * s)
        px0 = (W - pw) // 2
        py1 = H - self.POSTER_BOTTOM_CM * s
        py0 = py1 - ph

        # Cień plakatu: miękki, lekko w dół, jak od lampy z góry.
        shadow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(shadow).rectangle([px0 + 2, py0 + 6, px0 + pw + 2, py1 + 10], fill=255)
        _tint(scene, shadow.filter(ImageFilter.GaussianBlur(9)), (60, 45, 30), 0.38)

        poster = Image.new("RGB", (pw, ph), (255, 255, 255))
        if white_margin:
            m = round(WHITE_MARGIN_MM / 10 * s)
            inner = ImageOps.contain(image, (pw - 2 * m, ph - 2 * m), Image.LANCZOS)
            poster.paste(inner, ((pw - inner.width) // 2, (ph - inner.height) // 2))
        else:
            poster = ImageOps.fit(image, (pw, ph), Image.LANCZOS)
        scene.paste(poster, (px0, py0))

        d = ImageDraw.Draw(scene)
        d.rectangle([px0, py0, px0 + pw - 1, py1 - 1], outline=(206, 198, 188), width=1)
        # „x”, nie „×”: wbudowana czcionka Pillow nie ma znaku mnożenia
        label = f"{pw_mm / 10:g} x {ph_mm / 10:g} cm".replace(".", ",")
        self._annotate(d, s, W, H, (px0, py1, pw), label)
        return scene

    _rooms: dict = {}  # pokój zależy tylko od stałych klasy: rysujemy go raz na proces

    def _background(self, W, H):
        key = (type(self), W, H)
        if key not in self._rooms:
            room = self._room(self.PX_PER_CM * self.SUPERSAMPLE)
            self._rooms[key] = room.resize((W, H), Image.LANCZOS)
        return self._rooms[key].copy()

    def _room(self, s):
        """Ściana, podłoga, sofa, roślina i lampa w skali `s` px/cm."""
        W, H = self.WALL_W_CM * s, self.WALL_H_CM * s
        floor_top = H - self.FLOOR_H_CM * s
        contact = floor_top + 8 * s  # gdzie meble stoją na podłodze (lekka perspektywa)
        room = Image.new("RGB", (W, H))
        room.paste(_vgradient(W, floor_top, *self.WALL), (0, 0))
        room.paste(_vgradient(W, H - floor_top, *self.FLOOR), (0, floor_top))
        d = ImageDraw.Draw(room)

        # Deski: linie gęstsze przy ścianie, rzadsze bliżej widza.
        for cm in (5, 11, 19, 29):
            y = floor_top + cm * s
            d.line([0, y, W, y], fill=(168, 138, 106), width=max(s // 3, 1))
        d.rectangle([0, floor_top - 7 * s, W, floor_top], fill=self.BASEBOARD)
        d.line([0, floor_top - 7 * s, W, floor_top - 7 * s], fill=(222, 214, 203), width=s // 2)

        sofa_x0 = (W - self.SOFA_W_CM * s) // 2
        sofa_x1 = sofa_x0 + self.SOFA_W_CM * s
        lamp_x = sofa_x1 + 38 * s

        # Światło: jaśniejsza plama na ścianie i ciepła poświata wokół klosza lampy. Maski
        # rysujemy w skali 1 px = 2 cm i rozmywamy tam, bo duże rozmycie w pełnym rozmiarze kosztuje.
        q = 2 * s
        light = Image.new("L", (W // q, H // q), 0)
        ImageDraw.Draw(light).ellipse([30, -50, 160, 70], fill=255)  # w jednostkach po 2 cm
        _tint(
            room, light.filter(ImageFilter.GaussianBlur(30)).resize((W, H)), (252, 249, 244), 0.55
        )
        warm = Image.new("L", (W // q, H // q), 0)
        lx, ly = lamp_x // q, (contact - 154 * s) // q
        ImageDraw.Draw(warm).ellipse([lx - 14, ly - 18, lx + 14, ly + 22], fill=255)
        warm = warm.filter(ImageFilter.GaussianBlur(14)).resize((W, H))
        _tint(room, warm, (255, 208, 140), 0.5)

        # Cienie na podłodze pod meblami.
        shadow = Image.new("L", (W, H), 0)
        sd = ImageDraw.Draw(shadow)
        sd.ellipse([sofa_x0 - 4 * s, contact - 4 * s, sofa_x1 + 4 * s, contact + 5 * s], fill=255)
        sd.ellipse([22 * s, contact - 3 * s, 70 * s, contact + 4 * s], fill=255)
        sd.ellipse([lamp_x - 16 * s, contact - 2 * s, lamp_x + 16 * s, contact + 3 * s], fill=255)
        _tint(room, shadow.filter(ImageFilter.GaussianBlur(3 * s)), (70, 50, 34), 0.45)

        self._sofa(room, s, sofa_x0, contact)
        self._plant(d, s, 46 * s, contact)
        self._lamp(d, s, lamp_x, contact)
        return room

    def _sofa(self, room, s, x0, contact):
        """Od tyłu: nóżki, oparcie z poduchami, poduszki, siedzisko, podłokietniki."""
        d = ImageDraw.Draw(room)
        c = self.SOFA
        w = self.SOFA_W_CM * s
        top = contact - self.SOFA_H_CM * s
        seat = contact - 40 * s  # wierzch siedziska
        legs = contact - 10 * s
        arm = 18 * s
        half = (w - 2 * arm) // 2  # szerokość jednej poduchy
        for lx in (x0 + 10 * s, x0 + w - 14 * s):
            d.rounded_rectangle([lx, legs, lx + 4 * s, contact], s, fill=self.WOOD)
        d.rounded_rectangle([x0 + 8 * s, top, x0 + w - 8 * s, seat], 10 * s, fill=c["back"])
        for i in range(2):
            cx = x0 + arm + i * half
            box = [cx + 2 * s, top + 6 * s, cx + half - 2 * s, seat]
            d.rounded_rectangle(box, 8 * s, fill=c["cushion"])
        pillows = (
            (self.PILLOWS[0], x0 + arm + 8 * s, 8),
            (self.PILLOWS[1], x0 + w - arm - 44 * s, -7),
        )
        for color, px, angle in pillows:
            size = 36 * s
            pad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(pad).rounded_rectangle([0, 0, size - 1, size - 1], 9 * s, fill=color)
            pad = pad.rotate(angle, resample=Image.BICUBIC, expand=True)
            room.paste(pad, (px, seat - pad.height + 3 * s), pad)
        d.rounded_rectangle([x0 + 4 * s, seat - 2 * s, x0 + w - 4 * s, legs], 6 * s, fill=c["base"])
        for i in range(2):
            cx = x0 + arm + i * half
            d.rounded_rectangle(
                [cx + s, seat - 6 * s, cx + half - s, seat + 6 * s], 5 * s, fill=c["seat"]
            )
        for ax in (x0, x0 + w - arm):
            d.rounded_rectangle([ax, top + 26 * s, ax + arm, legs], 9 * s, fill=c["base"])
            cap = [ax + 2 * s, top + 26 * s, ax + arm - 2 * s, top + 36 * s]
            d.rounded_rectangle(cap, 7 * s, fill=c["cushion"])

    def _plant(self, d, s, cx, contact):
        pot_h, top_w, bottom_w = 38 * s, 32 * s, 24 * s
        rim = contact - pot_h
        leaves = (
            (62, 95, 16),
            (74, 118, 18),
            (66, 72, 15),
            (58, 140, 14),
            (70, 52, 13),
            (50, 160, 12),
            (56, 30, 11),
            (80, 100, 17),
            (60, 84, 14),
        )  # (długość cm, kąt, szerokość cm)
        for i, (length, angle, width) in enumerate(leaves):
            d.polygon(_leaf(cx, rim + 2 * s, length * s, width * s, angle), fill=self.LEAVES[i % 2])
        body = [
            (cx - top_w / 2, rim),
            (cx + top_w / 2, rim),
            (cx + bottom_w / 2, contact),
            (cx - bottom_w / 2, contact),
        ]
        d.polygon(body, fill=self.POT[0])
        d.polygon([(cx + 4 * s, rim), body[1], body[2], (cx + 3 * s, contact)], fill=self.POT[1])
        d.rounded_rectangle(
            [cx - top_w / 2 - s, rim - 3 * s, cx + top_w / 2 + s, rim + 2 * s], s, fill=self.POT[0]
        )

    def _lamp(self, d, s, cx, contact):
        shade_bottom = contact - 140 * s
        d.ellipse([cx - 13 * s, contact - 3 * s, cx + 13 * s, contact + 1 * s], fill=self.WOOD)
        d.rectangle([cx - s, shade_bottom, cx + s, contact - 2 * s], fill=self.WOOD)
        d.polygon(
            [
                (cx - 13 * s, shade_bottom - 28 * s),
                (cx + 13 * s, shade_bottom - 28 * s),
                (cx + 20 * s, shade_bottom),
                (cx - 20 * s, shade_bottom),
            ],
            fill=(246, 236, 218),
        )

    def _annotate(self, d, s, W, H, poster, label):
        """Pasek skali, wymiar plakatu i szerokość sofy, jak na rysunku technicznym."""
        font = _font(7 * s)
        px0, py1, pw = poster
        x0, y = 20 * s, 18 * s
        d.line([x0, y, x0 + 100 * s, y], fill=self.INK, width=max(s // 2, 1))
        for x in (x0, x0 + 100 * s):
            d.line([x, y - 2 * s, x, y + 2 * s], fill=self.INK, width=max(s // 2, 1))
        d.text((x0 + 50 * s, y - 3 * s), "1 m", fill=self.INK, font=font, anchor="md")
        d.text((px0 + pw // 2, py1 + 5 * s), label, fill=self.INK, font=font, anchor="mt")
        sofa_x0 = (W - self.SOFA_W_CM * s) // 2
        sofa_x1 = sofa_x0 + self.SOFA_W_CM * s
        y = H - 14 * s
        text = "200 cm"
        tw = d.textlength(text, font=font)
        mid = (sofa_x0 + sofa_x1) // 2
        for a, b in ((sofa_x0, mid - tw / 2 - 3 * s), (mid + tw / 2 + 3 * s, sofa_x1)):
            d.line([a, y, b, y], fill=self.INK, width=max(s // 2, 1))
        for x in (sofa_x0, sofa_x1):
            d.line([x, y - 2 * s, x, y + 2 * s], fill=self.INK, width=max(s // 2, 1))
        d.text((mid, y), text, fill=self.INK, font=font, anchor="mm")


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
    options = order_item.options
    fmt = format_of(options)
    white_margin = bool(options.get("white_margin"))
    poster_settings = PosterSettings.get()
    upscaler = upscaler or MockUpscale()
    crop = quality.sanitize_crop(options.get("crop"), asset.width, asset.height, fmt)

    img = asset.open_image()
    provider_upscale = ""
    if options.get("enhancer"):
        img = upscaler.upscale(img, poster_settings.upscale_factor)
        provider_upscale = upscaler.name
        save_version(
            asset,
            AssetVersion.Kind.UPSCALED,
            img,
            provider=upscaler.name,
            params={"factor": poster_settings.upscale_factor},
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
        white_margin,
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
            "white_margin": white_margin,
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
