"""
Czysta logika oceny jakości: kadr, efektywne DPI, werdykty.
Bez dostępu do bazy poza obiektami przekazanymi jako argumenty.
"""

from dataclasses import asdict, dataclass

MM_PER_INCH = 25.4


class Verdict:
    SHARP = "sharp"
    OK = "ok"
    NEEDS_ENHANCE = "needs_enhance"
    TOO_WEAK = "too_weak"

    LABELS = {
        SHARP: "ostry",
        OK: "ok",
        NEEDS_ENHANCE: "wymaga poprawy",
        TOO_WEAK: "za słaby",
    }

    # Opisy dla klienta (bez żargonu). {fmt} = nazwa formatu.
    CUSTOMER_TEXT = {
        SHARP: "Wystarczająco ostry do {fmt}.",
        OK: "W porządku do {fmt}, drobne szczegóły mogą być lekko miękkie.",
        NEEDS_ENHANCE: "Za mało szczegółów do {fmt}. Włącz poprawę jakości, żeby wydruk był ostry.",
        TOO_WEAK: "Ten plik jest za mały na {fmt}, nawet po poprawie. Wybierz mniejszy format.",
    }

    @classmethod
    def label(cls, code):
        return cls.LABELS.get(code, code)


def normalize_orientation(value, image_width, image_height):
    if value in ("portrait", "landscape"):
        return value
    return "landscape" if image_width > image_height else "portrait"


def default_crop(image_width, image_height, fmt, orientation=None) -> dict:
    """
    Największy wyśrodkowany kadr o proporcji formatu.
    Wynik znormalizowany do [0, 1] względem oryginału.
    """
    orientation = normalize_orientation(orientation, image_width, image_height)
    fw, fh = fmt.dims_mm(orientation)
    target = fw / fh
    img_ratio = image_width / image_height
    if img_ratio > target:
        w = target / img_ratio
        h = 1.0
        x = (1.0 - w) / 2.0
        y = 0.0
    else:
        w = 1.0
        h = img_ratio / target
        x = 0.0
        y = (1.0 - h) / 2.0
    return {
        "x": round(x, 6),
        "y": round(y, 6),
        "w": round(w, 6),
        "h": round(h, 6),
        "orientation": orientation,
    }


def sanitize_crop(crop, image_width, image_height, fmt) -> dict:
    """Przycina kadr z formularza do sensownych granic; przy śmieciach wraca domyślny."""
    if not isinstance(crop, dict):
        return default_crop(image_width, image_height, fmt)
    try:
        x = float(crop.get("x", 0))
        y = float(crop.get("y", 0))
        w = float(crop.get("w", 1))
        h = float(crop.get("h", 1))
    except (TypeError, ValueError):
        return default_crop(image_width, image_height, fmt)
    orientation = normalize_orientation(crop.get("orientation"), image_width, image_height)
    if w <= 0.01 or h <= 0.01 or w > 1.0001 or h > 1.0001:
        return default_crop(image_width, image_height, fmt, orientation)
    w = min(w, 1.0)
    h = min(h, 1.0)
    x = min(max(x, 0.0), 1.0 - w)
    y = min(max(y, 0.0), 1.0 - h)
    return {
        "x": round(x, 6),
        "y": round(y, 6),
        "w": round(w, 6),
        "h": round(h, 6),
        "orientation": orientation,
    }


def crop_box_px(crop, image_width, image_height) -> tuple[int, int, int, int]:
    left = int(round(crop["x"] * image_width))
    top = int(round(crop["y"] * image_height))
    right = int(round((crop["x"] + crop["w"]) * image_width))
    bottom = int(round((crop["y"] + crop["h"]) * image_height))
    right = max(min(right, image_width), left + 1)
    bottom = max(min(bottom, image_height), top + 1)
    return left, top, right, bottom


def effective_dpi(image_width, image_height, crop, fmt) -> float:
    """
    effective_dpi = min(width_px / (width_mm / 25.4), height_px / (height_mm / 25.4))
    liczone dla pikseli, które faktycznie wejdą w kadr.
    """
    fw, fh = fmt.dims_mm(crop.get("orientation", "portrait"))
    crop_w_px = crop["w"] * image_width
    crop_h_px = crop["h"] * image_height
    return min(crop_w_px / (fw / MM_PER_INCH), crop_h_px / (fh / MM_PER_INCH))


def verdict_for_dpi(dpi: float, shop_settings) -> str:
    if dpi >= shop_settings.dpi_sharp:
        return Verdict.SHARP
    if dpi >= shop_settings.dpi_ok:
        return Verdict.OK
    if dpi * max(shop_settings.upscale_factor, 1) >= shop_settings.dpi_ok:
        return Verdict.NEEDS_ENHANCE
    return Verdict.TOO_WEAK


@dataclass
class Quality:
    format_code: str
    format_name: str
    orientation: str
    effective_dpi: float
    dpi_after_upscale: float
    verdict: str
    verdict_label: str
    customer_text: str
    enhancer_recommended: bool
    blocked: bool

    def as_dict(self):
        return asdict(self)


def quality_for(image_width, image_height, fmt, shop_settings, crop=None) -> Quality:
    crop = sanitize_crop(crop, image_width, image_height, fmt) if crop else default_crop(
        image_width, image_height, fmt
    )
    dpi = effective_dpi(image_width, image_height, crop, fmt)
    verdict = verdict_for_dpi(dpi, shop_settings)
    return Quality(
        format_code=fmt.code,
        format_name=fmt.name,
        orientation=crop["orientation"],
        effective_dpi=round(dpi, 1),
        dpi_after_upscale=round(dpi * max(shop_settings.upscale_factor, 1), 1),
        verdict=verdict,
        verdict_label=Verdict.label(verdict),
        customer_text=Verdict.CUSTOMER_TEXT[verdict].format(fmt=fmt.name),
        enhancer_recommended=verdict == Verdict.NEEDS_ENHANCE,
        blocked=verdict == Verdict.TOO_WEAK,
    )


def suggest_smaller_format(fmt, formats, image_width, image_height, shop_settings):
    """Największy format (wg pola), który nie jest 'za słaby'."""
    candidates = []
    for other in formats:
        if other.pk == fmt.pk:
            continue
        q = quality_for(image_width, image_height, other, shop_settings)
        if not q.blocked:
            candidates.append((other.width_mm * other.height_mm, other))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]
