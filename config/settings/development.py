"""Lokalna praca i testy: wygodne wartości domyślne, wszystko da się nadpisać zmiennymi."""

from .base import *  # noqa: F403
from .base import INSTALLED_APPS, MIDDLEWARE, env, env_bool, env_list

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-secret-key-tylko-lokalnie")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "*")
HOME_BACKGROUND_SWITCH = env_bool("HOME_BACKGROUND_SWITCH", DEBUG)

# Pliki statyczne z whitenoise zamiast runservera, jak w `demo.py`. Przy DEBUG wysyła max-age=0,
# więc przeglądarka przy każdym wczytaniu pyta o zmiany (304, gdy bez zmian) i podmieniony CSS
# albo obraz pod tą samą nazwą widać bez Ctrl+F5. Runserver wydaje je bez Cache-Control i
# przeglądarka trzymała stare kopie. `runserver_nostatic` musi stać przed `staticfiles`.
INSTALLED_APPS = ["whitenoise.runserver_nostatic", *INSTALLED_APPS]
_security = MIDDLEWARE.index("django.middleware.security.SecurityMiddleware")
MIDDLEWARE = [
    *MIDDLEWARE[: _security + 1],
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[_security + 1 :],
]
# Tryb deweloperski na sztywno: pytest-django wyłącza DEBUG w locie i whitenoise szukałby wtedy
# staticfiles/ po collectstatic, którego lokalnie nie ma.
WHITENOISE_AUTOREFRESH = WHITENOISE_USE_FINDERS = DEBUG

# Atrapa GH w tym procesie: API /shop-api/v1/ i pulpit /gh-mock/. Wyłączenie (GH_MOCK_ENABLED=0)
# ma sens tylko z prawdziwym GH w GH_API_BASE_URL.
GH_MOCK_ENABLED = env_bool("GH_MOCK_ENABLED", True)
if GH_MOCK_ENABLED:
    INSTALLED_APPS = [*INSTALLED_APPS, "apps.gh_mock"]
