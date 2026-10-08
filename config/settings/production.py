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
