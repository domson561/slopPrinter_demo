from django.db import models


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
    """
    Ustawienia rdzenia sklepu (singleton, panel O5). Ustawienia produktu trzyma jego aplikacja,
    np. `poster.PosterSettings`.
    """

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
