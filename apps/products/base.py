"""
Interfejs produktu: wszystko, co rdzeń sklepu wie o produkcie. Rdzeń nie zna plakatu ani
książki, zna tylko sześć elementów poniżej. Wygląd i proces zakupu (formularz, kadrowanie,
podgląd, cena na żywo) należą do sklepu, nie do interfejsu.

Pozycja zamówienia trzyma kod produktu, opcje (JSON, rozumie je tylko produkt), plik od
kupującego i listę plików do druku (`AssetVersion` rodzaju `print_pdf`).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


class ProductError(ValueError):
    """Opcje albo plik nie pasują do produktu. Komunikat po polsku, do pokazania kupującemu."""


@dataclass(frozen=True)
class GhLine:
    """Opis pozycji dla GH (kontrakt pkt 3): kod ze słownika produktów i nazwa dla operatora GH."""

    product_code: str
    name: str


class Product(ABC):
    # 1. Kod i nazwa. Kod to klucz w rejestrze i wartość `OrderItem.product`.
    code: str = ""
    name: str = ""

    # 4. Pliki od kupującego: jakie typy i ile. Rdzeń odrzuca plik spoza listy; produkt może
    # zasady rdzenia tylko zaostrzyć, nigdy poluzować.
    accepted_mime: tuple[str, ...] = ()
    max_input_files: int = 1

    # Opcjonalne fragmenty szablonów panelu operatora: szczegóły techniczne pozycji (O3)
    # i ustawienia produktu, np. cennik (O5). Panel to narzędzie robocze, nie wygląd sklepu.
    operator_template: str | None = None
    settings_template: str | None = None

    # 2. Opcje
    @abstractmethod
    def clean_options(self, options: dict, asset) -> dict:
        """Sprawdza i normalizuje opcje pozycji dla danego pliku. Niepoprawne: `ProductError`."""

    # 3. Cena
    @abstractmethod
    def unit_price(self, options: dict) -> Decimal:
        """Cena detaliczna za sztukę. Cena hurtowa dojdzie z taskiem „Katalog”."""

    # 5. Pliki do druku
    @abstractmethod
    def build_print_files(self, item) -> list:
        """Z pliku kupującego i opcji robi PDF-y do druku (`AssetVersion`). Wołane w tle."""

    def review_flags(self, item) -> list[str]:
        """Powody ręcznej weryfikacji przez operatora po przygotowaniu plików; pusta = bez uwag."""
        return []

    # 6. Opis pozycji
    @abstractmethod
    def gh_line(self, item) -> GhLine:
        """`product_code` i nazwa pozycji w zleceniu do GH."""

    def summary(self, item) -> str:
        """Krótki tekst dla operatora, maila i strony statusu, np. „Plakat A2, mat”."""
        return self.gh_line(item).name

    def details(self, item) -> list[tuple[str, str]]:
        """Pary (etykieta, wartość) do podsumowania zamówienia."""
        return []

    def operator_context(self, item) -> dict:
        """Dane dla `operator_template`."""
        return {}

    def settings_forms(self, data=None) -> dict:
        """Formularze ustawień produktu w panelu (O5), klucz = nazwa w `settings_template`."""
        return {}
