"""
Serwer produkcyjny: bez wartości domyślnych dla sekretów, hostów i GH, DEBUG zawsze wyłączony,
atrapa GH nigdy nie zainstalowana.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env, env_list


def required(name):
    value = env(name)
    if value is None:
        raise ImproperlyConfigured(f"Brak zmiennej środowiskowej {name} (wymagana w produkcji).")
    return value


SECRET_KEY = required("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "Brak zmiennej środowiskowej DJANGO_ALLOWED_HOSTS (wymagana w produkcji)."
    )

# Prawdziwe GH: pełny adres https i token wydany przez GH. Bez tego produkcja po cichu
# wysyłałaby zlecenia do atrapy z tokenem demo.
GH_API_BASE_URL = required("GH_API_BASE_URL")
if not GH_API_BASE_URL.startswith("https://"):
    raise ImproperlyConfigured(
        "GH_API_BASE_URL w produkcji musi być adresem https:// prawdziwego GH, nie ścieżką atrapy."
    )
GH_API_TOKEN = required("GH_API_TOKEN")
GH_MOCK_ENABLED = False

# Publiczny adres sklepu: idzie do GH w linkach do plików i do kupujących w mailach. Z domyślnym
# `http://localhost:8000` GH nie pobrałoby żadnego pliku, a zlecenia stałyby na `pending`.
SITE_URL = required("SITE_URL").rstrip("/")
if not SITE_URL.startswith("https://"):
    raise ImproperlyConfigured("SITE_URL w produkcji musi być publicznym adresem https:// sklepu.")

# Pliki od kupujących: serwer www wystawia MEDIA_ROOT pod /media/, więc katalog prywatny
# w jego wnętrzu byłby publiczny.
if PRIVATE_MEDIA_ROOT.resolve().is_relative_to(MEDIA_ROOT.resolve()):  # noqa: F405
    raise ImproperlyConfigured("PRIVATE_MEDIA_ROOT nie może leżeć wewnątrz MEDIA_ROOT.")

# HTTPS: aplikacja stoi za reverse proxy, które kończy TLS i ustawia X-Forwarded-Proto.
# `manage.py check --deploy` ma przechodzić bez ostrzeżeń.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# HSTS: 30 dni domyślnie; nagłówek `preload` jest tylko deklaracją, wpis na listę preload to osobna decyzja.
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", 60 * 60 * 24 * 30))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
