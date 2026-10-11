"""
Słownik `product_code` wspólny dla sklepów i GH (kontrakt v1, pkt 10). Plik `slownik.json` obok,
wersjonowany niezależnie od API; GH trzyma własną kopię tego pliku. Produkt bierze stąd kod,
nazwę pozycji i stawkę GH za druk zamiast składać je w kodzie. Opis formatu:
`docs/slownik-produktow.md`.
"""

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path

from .base import ProductError

PATH = Path(__file__).with_name("slownik.json")
REQUIRED = ("name", "print_rate", "format", "paper", "finish", "files")


class UnknownProductCode(ProductError):
    """Kod spoza słownika. GH takie zlecenie przyjmie, ale oznaczy „do weryfikacji”."""


@dataclass(frozen=True)
class Entry:
    code: str
    name: str  # nazwa dla produkcji, idzie w `items[].name`
    print_rate: Decimal  # stawka GH za sztukę, PLN netto, idzie w `items[].unit_price`
    format: str
    paper: str
    finish: str
    files: tuple[dict, ...]  # oczekiwane pliki: rola, strony, opis


@cache
def _load() -> tuple[str, dict[str, Entry]]:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    entries = {}
    for code, raw in data["products"].items():
        missing = [key for key in REQUIRED if key not in raw]
        if missing:
            raise ValueError(f"slownik.json: wpis {code!r} bez pól {missing}.")
        entries[code] = Entry(
            code=code,
            name=raw["name"],
            print_rate=_rate(code, raw["print_rate"]),
            format=raw["format"],
            paper=raw["paper"],
            finish=raw["finish"],
            files=tuple(raw["files"]),
        )
    return data["version"], entries


def _rate(code: str, value) -> Decimal:
    """Stawka jako tekst z dwoma miejscami po przecinku, np. "32.00"; liczba w JSON-ie gubi grosze."""
    try:
        rate = Decimal(value) if isinstance(value, str) else None
    except InvalidOperation:
        rate = None
    if rate is None or not rate.is_finite() or rate <= 0 or rate != rate.quantize(Decimal("0.01")):
        raise ValueError(
            f"slownik.json: wpis {code!r}, print_rate {value!r} to nie kwota w groszach."
        )
    return rate.quantize(Decimal("0.01"))


def version() -> str:
    return _load()[0]


def all_entries() -> dict[str, Entry]:
    return dict(_load()[1])


def known(code: str) -> bool:
    return code in _load()[1]


def get(code: str) -> Entry:
    try:
        return _load()[1][code]
    except KeyError:
        raise UnknownProductCode(
            f"Kod {code!r} spoza słownika produktów (wersja {version()})."
        ) from None
