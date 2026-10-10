"""
Pliki od kupujących w rdzeniu: wgranie przez bramkę, podgląd, analiza treści, zapis wersji.
Obróbka pod konkretny produkt (kadr, upscale, PDF, makieta) należy do produktu, np. `apps.poster.pipeline`.
"""

import hashlib
import io
import logging
from datetime import timedelta
from typing import Protocol

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from PIL import Image

from apps.catalog.models import ShopSettings

from .gate import UploadError, check_upload  # noqa: F401 - UploadError importowany stąd
from .models import Asset, AssetAnalysis, AssetVersion

log = logging.getLogger("slop.assets")

PREVIEW_MAX_PX = 1600


# ---------------------------------------------------------------------------
# Upload i analiza
# ---------------------------------------------------------------------------


def create_asset_from_upload(uploaded_file, allowed_mime=None) -> Asset:
    """
    Plik od kupującego przez bramkę bezpieczeństwa (`gate.check_upload`), potem zapis
    w prywatnym katalogu. `allowed_mime` to typy przyjmowane przez produkt. Obraz dostaje
    podgląd i analizę treści; PDF tylko liczbę stron w `Asset`.
    """
    clean = check_upload(uploaded_file, allowed_mime)
    shop = ShopSettings.get()

    with transaction.atomic():
        asset = Asset(
            original_filename=getattr(uploaded_file, "name", "")[:255],
            mime=clean.mime,
            width=clean.width,
            height=clean.height,
            size_bytes=len(clean.data),
            sha256=hashlib.sha256(clean.data).hexdigest(),
            expires_at=timezone.now() + timedelta(days=shop.draft_retention_days),
        )
        asset.original_file.save(f"upload{clean.ext}", ContentFile(clean.data), save=False)
        asset.save()
        if clean.image is not None:
            _save_preview(asset, clean.image)
            AnalysisService().analyze(asset, image=clean.image)
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
    """
    Analiza treści obrazu w rdzeniu: proporcje i flagi moderacji. Ocenę przydatności do druku
    (np. DPI per format) dopisuje produkt do `AssetAnalysis.quality_per_format`.
    """

    def __init__(self, content_safety: ContentSafetyService | None = None):
        self.content_safety = content_safety or MockContentSafety()

    def analyze(self, asset: Asset, image: Image.Image | None = None) -> AssetAnalysis:
        flags = self.content_safety.check(asset, image)
        analysis, _ = AssetAnalysis.objects.update_or_create(
            asset=asset,
            defaults={"aspect_ratio": round(asset.aspect_ratio, 4), "content_flags": flags},
        )
        return analysis
