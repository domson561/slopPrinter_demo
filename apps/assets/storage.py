import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """
    Pliki od kupujących (oryginały, zdjęcia do reklamacji) w `PRIVATE_MEDIA_ROOT`, poza publicznym
    `/media/`. Nie mają adresu URL: wydaje je tylko widok z kontrolą dostępu. Katalog czytany
    z ustawień przy każdym dostępie, żeby testy mogły go podmienić.
    """

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError("Plik prywatny nie ma publicznego adresu; wydaje go widok.")


private_storage = PrivateStorage()
