"""Dane i pomocnicze funkcje testów zgodności z kontraktem v1."""

import hashlib

from apps.gh_mock.models import GhOrder
from apps.printing import links
from apps.printing.models import PrintJob

# Dane kupującego: kontrakt pozwala na nie tylko w PDF etykiety, nigdy w body zlecenia.
CUSTOMER = {
    "email": "kupujacy@example.com",
    "full_name": "Jan Kowalski",
    "phone": "+48 600 100 200",
    "address": {
        "line1": "Długa 1",
        "postal_code": "00-001",
        "city": "Warszawa",
        "country": "PL",
    },
}
# Sztuk każdej pozycji koszyka; więcej niż 1, żeby wartość zlecenia sprawdzała mnożenie.
QUANTITY = 2


def customer_values() -> list[str]:
    """Wartości, których nie może być w body; kod kraju pomijamy, bo „PL” jest w „PLN”."""
    address = {k: v for k, v in CUSTOMER["address"].items() if k != "country"}
    return [CUSTOMER["email"], CUSTOMER["full_name"], CUSTOMER["phone"], *address.values()]


def keys_of(value) -> set[str]:
    """Wszystkie klucze słowników w body, na każdym poziomie zagnieżdżenia."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


def sha256_of(obj) -> tuple[str, int]:
    """Suma i rozmiar pliku za linkiem (PDF do druku albo etykieta)."""
    file = links.file_of(obj)
    with file.open("rb") as fh:
        data = fh.read()
    return hashlib.sha256(data).hexdigest(), len(data)


def gh_order_of(job: PrintJob) -> GhOrder:
    return GhOrder.objects.get(shop_order_number=job.shop_order_number)
