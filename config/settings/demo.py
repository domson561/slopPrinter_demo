"""
Demo na publicznym serwerze (np. Railway): zabezpieczenia jak w produkcji (`public.py`), ale zamiast
prawdziwego GH atrapa w tym procesie. Przed Django nie stoi serwer www: pliki statyczne wydaje
whitenoise, `/media/` widok Django.
"""

from .public import *  # noqa: F403
from .public import ALLOWED_HOSTS, INSTALLED_APPS, MIDDLEWARE

# Atrapa GH jak w `development.py`: API /shop-api/v1/ i pulpit /gh-mock/ w tym procesie. Token
# z GH_API_TOKEN (domyślnie demo, ten sam ustawia atrapie `seed_demo`).
GH_MOCK_ENABLED = True
INSTALLED_APPS = [*INSTALLED_APPS, "apps.gh_mock"]
GH_API_BASE_URL = "/shop-api/v1"

# Pliki statyczne: whitenoise zaraz za SecurityMiddleware, przy collectstatic nazwy z hashem i kompresja.
_security = MIDDLEWARE.index("django.middleware.security.SecurityMiddleware")
MIDDLEWARE = [
    *MIDDLEWARE[: _security + 1],
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[_security + 1 :],
]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# Plik spoza manifestu (np. dodany bez collectstatic) dostaje adres bez hasha zamiast błędu 500.
WHITENOISE_MANIFEST_STRICT = False

# `/media/` (podglądy, PDF-y do druku) z dysku przez widok Django: wolniej niż serwer www, w demo wystarcza.
SERVE_MEDIA = True

# Healthcheck Railway przychodzi z tego hosta, po HTTP i bez X-Forwarded-Proto, więc bez
# przekierowania na https.
ALLOWED_HOSTS = [*ALLOWED_HOSTS, "healthcheck.railway.app"]
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
