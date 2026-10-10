"""Skróty plakatu dla sklepu demo (konfigurator, seed, testy) nad ogólnym rdzeniem."""

from apps.assets.services import create_asset_from_upload
from apps.orders import services as order_services
from apps.products import get_product

from .options import PRODUCT_CODE, poster_options
from .pipeline import analyze_formats


def upload_poster_asset(uploaded_file):
    """Plik przez bramkę rdzenia (typy plakatu), potem werdykt jakości dla każdego formatu."""
    asset = create_asset_from_upload(
        uploaded_file, allowed_mime=get_product(PRODUCT_CODE).accepted_mime
    )
    analyze_formats(asset)
    return asset


def save_poster_draft(
    asset,
    fmt,
    paper,
    *,
    white_margin,
    quantity,
    crop,
    enhancer_enabled,
    order=None,
    item=None,
):
    """Dodaje plakat do koszyka `order` (albo nowego) lub aktualizuje `item`. Zwraca pozycję."""
    return order_services.save_draft(
        PRODUCT_CODE,
        asset,
        poster_options(fmt, paper, white_margin=white_margin, crop=crop, enhancer=enhancer_enabled),
        quantity=quantity,
        order=order,
        item=item,
    )
