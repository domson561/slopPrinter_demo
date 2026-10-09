"""
Ustawienia wspólne dla wszystkich środowisk. Wszystko, co zależy od środowiska, czytane
jest ze zmiennych środowiskowych. SECRET_KEY, DEBUG i ALLOWED_HOSTS ustawiają
`development.py` i `production.py`.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(name, default=None):
    value = os.environ.get(name)
    return default if value in (None, "") else value


def env_bool(name, default):
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Nazwa robocza serwisu - jedno miejsce, w szablonach {{ site_name }}.
SITE_NAME = env("SITE_NAME", "NAZWA_ROBOCZA")
# Bazowy URL do linków w e-mailach (poza requestem).
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")
# Prefiks numeru zamówienia (`PA-2026-000123`). Numer jest kluczem idempotencji w GH, więc każdy
# sklep podpięty do tego samego GH musi mieć inny prefiks.
ORDER_NUMBER_PREFIX = env("ORDER_NUMBER_PREFIX", "PA")
# Produkty sklepu (interfejs `apps.products.Product`). Rdzeń zna je tylko przez rejestr.
SHOP_PRODUCTS = ["apps.poster.product.PosterProduct"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "huey.contrib.djhuey",
    "apps.catalog",
    "apps.assets",
    "apps.orders",
    "apps.printing",
    "apps.ops",
    "apps.web",
    "apps.demo",
]
# Atrapa GH (`apps.gh_mock`, API /shop-api/v1/ i pulpit /gh-mock/) dochodzi w `development.py`
# pod flagą GH_MOCK_ENABLED. Produkcja nigdy jej nie instaluje.

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.web.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if env("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "slop"),
            "USER": env("POSTGRES_USER", "slop"),
            "PASSWORD": env("POSTGRES_PASSWORD", "slop"),
            "HOST": env("POSTGRES_HOST"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = []  # demo

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", BASE_DIR / "media"))

LOGIN_URL = "/ops/login/"
LOGIN_REDIRECT_URL = "/ops/"
LOGOUT_REDIRECT_URL = "/ops/login/"

# E-maile w demo trafiają do konsoli / logu.
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "zamowienia@example.invalid")

# --- Upload -----------------------------------------------------------------
UPLOAD_MAX_BYTES = 30 * 1024 * 1024
UPLOAD_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
DATA_UPLOAD_MAX_MEMORY_SIZE = UPLOAD_MAX_BYTES + 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
PIL_MAX_IMAGE_PIXELS = 80_000_000  # ok. 9000 x 9000 px

# --- Mocki ------------------------------------------------------------------
MOCK_UPSCALE_DELAY_SECONDS = tuple(
    float(x) for x in env("MOCK_UPSCALE_DELAY_SECONDS", "3,8").split(",")
)
PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", "apps.orders.payments.MockPaymentProvider")

# --- Graphic House (kontrakt v1, docs/kontrakt-gh-v1.md) ----------------------
PRINT_PROVIDER = env("PRINT_PROVIDER", "apps.printing.providers.GhApiProvider")
# Ścieżka = atrapa GH w tym procesie (apps.gh_mock); pełny URL http(s) = prawdziwe GH.
GH_API_BASE_URL = env("GH_API_BASE_URL", "/shop-api/v1")
# Token wydaje GH przy podpinaniu sklepu. Atrapa zna ten sam token (seed_demo).
GH_API_TOKEN = env("GH_API_TOKEN", "demo-shop-token")
GH_FILE_LINK_TTL_HOURS = 24  # nominalna ważność linku do pliku (pkt 4)
PRINT_SUBMIT_MAX_ATTEMPTS = 5
PRINT_SUBMIT_RETRY_BACKOFF_SECONDS = 0.5
# Atrapa GH: limit rozmiaru pobieranego PDF-a.
GH_MOCK_MAX_FILE_BYTES = 500 * 1024 * 1024

# --- Zadania w tle (huey) ---------------------------------------------------
if env("REDIS_URL"):
    HUEY = {
        "huey_class": "huey.RedisHuey",
        "name": "slop_printer",
        "url": env("REDIS_URL"),
        "immediate": env_bool("HUEY_IMMEDIATE", False),
        "consumer": {"workers": 2},
    }
else:
    HUEY = {
        "huey_class": "huey.SqliteHuey",
        "name": "slop_printer",
        "filename": str(BASE_DIR / "huey.db"),
        "immediate": env_bool("HUEY_IMMEDIATE", True),
        "consumer": {"workers": 2},
    }

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"slop": {"level": "DEBUG", "propagate": True}},
}
