"""
Serwer produkcyjny: ustawienia serwera publicznego (`public.py`) i prawdziwe GH, atrapa GH nigdy
nie zainstalowana.
"""

from django.core.exceptions import ImproperlyConfigured

from .public import *  # noqa: F403
from .public import required

# Prawdziwe GH: pełny adres https i token wydany przez GH. Bez tego produkcja po cichu
# wysyłałaby zlecenia do atrapy z tokenem demo.
GH_API_BASE_URL = required("GH_API_BASE_URL")
if not GH_API_BASE_URL.startswith("https://"):
    raise ImproperlyConfigured(
        "GH_API_BASE_URL w produkcji musi być adresem https:// prawdziwego GH, nie ścieżką atrapy."
    )
GH_API_TOKEN = required("GH_API_TOKEN")
GH_MOCK_ENABLED = False
