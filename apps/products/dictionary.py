"""
Słownik `product_code` wspólny dla sklepów i GH (kontrakt v1, pkt 10). Plik `slownik.json` obok,
wersjonowany niezależnie od API; GH trzyma własną kopię tego pliku. Produkt bierze stąd kod
i nazwę pozycji dla GH zamiast składać je w kodzie. Opis formatu: `docs/slownik-produktow.md`.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .base import ProductError

PATH = Path(__file__).with_name("slownik.json")
REQUIRED = ("name", "format", "paper", "finish", "files")


class UnknownProductCode(ProductError):
    """Kod spoza słownika. GH takie zlecenie przyjmie, ale oznaczy „do weryfikacji”."""


@dataclass(frozen=True)
class Entry:
    code: str
    name: str  # nazwa dla produkcji, idzie w `items[].name`
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
            format=raw["format"],
            paper=raw["paper"],
            finish=raw["finish"],
            files=tuple(raw["files"]),
        )
    return data["version"], entries


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
