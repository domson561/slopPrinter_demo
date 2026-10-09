"""Rejestr produktów: klasy z `settings.SHOP_PRODUCTS`, po jednej instancji na kod."""

from functools import cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from .base import Product, ProductError


@cache
def _registry() -> dict[str, Product]:
    products: dict[str, Product] = {}
    for path in settings.SHOP_PRODUCTS:
        product = import_string(path)()
        if not product.code:
            raise ImproperlyConfigured(f"{path}: produkt bez kodu.")
        if product.code in products:
            raise ImproperlyConfigured(f"{path}: kod {product.code!r} już zarejestrowany.")
        products[product.code] = product
    return products


def get_product(code: str) -> Product:
    try:
        return _registry()[code]
    except KeyError:
        raise ProductError(f"Nieznany produkt: {code!r}.") from None


def all_products() -> list[Product]:
    return list(_registry().values())


@receiver(setting_changed)
def _reset_registry(setting, **kwargs):
    if setting == "SHOP_PRODUCTS":
        _registry.cache_clear()
