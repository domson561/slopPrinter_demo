from decimal import Decimal

from django.db import models

MM_PER_INCH = Decimal("25.4")


class Format(models.Model):
    """Format wydruku. Wymiary podawane w orientacji pionowej (width < height)."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    width_mm = models.PositiveIntegerField()
    height_mm = models.PositiveIntegerField()
    bleed_mm = models.PositiveIntegerField(default=3)
    base_price = models.DecimalField(max_digits=8, decimal_places=2)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self):
        return f"{self.name} ({self.width_mm}x{self.height_mm} mm)"

    @property
    def width_in(self):
        return float(self.width_mm) / float(MM_PER_INCH)

    @property
    def height_in(self):
        return float(self.height_mm) / float(MM_PER_INCH)

    @property
    def ratio(self):
        """Szerokość / wysokość w orientacji pionowej."""
        return self.width_mm / self.height_mm

    def dims_mm(self, orientation="portrait"):
        if orientation == "landscape":
            return self.height_mm, self.width_mm
        return self.width_mm, self.height_mm


class PaperOption(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    price_delta = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self):
        return self.name


class DeliveryMethod(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self):
        return self.name


class ShopSettings(models.Model):
    """Ustawienia demo (singleton, edytowane w panelu operatora O5)."""

    dpi_sharp = models.PositiveIntegerField(default=200, help_text="Próg 'ostry' (DPI).")
    dpi_ok = models.PositiveIntegerField(default=120, help_text="Próg 'ok' (DPI).")
    upscale_factor = models.PositiveIntegerField(
        default=4, help_text="Mnożnik enhancera; 'wymaga poprawy' gdy dpi * mnożnik >= próg 'ok'."
    )
    auto_approve = models.BooleanField(
        default=False, help_text="Po udanym pipeline wysyłaj do drukarni bez moderacji."
    )
    lead_time_days = models.PositiveIntegerField(
        default=5, help_text="Deklarowany czas realizacji pokazywany klientowi [ZAŁOŻENIE]."
    )
    retention_days_after_close = models.PositiveIntegerField(default=30)
    draft_retention_days = models.PositiveIntegerField(default=7)
    currency = models.CharField(max_length=3, default="PLN")

    class Meta:
        verbose_name = "Ustawienia sklepu"
        verbose_name_plural = "Ustawienia sklepu"

    def __str__(self):
        return "Ustawienia sklepu"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def dpi_needs_enhance(self):
        """Poniżej tego DPI nawet enhancer nie da progu 'ok'."""
        return self.dpi_ok / max(self.upscale_factor, 1)
