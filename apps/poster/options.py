"""Opcje pozycji plakatu. Bez ciężkich importów, bo czyta je też `pipeline`."""

from .models import Format, PaperOption

PRODUCT_CODE = "poster"


def poster_options(fmt, paper=None, *, white_margin=False, crop=None, enhancer=False) -> dict:
    return {
        "format": fmt.code,
        "paper": paper.code if paper else None,
        "white_margin": bool(white_margin),
        "crop": crop or {},
        "enhancer": bool(enhancer),
    }


def format_of(options: dict) -> Format:
    return Format.objects.get(code=options.get("format"))


def paper_of(options: dict) -> PaperOption | None:
    code = options.get("paper")
    return PaperOption.objects.filter(code=code).first() if code else None
