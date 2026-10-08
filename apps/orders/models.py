import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Szkic"
    AWAITING_PAYMENT = "awaiting_payment", "Oczekuje na płatność"
    PAID = "paid", "Opłacone"
    PROCESSING = "processing", "Przetwarzanie pliku"
    AWAITING_APPROVAL = "awaiting_approval", "Czeka na zatwierdzenie"
    ON_HOLD = "on_hold", "Wstrzymane"
    SENT_TO_PRINTER = "sent_to_printer", "W drukarni"
    PRINTED = "printed", "Wydrukowane"
    SHIPPED = "shipped", "Nadane"
    DELIVERED = "delivered", "Doręczone"
    REPRINT = "reprint", "Dodruk"
    CANCELLED = "cancelled", "Anulowane"


class Actor(models.TextChoices):
    SYSTEM = "system", "System"
    OPERATOR = "operator", "Operator"
    PRINTER = "printer", "Drukarnia"
    CUSTOMER = "customer", "Klient"


def make_status_token():
    return secrets.token_hex(16)  # 32 znaki


class Order(models.Model):
    number = models.CharField(max_length=20, unique=True, blank=True, db_index=True)
    status = models.CharField(
        max_length=30, choices=OrderStatus.choices, default=OrderStatus.DRAFT, db_index=True
    )

    email = models.EmailField(blank=True)
    full_name = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    shipping_address = models.JSONField(default=dict, blank=True)
    delivery_method = models.ForeignKey(
        "catalog.DeliveryMethod",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    delivery_price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=3, default="PLN")

    status_token = models.CharField(
        max_length=32, unique=True, default=make_status_token, editable=False
    )
    consents = models.JSONField(default=dict, blank=True)
    hold_reason = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number or f"Order#{self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f"PA-{self.created_at.year}-{self.pk:06d}"
            super().save(update_fields=["number"])

    @property
    def item(self):
        return self.items.select_related("asset", "format", "paper").first()

    @property
    def is_draft(self):
        return self.status == OrderStatus.DRAFT

    @property
    def is_closed(self):
        return self.status in (OrderStatus.DELIVERED, OrderStatus.CANCELLED)

    @property
    def needs_attention(self):
        return (
            self.status in (OrderStatus.AWAITING_APPROVAL, OrderStatus.ON_HOLD)
            or self.complaints.filter(status=Complaint.Status.OPEN).exists()
        )

    @property
    def can_complain(self):
        return self.status == OrderStatus.DELIVERED

    def get_status_url(self):
        return reverse("web:order_status", args=[self.status_token])

    def get_absolute_status_url(self):
        return settings.SITE_URL + self.get_status_url()

    @property
    def latest_payment(self):
        return self.payments.order_by("-created_at").first()

    @property
    def latest_shipment(self):
        return self.shipments.order_by("-shipped_at").first()

    @property
    def shipping_address_lines(self):
        a = self.shipping_address or {}
        lines = [
            a.get("line1"),
            a.get("line2"),
            f"{a.get('postal_code', '')} {a.get('city', '')}".strip(),
            a.get("country"),
        ]
        return [line for line in lines if line]


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="order_items")
    format = models.ForeignKey(
        "catalog.Format", on_delete=models.PROTECT, related_name="order_items"
    )
    paper = models.ForeignKey(
        "catalog.PaperOption",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    white_margin = models.BooleanField(default=False)
    quantity = models.PositiveIntegerField(default=1)
    crop = models.JSONField(default=dict, blank=True)
    enhancer_enabled = models.BooleanField(default=False)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        return f"{self.order} / {self.format.code} x{self.quantity}"

    @property
    def line_total(self):
        return (Decimal(self.unit_price) * self.quantity).quantize(Decimal("0.01"))

    @property
    def orientation(self):
        return (self.crop or {}).get("orientation", "portrait")

    @property
    def print_file(self):
        return self.asset.version("print_pdf", self)

    @property
    def cropped_version(self):
        return self.asset.version("cropped", self)

    @property
    def upscaled_version(self):
        return self.asset.version("upscaled", self)


class OrderStatusEvent(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_events")
    from_status = models.CharField(max_length=30, choices=OrderStatus.choices, blank=True)
    to_status = models.CharField(max_length=30, choices=OrderStatus.choices)
    actor = models.CharField(max_length=20, choices=Actor.choices)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    reason = models.CharField(max_length=500, blank=True)
    forced = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.order}: {self.from_status} -> {self.to_status} ({self.actor})"


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Oczekuje"
        SUCCEEDED = "succeeded", "Zaksięgowana"
        FAILED = "failed", "Nieudana"
        REFUNDED = "refunded", "Zwrócona"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=50)
    external_id = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="PLN")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    events = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.provider} {self.external_id} ({self.status})"

    def log(self, kind, **data):
        self.events = list(self.events) + [{"kind": kind, "at": timezone.now().isoformat(), **data}]

    @property
    def is_mock(self):
        return self.provider.startswith("Mock")


class Shipment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="shipments")
    carrier = models.CharField(max_length=50, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    shipped_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.carrier} {self.tracking_number}"


class Complaint(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Otwarta"
        REPRINT = "reprint", "Uznana: dodruk"
        REJECTED = "rejected", "Odrzucona"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="complaints")
    description = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Reklamacja {self.order} ({self.status})"


def complaint_photo_upload_to(instance, filename):
    return f"complaints/{instance.complaint.order_id}/{secrets.token_hex(4)}_{filename}"


class ComplaintPhoto(models.Model):
    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=complaint_photo_upload_to)

    def __str__(self):
        return f"Zdjęcie reklamacji #{self.complaint_id}"
