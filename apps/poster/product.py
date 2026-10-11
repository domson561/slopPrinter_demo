"""Plakat z demo jako `Product`: opcje, cena, PDF ze spadami, opis dla GH i panelu."""

import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.assets.services import FLAG_LABELS
from apps.products import GhLine, Product, ProductError, dictionary

from . import pricing, quality
from .models import Format, PaperOption, PosterSettings
from .options import PRODUCT_CODE, format_of, paper_of
from .pipeline import run_pipeline


def dictionary_code(fmt: Format, paper: PaperOption) -> str:
    """Kod ze słownika produktów dla pary format + papier, np. `poster-a2-matte`."""
    return f"poster-{fmt.code.lower()}-{paper.code}"


class PosterProduct(Product):
    code = PRODUCT_CODE
    name = "Plakat"
    accepted_mime = ("image/jpeg", "image/png", "image/webp")
    operator_template = "poster/ops_item.html"
    settings_template = "poster/ops_settings.html"
    fixtures = ("poster.json",)

    def clean_options(self, options: dict, asset) -> dict:
        fmt = Format.objects.filter(code=options.get("format"), is_active=True).first()
        if fmt is None:
            raise ProductError("Wybierz dostępny format.")
        if options.get("paper"):
            paper = PaperOption.objects.filter(code=options["paper"], is_active=True).first()
        else:
            # Brak papieru w opcjach = pierwszy aktywny; kod dla GH zawsze ma papier.
            paper = PaperOption.objects.filter(is_active=True).first()
        if paper is None:
            raise ProductError("Wybierz dostępny papier.")
        if not dictionary.known(dictionary_code(fmt, paper)):
            raise ProductError(f"Plakat {fmt.name} na papierze {paper.name} nie jest dostępny.")
        crop = quality.sanitize_crop(options.get("crop"), asset.width, asset.height, fmt)
        q = quality.quality_for(asset.width, asset.height, fmt, PosterSettings.get(), crop)
        if q.blocked:
            raise ProductError(
                f"Format {fmt.name} jest zablokowany dla tego pliku (za słaby plik)."
            )
        return {
            "format": fmt.code,
            "paper": paper.code,
            "white_margin": bool(options.get("white_margin")),
            "crop": crop,
            "enhancer": bool(options.get("enhancer")),
        }

    def unit_price(self, options: dict) -> Decimal:
        return pricing.unit_price(
            format_of(options), paper_of(options), options.get("white_margin", False)
        )

    def build_print_files(self, item) -> list:
        return [run_pipeline(item)]

    def review_flags(self, item) -> list[str]:
        analysis = getattr(item.asset, "analysis", None)
        return [FLAG_LABELS.get(f, f) for f in (analysis.content_flags if analysis else [])]

    def gh_line(self, item) -> GhLine:
        """Kod, nazwa i stawka ze słownika; biały margines jest w PDF-ie, GH go nie obchodzi."""
        fmt, paper = format_of(item.options), paper_of(item.options)
        if paper is None:
            raise ProductError("Pozycja plakatu bez papieru; brak kodu dla GH.")
        entry = dictionary.get(dictionary_code(fmt, paper))
        return GhLine(product_code=entry.code, name=entry.name, print_rate=entry.print_rate)

    def summary(self, item) -> str:
        text = self.gh_line(item).name
        if item.options.get("white_margin"):
            text += ", biały margines"
        return text

    def details(self, item) -> list[tuple[str, str]]:
        fmt, paper = format_of(item.options), paper_of(item.options)
        landscape = (item.options.get("crop") or {}).get("orientation") == "landscape"
        return [
            (
                "Format",
                f"{fmt.name} ({fmt.width_mm} x {fmt.height_mm} mm, "
                f"{'poziomo' if landscape else 'pionowo'})",
            ),
            ("Papier", paper.name if paper else "-"),
            ("Biały margines", "tak" if item.options.get("white_margin") else "nie"),
            (
                "Poprawa jakości",
                "włączona (symulacja)" if item.options.get("enhancer") else "wyłączona",
            ),
        ]

    def operator_context(self, item) -> dict:
        analysis = getattr(item.asset, "analysis", None)
        return {
            "format_code": item.options.get("format"),
            "cropped": item.asset.version("cropped", item),
            "analysis": analysis,
            "flag_labels": self.review_flags(item),
        }

    def contract_sample(self) -> tuple:
        """PNG 1448 x 2048 px, którego jakość wystarcza na A3, i plakat A3 na papierze matowym."""
        buf = io.BytesIO()
        Image.new("RGB", (1448, 2048), (120, 80, 200)).save(buf, "PNG")
        upload = SimpleUploadedFile("probka.png", buf.getvalue(), content_type="image/png")
        return upload, {"format": "A3", "paper": "matte"}

    def settings_forms(self, data=None) -> dict:
        from .forms import FormatFormSet, PaperFormSet, PosterSettingsForm

        return {
            "settings": PosterSettingsForm(data, instance=PosterSettings.get()),
            "formats": FormatFormSet(data, prefix="formats"),
            "papers": PaperFormSet(data, prefix="papers"),
        }
