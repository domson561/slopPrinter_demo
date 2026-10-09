from decimal import Decimal

from apps.assets.models import AssetVersion
from apps.assets.services import save_version
from apps.products import GhLine, Product, ProductError


class ReadyPdfProduct(Product):
    code = "ready_pdf"
    name = "Gotowy PDF"
    accepted_mime = ("application/pdf",)
    price = Decimal("25.00")

    def clean_options(self, options: dict, asset) -> dict:
        if options:
            raise ProductError("Gotowy PDF nie ma opcji.")
        return {}

    def unit_price(self, options: dict) -> Decimal:
        return self.price

    def build_print_files(self, item) -> list:
        """Plik do druku to kopia oryginału; PDF sprawdziła bramka plików przy wgrywaniu."""
        with item.asset.original_file.open("rb") as fh:
            data = fh.read()
        version = save_version(
            item.asset,
            AssetVersion.Kind.PRINT_PDF,
            provider="ReadyPdf",
            params={"source": "original"},
            order_item=item,
            pdf_bytes=data,
        )
        return [version]

    def gh_line(self, item) -> GhLine:
        return GhLine(product_code="ready-pdf", name="Gotowy PDF od klienta")
