import importlib
import sys

import pytest
from django.core.exceptions import ImproperlyConfigured

REQUIRED = {
    "DJANGO_SECRET_KEY": "test-secret",
    "DJANGO_ALLOWED_HOSTS": "sklep.pl, www.sklep.pl",
    "GH_API_BASE_URL": "https://gh.example.pl/shop-api/v1",
    "GH_API_TOKEN": "token-wydany-przez-gh",
    "SITE_URL": "https://sklep.pl/",
}
OPTIONAL = ("GH_MOCK_ENABLED",)


def load_settings(monkeypatch, module, env):
    for name in (*REQUIRED, *OPTIONAL):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delitem(sys.modules, f"config.settings.{module}", raising=False)
    return importlib.import_module(f"config.settings.{module}")


@pytest.mark.parametrize("missing", list(REQUIRED))
def test_production_refuses_to_start_without_required_env(monkeypatch, missing):
    env = {name: value for name, value in REQUIRED.items() if name != missing}
    with pytest.raises(ImproperlyConfigured, match=missing):
        load_settings(monkeypatch, "production", env)


@pytest.mark.parametrize("base_url", ["/shop-api/v1", "http://gh.example.pl/shop-api/v1"])
def test_production_refuses_gh_mock_path_or_plain_http(monkeypatch, base_url):
    with pytest.raises(ImproperlyConfigured, match="GH_API_BASE_URL"):
        load_settings(monkeypatch, "production", {**REQUIRED, "GH_API_BASE_URL": base_url})


@pytest.mark.parametrize("site_url", ["http://sklep.pl", "localhost:8000"])
def test_production_refuses_site_url_without_https(monkeypatch, site_url):
    with pytest.raises(ImproperlyConfigured, match="SITE_URL"):
        load_settings(monkeypatch, "production", {**REQUIRED, "SITE_URL": site_url})


def test_production_reads_env_and_disables_debug(monkeypatch):
    prod = load_settings(monkeypatch, "production", REQUIRED)
    assert prod.SECRET_KEY == "test-secret"
    assert prod.ALLOWED_HOSTS == ["sklep.pl", "www.sklep.pl"]
    assert prod.DEBUG is False
    assert prod.GH_API_BASE_URL == "https://gh.example.pl/shop-api/v1"
    assert prod.GH_API_TOKEN == "token-wydany-przez-gh"
    assert prod.SITE_URL == "https://sklep.pl"


def test_production_never_installs_gh_mock(monkeypatch):
    prod = load_settings(monkeypatch, "production", {**REQUIRED, "GH_MOCK_ENABLED": "1"})
    assert prod.GH_MOCK_ENABLED is False
    assert "apps.gh_mock" not in prod.INSTALLED_APPS


def test_development_installs_gh_mock_unless_disabled(monkeypatch):
    dev = load_settings(monkeypatch, "development", {})
    assert dev.GH_MOCK_ENABLED is True
    assert "apps.gh_mock" in dev.INSTALLED_APPS

    dev = load_settings(monkeypatch, "development", {"GH_MOCK_ENABLED": "0"})
    assert dev.GH_MOCK_ENABLED is False
    assert "apps.gh_mock" not in dev.INSTALLED_APPS


def test_production_forces_https_and_secure_cookies(monkeypatch):
    prod = load_settings(monkeypatch, "production", {**REQUIRED, "DJANGO_HSTS_SECONDS": "3600"})
    assert prod.SECURE_SSL_REDIRECT is True
    assert prod.SESSION_COOKIE_SECURE is True
    assert prod.CSRF_COOKIE_SECURE is True
    assert prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert prod.SECURE_HSTS_SECONDS == 3600
