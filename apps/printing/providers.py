"""
Klient API GH za wspólnym interfejsem `PrintProvider` (kontrakt v1, `docs/kontrakt-gh-v1.md`).
Jedna implementacja, `GhApiProvider`, i dwa transporty:
- `InProcessTransport`: woła widoki atrapy (`apps.gh_mock`) w tym samym procesie, przez pełny
  cykl HTTP Django (routing, token, serializacja). Domyślny w dev i testach.
- `HttpTransport`: `requests` do prawdziwego GH. Włącza się, gdy `GH_API_BASE_URL` zaczyna
  się od `http`.
Podmiana atrapy na prawdziwe GH = zmiana `GH_API_BASE_URL` i `GH_API_TOKEN`.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

import requests
from django.conf import settings
from django.utils.module_loading import import_string

log = logging.getLogger("slop.printing")


class ProviderError(Exception):
    """Błąd przejściowy (429, 5xx, timeout, sieć). Serwis ponawia z backoffem."""


class ProviderRejected(Exception):
    """400, 404, 409: GH odrzuciło żądanie. Bez ponawiania, zlecenie do ręcznej obsługi."""

    def __init__(self, status_code: int, errors):
        super().__init__(f"HTTP {status_code}: {errors}")
        self.status_code = status_code
        self.errors = errors


class ProviderAuthError(Exception):
    """401: zły lub cofnięty token. Sklep wstrzymuje wysyłkę i polling, alarmuje operatora."""


@dataclass
class RemoteOrderState:
    """Element `orders[]` z pkt 5 kontraktu (ten sam kształt co odpowiedź na POST)."""

    shop_order_number: str
    gh_number: str
    status: str
    files: list[dict] = field(default_factory=list)
    status_changed_at: str | None = None
    shipped_at: str | None = None

    @classmethod
    def from_payload(cls, data: dict) -> "RemoteOrderState":
        return cls(
            shop_order_number=str(data.get("shop_order_number", "")),
            gh_number=str(data.get("gh_number", "")),
            status=str(data.get("status", "")),
            files=list(data.get("files") or []),
            status_changed_at=data.get("status_changed_at"),
            shipped_at=data.get("shipped_at"),
        )

    @property
    def all_files_downloaded(self) -> bool:
        return bool(self.files) and all(f.get("state") == "downloaded" for f in self.files)

    def as_dict(self) -> dict:
        return {
            "shop_order_number": self.shop_order_number,
            "gh_number": self.gh_number,
            "status": self.status,
            "status_changed_at": self.status_changed_at,
            "shipped_at": self.shipped_at,
            "files": self.files,
        }


@dataclass
class ChangesResult:
    server_time: str
    orders: list[RemoteOrderState]


class PrintProvider(Protocol):
    name: str

    def submit(self, body: dict) -> RemoteOrderState: ...

    def fetch_changes(self, since) -> ChangesResult: ...

    def fetch_order(self, shop_order_number: str) -> RemoteOrderState: ...

    def resend_file(self, shop_order_number: str, body: dict) -> RemoteOrderState: ...


# ---------------------------------------------------------------------------
# Transporty
# ---------------------------------------------------------------------------


class InProcessTransport:
    """Żądania do atrapy GH w tym samym procesie Django, bez sieci i bez zależności od portu."""

    def __init__(self, base_path: str):
        from django.test import Client

        self.base_path = base_path.rstrip("/")
        self.client = Client(raise_request_exception=True)

    def request(self, method, path, token, body=None, params=None):
        url = f"{self.base_path}/{path.lstrip('/')}"
        kwargs = {"HTTP_AUTHORIZATION": f"Token {token}"}
        if method == "GET":
            response = self.client.get(url, data=params or {}, **kwargs)
        else:
            response = self.client.post(
                url,
                data=json.dumps(body or {}),
                content_type="application/json",
                **kwargs,
            )
        return response.status_code, _decode(response.content)


class HttpTransport:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method, path, token, body=None, params=None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
        try:
            response = requests.request(
                method, url, json=body, params=params, headers=headers, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise ProviderError(f"{method} {url}: {exc}") from exc
        return response.status_code, _decode(response.content)


def _decode(content: bytes):
    try:
        return json.loads(content.decode() or "null")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"raw": content[:300].decode(errors="replace")}


def default_transport():
    base = settings.GH_API_BASE_URL
    if base.startswith(("http://", "https://")):
        return HttpTransport(base)
    return InProcessTransport(base)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class GhApiProvider:
    name = "GhApiProvider"
    fail_next_requests = 0  # do testów: ile kolejnych żądań ma udać błąd przejściowy

    def __init__(self, transport=None, token=None):
        self.transport = transport or default_transport()
        self.token = token if token is not None else settings.GH_API_TOKEN

    def _request(self, method, path, body=None, params=None):
        if GhApiProvider.fail_next_requests > 0:
            GhApiProvider.fail_next_requests -= 1
            raise ProviderError("symulowany błąd połączenia z GH")
        status, data = self.transport.request(method, path, self.token, body=body, params=params)
        if status == 401:
            raise ProviderAuthError("GH odrzuciło token sklepu")
        if status in (400, 404, 409):
            errors = data.get("errors") or data.get("detail") if isinstance(data, dict) else data
            raise ProviderRejected(status, errors)
        if status == 429 or status >= 500:
            raise ProviderError(f"GH odpowiedziało HTTP {status}")
        return status, data

    def submit(self, body: dict) -> RemoteOrderState:
        status, data = self._request("POST", "orders/", body=body)
        log.info("GH: POST orders %s -> %s", body.get("shop_order_number"), status)
        return RemoteOrderState.from_payload(data)

    def fetch_changes(self, since) -> ChangesResult:
        params = {"since": since.isoformat()} if since else {}
        _, data = self._request("GET", "orders/changes/", params=params)
        return ChangesResult(
            server_time=data["server_time"],
            orders=[RemoteOrderState.from_payload(o) for o in data.get("orders", [])],
        )

    def fetch_order(self, shop_order_number: str) -> RemoteOrderState:
        _, data = self._request("GET", f"orders/{shop_order_number}/")
        return RemoteOrderState.from_payload(data)

    def resend_file(self, shop_order_number: str, body: dict) -> RemoteOrderState:
        _, data = self._request("POST", f"orders/{shop_order_number}/files/", body=body)
        return RemoteOrderState.from_payload(data)


def get_print_provider() -> PrintProvider:
    return import_string(settings.PRINT_PROVIDER)()
