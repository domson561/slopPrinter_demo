"""Serwer produkcyjny: bez wartości domyślnych dla sekretów i hostów, DEBUG zawsze wyłączony."""

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
    raise ImproperlyConfigured("Brak zmiennej środowiskowej DJANGO_ALLOWED_HOSTS (wymagana w produkcji).")
