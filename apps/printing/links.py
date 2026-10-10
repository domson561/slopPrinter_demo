"""
Podpisane, wygasające linki do plików dla GH (kontrakt pkt 4). Token niesie id wersji pliku,
termin ważności i losowy nonce (każde wystawienie to nowy link), podpisany `SECRET_KEY`.
Link nie prowadzi do `/media/`; plik wydaje widok `printing:file` po sprawdzeniu podpisu i terminu.
"""

import secrets
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from apps.assets.models import AssetVersion

SALT = "printing.file-link"


class LinkError(Exception):
    """Zły podpis albo plik, którego już nie ma."""


class LinkExpired(LinkError):
    """Link po terminie ważności."""


def issue(version: AssetVersion, ttl_hours: int | None = None) -> tuple[str, datetime]:
    """Nowy link do pliku do druku; zwraca (url, expires_at), oba z tą samą datą ważności."""
    ttl = ttl_hours or settings.GH_FILE_LINK_TTL_HOURS
    expires_at = (timezone.now() + timedelta(hours=ttl)).replace(microsecond=0)
    payload = {"v": version.pk, "exp": int(expires_at.timestamp()), "n": secrets.token_hex(4)}
    token = signing.dumps(payload, salt=SALT, compress=False)
    return settings.SITE_URL + reverse("printing:file", kwargs={"token": token}), expires_at


def resolve(token: str) -> AssetVersion:
    """Wersja pliku dla tokenu; `LinkError` przy złym podpisie, `LinkExpired` po terminie."""
    try:
        payload = signing.loads(token, salt=SALT)
    except signing.BadSignature:
        raise LinkError("zły podpis linku") from None
    if datetime.fromtimestamp(payload["exp"], tz=UTC) <= timezone.now():
        raise LinkExpired("link wygasł")
    version = AssetVersion.objects.filter(pk=payload["v"], kind=AssetVersion.Kind.PRINT_PDF).first()
    if version is None:
        raise LinkError("plik nie istnieje")
    return version
