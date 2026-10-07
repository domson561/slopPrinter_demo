"""Lokalna praca i testy: wygodne wartości domyślne, wszystko da się nadpisać zmiennymi."""

from .base import *  # noqa: F403
from .base import env, env_bool, env_list

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-secret-key-tylko-lokalnie")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "*")
