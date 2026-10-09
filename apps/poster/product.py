"""Plakat z demo jako `Product`: opcje, cena, PDF ze spadami, opis dla GH i panelu."""

from decimal import Decimal

from apps.assets import quality
from apps.assets.services import FLAG_LABELS, run_pipeline
from apps.catalog import services as catalog_services
from apps.catalog.models import Format, PaperOption, ShopSettings
from apps.products import GhLine, Product, ProductError

from .options import PRODUCT_CODE, format_of, paper_of


class PosterProduct(Product):
    code = PRODUCT_CODE
    name = "Plakat"
    accepted_mime = ("image/jpeg", "image/png", "image/webp")
    operator_template = "poster/ops_item.html"
    settings_template = "poster/ops_settings.html"

    def clean_options(self, options: dict, asset) -> dict:
        fmt = Format.objects.filter(code=options.get("format"), is_active=True).first()
        if fmt is None:
            raise ProductError("Wybierz dostępny format.")
        paper = None
        if options.get("paper"):
            paper = PaperOption.objects.filter(code=options["paper"], is_active=True).first()
            if paper is None:
                raise ProductError("Wybierz dostępny papier.")
        crop = quality.sanitize_crop(options.get("crop"), asset.width, asset.height, fmt)
        q = quality.quality_for(asset.width, asset.height, fmt, ShopSettings.get(), crop)
        if q.blocked:
            raise ProductError(
                f"Format {fmt.name} jest zablokowany dla tego pliku (za słaby plik)."
            )
        return {
            "format": fmt.code,
            "paper": paper.code if paper else None,
            "white_margin": bool(options.get("white_margin")),
            "crop": crop,
            "enhancer": bool(options.get("enhancer")),
        }

    def unit_price(self, options: dict) -> Decimal:
        return catalog_services.unit_price(
            format_of(options), paper_of(options), options.get("white_margin", False)
        )

    def build_print_files(self, item) -> list:
        return [run_pipeline(item)]

    def review_flags(self, item) -> list[str]:
        analysis = getattr(item.asset, "analysis", None)
        return [FLAG_LABELS.get(f, f) for f in (analysis.content_flags if analysis else [])]

    def gh_line(self, item) -> GhLine:
        fmt, paper = format_of(item.options), paper_of(item.options)
        code = "-".join(p for p in ("poster", fmt.code.lower(), paper.code if paper else None) if p)
        parts = [f"Plakat {fmt.name}"]
        if paper:
            parts.append(paper.name)
        if item.options.get("white_margin"):
            parts.append("biały margines")
        return GhLine(product_code=code, name=", ".join(parts))

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

    def settings_forms(self, data=None) -> dict:
        from .forms import FormatFormSet, PaperFormSet, PosterSettingsForm

        return {
            "settings": PosterSettingsForm(data, instance=ShopSettings.get()),
            "formats": FormatFormSet(data, prefix="formats"),
            "papers": PaperFormSet(data, prefix="papers"),
        }
