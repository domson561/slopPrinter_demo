"""
Atrapa Graphic House: to, co GH widzi po swojej stronie. Osobna aplikacja, żeby kontrakt
(`docs/kontrakt-gh-v1.md`) był widoczny jako HTTP, a nie skrót w serwisie sklepu.
Kształt pól wzięty z modelu `Order` w GH (`new`, `in_progress`, `ready`, `shipped`).
Prawdziwe GH trzyma pobrane PDF-y na prywatnym wolumenie; atrapa tylko weryfikuje
pobranie (nagłówek, rozmiar, suma) i nie zapisuje bajtów.
"""

import hashlib

from django.db import models
from django.utils import timezone


class GhShop(models.Model):
    """Sklep podpięty do GH: token przechowywany jako hash, cofany flagą `is_active`."""

    name = models.CharField(max_length=80)
    code = models.CharField(max_length=10, unique=True)
    token_hash = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} {self.name}"

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def authenticate(cls, token: str):
        if not token:
            return None
        return cls.objects.filter(token_hash=cls.hash_token(token), is_active=True).first()


class GhOrder(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Nowe"
        IN_PROGRESS = "in_progress", "W realizacji"
        READY = "ready", "Gotowe"
        SHIPPED = "shipped", "Wysłane"

    shop = models.ForeignKey(GhShop, on_delete=models.PROTECT, related_name="orders")
    shop_order_number = models.CharField(max_length=40)
    gh_number = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    total_value = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="PLN")
    note_for_production = models.CharField(max_length=500, blank=True)
    body_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    status_changed_at = models.DateTimeField(default=timezone.now)
    shipped_at = models.DateTimeField(null=True, blank=True)
    needs_review = models.BooleanField(default=False, help_text="Nieznany product_code.")
    changed_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "shop_order_number"], name="gh_order_unique_per_shop"
            )
        ]

    def __str__(self):
        return f"{self.gh_number} ({self.shop_order_number})"

    def touch(self):
        self.changed_at = timezone.now()

    @property
    def all_files_downloaded(self) -> bool:
        return not self.files.exclude(state=GhOrderFile.State.DOWNLOADED).exists()

    @property
    def next_status(self):
        return {
            self.Status.NEW: self.Status.IN_PROGRESS,
            self.Status.IN_PROGRESS: self.Status.READY,
            self.Status.READY: self.Status.SHIPPED,
        }.get(self.status)

    def to_state(self) -> dict:
        """Element `orders[]` z pkt 5 kontraktu."""
        return {
            "shop_order_number": self.shop_order_number,
            "gh_number": self.gh_number,
            "status": self.status,
            "status_changed_at": self.status_changed_at.isoformat(),
            "shipped_at": self.shipped_at.isoformat() if self.shipped_at else None,
            "files": [f.to_state() for f in self.files.all()],
        }


class GhOrderItem(models.Model):
    order = models.ForeignKey(GhOrder, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    product_code = models.CharField(max_length=60)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["order", "position"], name="gh_item_unique_position")
        ]

    def __str__(self):
        return f"{self.position}. {self.name} ×{self.quantity}"


class GhOrderFile(models.Model):
    class Kind(models.TextChoices):
        ITEM = "item", "Plik pozycji"
        LABEL = "label", "Etykieta"

    class State(models.TextChoices):
        PENDING = "pending", "Do pobrania"
        DOWNLOADED = "downloaded", "Pobrany"
        FAILED = "failed", "Błąd"

    ERRORS = ("expired", "checksum_mismatch", "not_pdf", "too_large", "unreachable")

    order = models.ForeignKey(GhOrder, on_delete=models.CASCADE, related_name="files")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    position = models.PositiveIntegerField(null=True, blank=True)
    url = models.CharField(max_length=1000)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    expires_at = models.DateTimeField()
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    error = models.CharField(max_length=30, blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["kind", "position"]

    def __str__(self):
        where = f"poz. {self.position}" if self.kind == self.Kind.ITEM else "etykieta"
        return f"{self.order.gh_number} {where}: {self.state}"

    def to_state(self) -> dict:
        state = {"kind": self.kind, "state": self.state}
        if self.kind == self.Kind.ITEM:
            state["position"] = self.position
        if self.state == self.State.FAILED:
            state["error"] = self.error
        return state
