"""Skróty plakatu dla sklepu demo (konfigurator, seed, testy) nad ogólnym szkicem z rdzenia."""

from apps.orders import services as order_services

from .options import PRODUCT_CODE, poster_options


def save_poster_draft(
    asset, fmt, paper, *, white_margin, quantity, crop, enhancer_enabled, order=None
):
    return order_services.save_draft(
        PRODUCT_CODE,
        asset,
        poster_options(fmt, paper, white_margin=white_margin, crop=crop, enhancer=enhancer_enabled),
        quantity=quantity,
        order=order,
    )
