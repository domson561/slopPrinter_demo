import importlib
import sys

import pytest
from django.core.exceptions import ImproperlyConfigured

REQUIRED = {"DJANGO_SECRET_KEY": "test-secret", "DJANGO_ALLOWED_HOSTS": "sklep.pl, www.sklep.pl"}


def load_production(monkeypatch, env):
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delitem(sys.modules, "config.settings.production", raising=False)
    return importlib.import_module("config.settings.production")


@pytest.mark.parametrize("missing", list(REQUIRED))
def test_production_refuses_to_start_without_required_env(monkeypatch, missing):
    env = {name: value for name, value in REQUIRED.items() if name != missing}
    with pytest.raises(ImproperlyConfigured, match=missing):
        load_production(monkeypatch, env)


def test_production_reads_env_and_disables_debug(monkeypatch):
    prod = load_production(monkeypatch, REQUIRED)
    assert prod.SECRET_KEY == "test-secret"
    assert prod.ALLOWED_HOSTS == ["sklep.pl", "www.sklep.pl"]
    assert prod.DEBUG is False
