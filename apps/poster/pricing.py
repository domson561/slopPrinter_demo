"""Cennik plakatu: cena za sztukę z formatu i papieru, wycena koszyka dla konfiguratora."""

from dataclasses import dataclass
from decimal import Decimal

from .models import Format, PaperOption


@dataclass(frozen=True)
class Quote:
    unit_price: Decimal
    quantity: int
    subtotal: Decimal
    delivery_price: Decimal
    total: Decimal


def unit_price(fmt: Format, paper: PaperOption | None, white_margin: bool = False) -> Decimal:
    price = Decimal(fmt.base_price)
    if paper is not None:
        price += Decimal(paper.price_delta)
    # Biały margines nie zmienia ceny [PROPOZYCJA].
    return price.quantize(Decimal("0.01"))


def quote(
    fmt: Format,
    paper: PaperOption | None,
    quantity: int,
    white_margin: bool = False,
    delivery=None,
) -> Quote:
    """`delivery` to metoda dostawy z rdzenia (`catalog.DeliveryMethod`), liczy się tylko jej cena."""
    quantity = max(int(quantity), 1)
    unit = unit_price(fmt, paper, white_margin)
    subtotal = (unit * quantity).quantize(Decimal("0.01"))
    delivery_price = Decimal(delivery.price) if delivery is not None else Decimal("0.00")
    return Quote(
        unit_price=unit,
        quantity=quantity,
        subtotal=subtotal,
        delivery_price=delivery_price.quantize(Decimal("0.01")),
        total=(subtotal + delivery_price).quantize(Decimal("0.01")),
    )


def active_formats():
    return Format.objects.filter(is_active=True)


def active_papers():
    return PaperOption.objects.filter(is_active=True)
