"""
Logika atrapy GH: walidacja żądań z kontraktu, idempotentne przyjęcie zlecenia, pobieranie
plików „z crona”, zmiany statusów przez biuro i lista zmian dla sklepu (pkt 3-7 kontraktu).
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.printing import links as file_links
from apps.products import dictionary

from .models import GhOrder, GhOrderFile, GhOrderItem, GhShop

log = logging.getLogger("slop.gh_mock")

# Dane kupującego nie mają prawa wejść do GH (kontrakt pkt 1). Szukane rekurencyjnie.
FORBIDDEN_KEYS = {
    "address",
    "shipping_address",
    "shipping",
    "email",
    "phone",
    "customer",
    "customer_name",
    "full_name",
    "note_for_customer",
    "recipient",
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ValidationFailed(Exception):
    def __init__(self, errors: dict):
        super().__init__("validation failed")
        self.errors = errors


class Conflict(Exception):
    """Ten sam numer zamówienia sklepu z inną treścią (409)."""


class NotAllowed(Exception):
    """Operacja niedozwolona w tym stanie (400)."""


# ---------------------------------------------------------------------------
# Sklep demo
# ---------------------------------------------------------------------------


def ensure_demo_shop() -> GhShop:
    """
    Prawdziwe GH wydaje token przy podpinaniu sklepu. Atrapa zna jeden sklep: ten,
    którego token siedzi w `GH_API_TOKEN` sklepu, żeby demo działało bez konfiguracji.
    """
    shop, _ = GhShop.objects.update_or_create(
        code="SP",
        defaults={
            "name": settings.SITE_NAME,
            "token_hash": GhShop.hash_token(settings.GH_API_TOKEN),
            "is_active": True,
        },
    )
    return shop


# ---------------------------------------------------------------------------
# Walidacja
# ---------------------------------------------------------------------------


def body_hash(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _find_forbidden(value, path, errors):
    if isinstance(value, dict):
        for key, inner in value.items():
            key_path = f"{path}.{key}" if path else key
            if key in FORBIDDEN_KEYS:
                errors.setdefault(key_path, []).append("dane kupującego tylko na etykiecie")
            _find_forbidden(inner, key_path, errors)
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            _find_forbidden(inner, f"{path}.{i}", errors)


def _decimal(value, path, errors) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        errors.setdefault(path, []).append("liczba dziesiętna jako tekst")
        return None


def _datetime(value, path, errors) -> datetime | None:
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None:
        errors.setdefault(path, []).append("data ISO 8601")
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_default_timezone())
    return parsed


def validate_file(value, path, errors) -> dict | None:
    """Obiekt pliku z pkt 4: url, sha256, size, expires_at."""
    if not isinstance(value, dict):
        errors.setdefault(path, []).append("wymagany obiekt pliku")
        return None
    url = value.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        errors.setdefault(f"{path}.url", []).append("wymagany link http(s)")
    sha = value.get("sha256")
    if not isinstance(sha, str) or not _SHA256.match(sha):
        errors.setdefault(f"{path}.sha256", []).append("64 znaki hex")
    size = value.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        errors.setdefault(f"{path}.size", []).append("rozmiar w bajtach > 0")
    expires_at = _datetime(value.get("expires_at"), f"{path}.expires_at", errors)
    if errors:
        return None
    return {"url": url, "sha256": sha, "size": size, "expires_at": expires_at}


def validate_order_body(body) -> dict:
    """Zwraca oczyszczone dane albo rzuca ValidationFailed z błędami jak w pkt 7."""
    errors: dict[str, list[str]] = {}
    if not isinstance(body, dict):
        raise ValidationFailed({"body": ["wymagany obiekt JSON"]})
    _find_forbidden(body, "", errors)

    number = body.get("shop_order_number")
    if not isinstance(number, str) or not 1 <= len(number) <= 40:
        errors.setdefault("shop_order_number", []).append("tekst 1-40 znaków")
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.setdefault("title", []).append("wymagany")
    if body.get("currency") != "PLN":
        errors.setdefault("currency", []).append("w v1 tylko PLN")
    total_value = _decimal(body.get("total_value"), "total_value", errors)
    note = body.get("note_for_production", "") or ""
    if not isinstance(note, str) or len(note) > 500:
        errors.setdefault("note_for_production", []).append("tekst do 500 znaków")

    raw_items = body.get("items")
    items = []
    if not isinstance(raw_items, list) or not raw_items:
        errors.setdefault("items", []).append("lista z co najmniej jedną pozycją")
        raw_items = []
    items_total = Decimal("0.00")
    for i, raw in enumerate(raw_items):
        path = f"items.{i}"
        if not isinstance(raw, dict):
            errors.setdefault(path, []).append("wymagany obiekt pozycji")
            continue
        if raw.get("position") != i + 1:
            errors.setdefault(f"{path}.position", []).append(
                f"kolejny numer od 1, oczekiwano {i + 1}"
            )
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.setdefault(f"{path}.name", []).append("wymagany")
        code = raw.get("product_code")
        if not isinstance(code, str) or not code.strip():
            errors.setdefault(f"{path}.product_code", []).append("wymagany")
        quantity = raw.get("quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            errors.setdefault(f"{path}.quantity", []).append("liczba całkowita >= 1")
        unit_price = _decimal(raw.get("unit_price"), f"{path}.unit_price", errors)
        file = validate_file(raw.get("file"), f"{path}.file", errors)
        if unit_price is not None and isinstance(quantity, int):
            items_total += unit_price * quantity
        items.append(
            {
                "position": i + 1,
                "name": name,
                "product_code": code,
                "quantity": quantity,
                "unit_price": unit_price,
                "file": file,
            }
        )

    label = validate_file(body.get("label"), "label", errors)

    if total_value is not None and raw_items and total_value != items_total:
        errors.setdefault("total_value", []).append(
            f"musi równać się sumie pozycji ({items_total})"
        )
    if errors:
        raise ValidationFailed(errors)
    return {
        "shop_order_number": number,
        "title": title.strip(),
        "currency": "PLN",
        "total_value": total_value,
        "note_for_production": note,
        "items": items,
        "label": label,
    }


# ---------------------------------------------------------------------------
# Przyjęcie zlecenia (pkt 3)
# ---------------------------------------------------------------------------


def _next_gh_number() -> str:
    year = timezone.now().year
    count = GhOrder.objects.filter(gh_number__endswith=f"/{year}").count()
    return f"{count + 1}/{year}"


@transaction.atomic
def accept_order(shop: GhShop, body: dict) -> tuple[GhOrder, bool]:
    """Idempotentnie: (zlecenie, czy nowe). Ten sam numer z inną treścią -> Conflict."""
    digest = body_hash(body)
    clean = validate_order_body(body)
    existing = GhOrder.objects.filter(
        shop=shop, shop_order_number=clean["shop_order_number"]
    ).first()
    if existing is not None:
        if existing.body_hash != digest:
            raise Conflict(existing.shop_order_number)
        return existing, False

    order = GhOrder.objects.create(
        shop=shop,
        shop_order_number=clean["shop_order_number"],
        gh_number=_next_gh_number(),
        title=clean["title"],
        total_value=clean["total_value"],
        currency=clean["currency"],
        note_for_production=clean["note_for_production"],
        body_hash=digest,
        # Słownik produktów (kontrakt pkt 10); prawdziwe GH trzyma własną kopię pliku.
        needs_review=any(not dictionary.known(item["product_code"]) for item in clean["items"]),
    )
    for item in clean["items"]:
        GhOrderItem.objects.create(
            order=order,
            position=item["position"],
            name=item["name"],
            product_code=item["product_code"],
            quantity=item["quantity"],
            unit_price=item["unit_price"],
        )
        GhOrderFile.objects.create(
            order=order, kind=GhOrderFile.Kind.ITEM, position=item["position"], **item["file"]
        )
    GhOrderFile.objects.create(order=order, kind=GhOrderFile.Kind.LABEL, **clean["label"])
    log.info("GH: przyjęto %s jako %s", order.shop_order_number, order.gh_number)
    return order, True


def order_response(order: GhOrder) -> dict:
    """Odpowiedź na POST z pkt 3."""
    return {
        "gh_number": order.gh_number,
        "shop_order_number": order.shop_order_number,
        "status": order.status,
        "files": [f.to_state() for f in order.files.all()],
        "created_at": order.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Pliki (pkt 4)
# ---------------------------------------------------------------------------


def replace_file_link(order: GhOrder, body: dict) -> GhOrderFile:
    """Dosłanie świeżego linku. Tylko dla pliku `pending` lub `failed`."""
    errors: dict[str, list[str]] = {}
    if not isinstance(body, dict):
        raise ValidationFailed({"body": ["wymagany obiekt JSON"]})
    _find_forbidden(body, "", errors)
    kind = body.get("kind")
    position = body.get("position")
    if kind not in (GhOrderFile.Kind.ITEM, GhOrderFile.Kind.LABEL):
        errors.setdefault("kind", []).append("item albo label")
    if kind == GhOrderFile.Kind.ITEM and (not isinstance(position, int) or position < 1):
        errors.setdefault("position", []).append("numer pozycji")
    file = validate_file(body.get("file"), "file", errors)
    if errors:
        raise ValidationFailed(errors)

    lookup = {"kind": kind}
    if kind == GhOrderFile.Kind.ITEM:
        lookup["position"] = position
    target = order.files.filter(**lookup).first()
    if target is None:
        if kind == GhOrderFile.Kind.ITEM:
            raise ValidationFailed({"position": ["brak takiej pozycji"]})
        raise ValidationFailed({"kind": ["zlecenie nie ma etykiety"]})
    if target.state == GhOrderFile.State.DOWNLOADED:
        raise NotAllowed("plik już pobrany, zmiana wymaga nowego zamówienia")

    for key, value in file.items():
        setattr(target, key, value)
    target.state = GhOrderFile.State.PENDING
    target.error = ""
    target.changed_at = timezone.now()
    target.save()
    order.touch()
    order.save(update_fields=["changed_at"])
    return target


def fetch_url(url: str) -> bytes:
    """
    Pobranie pliku. Podpisane linki własnego sklepu atrapa rozwiązuje w procesie tym samym kodem,
    co widok `printing:file` (podpis i termin sprawdzone, zły link = wyjątek = `unreachable`),
    bez zależności od portu. Prawdziwe GH pobiera wyłącznie przez HTTP.
    """
    signed_prefix = settings.SITE_URL + "/files/signed/"
    if url.startswith(signed_prefix):
        token = url[len(signed_prefix) :].split("?", 1)[0].removesuffix(".pdf")
        with file_links.resolve(token).open("rb") as fh:
            return fh.read()
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def download_file(file: GhOrderFile, fetch=fetch_url) -> GhOrderFile:
    """Jedno pobranie z weryfikacją jak w pkt 4. Zapisuje stan i ewentualny błąd."""
    error = ""
    if file.expires_at <= timezone.now():
        error = "expired"
    else:
        try:
            data = fetch(file.url)
        except Exception as exc:  # noqa: BLE001 - każdy błąd sieci/dysku to `unreachable`
            log.warning("GH: pobranie %s nieudane: %s", file.url, exc)
            data = None
            error = "unreachable"
        if data is not None:
            if not data.startswith(b"%PDF"):
                error = "not_pdf"
            elif len(data) > settings.GH_MOCK_MAX_FILE_BYTES:
                error = "too_large"
            elif len(data) != file.size or hashlib.sha256(data).hexdigest() != file.sha256:
                error = "checksum_mismatch"
    if error:
        file.state = GhOrderFile.State.FAILED
        file.error = error
    else:
        file.state = GhOrderFile.State.DOWNLOADED
        file.error = ""
        file.downloaded_at = timezone.now()
    file.changed_at = timezone.now()
    file.save()
    file.order.touch()
    file.order.save(update_fields=["changed_at"])
    return file


def download_pending_files(fetch=fetch_url) -> int:
    """Krok „crona” GH: pobiera wszystkie pliki w stanie `pending`. Zwraca liczbę prób."""
    pending = GhOrderFile.objects.filter(state=GhOrderFile.State.PENDING).select_related("order")
    count = 0
    for file in pending:
        download_file(file, fetch)
        count += 1
    return count


def expire_file(file: GhOrderFile) -> GhOrderFile:
    """Akcja demo: udaje, że link wygasł, zanim GH zdążyło pobrać."""
    file.state = GhOrderFile.State.FAILED
    file.error = "expired"
    file.changed_at = timezone.now()
    file.save()
    file.order.touch()
    file.order.save(update_fields=["changed_at"])
    return file


# ---------------------------------------------------------------------------
# Statusy (pkt 6) i zmiany (pkt 5)
# ---------------------------------------------------------------------------


def set_status(order: GhOrder, status: str) -> GhOrder:
    """Biuro przesuwa zlecenie o jeden krok. W realizację tylko z kompletem plików."""
    if status != order.next_status:
        raise NotAllowed(f"z {order.status} nie da się przejść do {status}")
    if status == GhOrder.Status.IN_PROGRESS and not order.all_files_downloaded:
        raise NotAllowed("nie wszystkie pliki pobrane")
    now = timezone.now()
    order.status = status
    order.status_changed_at = now
    if status == GhOrder.Status.SHIPPED:
        order.shipped_at = now
    order.touch()
    order.save()
    return order


def changes_since(shop: GhShop, since) -> list[GhOrder]:
    qs = GhOrder.objects.filter(shop=shop).prefetch_related("files")
    if since is not None:
        qs = qs.filter(changed_at__gt=since)
    return list(qs.order_by("changed_at", "pk"))


def advance_all(fetch=fetch_url) -> int:
    """Auto-symulacja: najpierw pobrania, potem każde zlecenie o jeden status."""
    advanced = download_pending_files(fetch)
    for order in GhOrder.objects.exclude(status=GhOrder.Status.SHIPPED).order_by("created_at"):
        try:
            set_status(order, order.next_status)
            advanced += 1
        except NotAllowed:
            continue
    return advanced
