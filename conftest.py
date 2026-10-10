import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from PIL import Image, ImageDraw


@pytest.fixture(autouse=True)
def _fast_demo_settings(settings, tmp_path):
    """Testy: bez sztucznych opóźnień mocków, pliki w katalogu tymczasowym, e-maile w pamięci."""
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "private_media"
    settings.MOCK_UPSCALE_DELAY_SECONDS = (0, 0)
    settings.PRINT_SUBMIT_RETRY_BACKOFF_SECONDS = 0
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    yield


@pytest.fixture(autouse=True)
def _reset_gh_provider():
    from apps.printing.providers import GhApiProvider

    GhApiProvider.fail_next_requests = 0
    yield
    GhApiProvider.fail_next_requests = 0


@pytest.fixture
def catalog_data(db):
    """Ustawienia rdzenia, katalog plakatu i sklep demo w atrapie GH (token z GH_API_TOKEN)."""
    from apps.gh_mock.services import ensure_demo_shop

    call_command("loaddata", "catalog.json", "poster.json", verbosity=0)
    ensure_demo_shop()


def make_image_upload(name="test.png", size=(800, 1200), fmt="PNG"):
    img = Image.new("RGB", size, (120, 80, 200))
    d = ImageDraw.Draw(img)
    d.rectangle([size[0] // 4, size[1] // 4, size[0] // 2, size[1] // 2], fill=(240, 200, 40))
    buf = io.BytesIO()
    img.save(buf, fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=f"image/{fmt.lower()}")


@pytest.fixture
def image_upload():
    return make_image_upload
