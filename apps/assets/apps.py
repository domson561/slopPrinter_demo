from django.apps import AppConfig
from django.conf import settings


class AssetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.assets"
    verbose_name = "Pliki"

    def ready(self):
        # Bezpieczne otwieranie obrazów: limit pikseli chroni przed "decompression bomb".
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = settings.PIL_MAX_IMAGE_PIXELS
