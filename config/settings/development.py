"""Lokalna praca i testy: wygodne wartości domyślne, wszystko da się nadpisać zmiennymi."""

from .base import *  # noqa: F403
from .base import INSTALLED_APPS, env, env_bool, env_list

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-secret-key-tylko-lokalnie")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "*")

# Atrapa GH w tym procesie: API /shop-api/v1/ i pulpit /gh-mock/. Wyłączenie (GH_MOCK_ENABLED=0)
# ma sens tylko z prawdziwym GH w GH_API_BASE_URL.
GH_MOCK_ENABLED = env_bool("GH_MOCK_ENABLED", True)
if GH_MOCK_ENABLED:
    INSTALLED_APPS = [*INSTALLED_APPS, "apps.gh_mock"]
