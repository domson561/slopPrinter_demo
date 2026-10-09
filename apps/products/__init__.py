"""
Produkty sklepu: interfejs, przez który sklep wpina własny produkt w rdzeń, i rejestr
produktów z `settings.SHOP_PRODUCTS`. Rdzeń (zamówienia, druk, panel) importuje tylko stąd.
"""

from .base import GhLine, Product, ProductError
from .registry import all_products, get_product

__all__ = ["GhLine", "Product", "ProductError", "all_products", "get_product"]
