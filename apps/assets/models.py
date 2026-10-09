import os
import uuid

from django.db import models
from django.utils import timezone
from PIL import Image, ImageOps

from .storage import private_storage


def original_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    return f"assets/{instance.pk}/original{ext}"


def version_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    return f"assets/{instance.asset_id}/{instance.kind}_{uuid.uuid4().hex[:8]}{ext}"


class Asset(models.Model):
    """
    Wgrany przez klienta plik, już po bramce (`gate`). Identyfikator jest UUID, żeby URL-e nie były
    enumerowalne. Oryginał leży w prywatnym katalogu; wersje (podgląd, PDF) na razie w `/media/`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_file = models.FileField(upload_to=original_upload_to, storage=private_storage)
    original_filename = models.CharField(max_length=255, blank=True)
    mime = models.CharField(max_length=50)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_filename or self.pk} ({self.width}x{self.height})"

    @property
    def aspect_ratio(self):
        return self.width / self.height if self.height else 0

    @property
    def is_landscape(self):
        return self.width > self.height

    @property
    def natural_orientation(self):
        return "landscape" if self.is_landscape else "portrait"

    def open_image(self) -> Image.Image:
        """Otwiera oryginał, obraca wg EXIF, konwertuje do RGB."""
        self.original_file.open("rb")
        try:
            img = Image.open(self.original_file)
            img.load()
        finally:
            # Po load() piksele są w pamięci; otwarty uchwyt blokowałby usuwanie pliku na Windowsie.
            self.original_file.close()
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img

    def version(self, kind, order_item=None):
        qs = self.versions.filter(kind=kind)
        if order_item is not None:
            qs = qs.filter(order_item=order_item)
        return qs.order_by("-created_at").first()

    @property
    def preview(self):
        return self.version(AssetVersion.Kind.PREVIEW)

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()


class AssetAnalysis(models.Model):
    asset = models.OneToOneField(Asset, on_delete=models.CASCADE, related_name="analysis")
    aspect_ratio = models.FloatField()
    content_flags = models.JSONField(default=list, blank=True)
    quality_per_format = models.JSONField(default=dict, blank=True)
    analyzed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Asset analyses"

    def __str__(self):
        return f"Analiza {self.asset_id}"

    @property
    def has_flags(self):
        return bool(self.content_flags)

    def quality(self, format_code):
        return self.quality_per_format.get(format_code)


class AssetVersion(models.Model):
    class Kind(models.TextChoices):
        ORIGINAL = "original", "Oryginał"
        PREVIEW = "preview", "Podgląd"
        UPSCALED = "upscaled", "Po upscale"
        CROPPED = "cropped", "Po kadrowaniu"
        PRINT_PDF = "print_pdf", "PDF do druku"

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="versions")
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="asset_versions",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file = models.FileField(upload_to=version_upload_to)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    provider = models.CharField(max_length=100, blank=True)
    params = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.asset_id}"

    @property
    def is_mock(self):
        return self.provider.startswith("Mock")

    @property
    def size_bytes(self):
        try:
            return self.file.size
        except (OSError, ValueError):
            return 0
