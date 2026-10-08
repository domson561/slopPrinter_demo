import uuid

from django.db import models
from django.utils import timezone


class PrintJob(models.Model):
    """Zlecenie do drukarni dla jednej pozycji zamówienia."""

    class Status(models.TextChoices):
        CREATED = "created", "Utworzone"
        SENT = "sent", "Wysłane"
        ACCEPTED = "accepted", "Przyjęte"
        PRINTED = "printed", "Wydrukowane"
        SHIPPED = "shipped", "Nadane"
        REJECTED = "rejected", "Odrzucone"
        FAILED = "failed", "Błąd / wada"
        CANCELLED = "cancelled", "Anulowane"

    order_item = models.ForeignKey(
        "orders.OrderItem", on_delete=models.CASCADE, related_name="print_jobs"
    )
    external_id = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True
    )
    print_file = models.ForeignKey(
        "assets.AssetVersion", null=True, blank=True, on_delete=models.SET_NULL
    )
    spec = models.JSONField(default=dict, blank=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    reprint_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reprints"
    )
    reason = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"pj_{self.pk} ({self.status})"

    @property
    def job_id(self):
        return f"pj_{self.idempotency_key.hex[:12]}"

    @property
    def order(self):
        return self.order_item.order

    @property
    def is_open(self):
        return self.status in (
            self.Status.CREATED,
            self.Status.SENT,
            self.Status.ACCEPTED,
            self.Status.PRINTED,
        )


class PrintJobEvent(models.Model):
    print_job = models.ForeignKey(PrintJob, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=20)
    payload = models.JSONField(default=dict, blank=True)
    applied = models.BooleanField(default=True)
    note = models.CharField(max_length=300, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["received_at", "pk"]

    def __str__(self):
        return f"{self.print_job} <- {self.status}"


class MockPrinterJob(models.Model):
    """
    "Tabela drukarni": to, co widzi symulowany pulpit D1. Celowo osobny model, żeby
    kontrakt (spec JSON + webhook) był widoczny, a nie skrót po stronie serwisu.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", "Otrzymane"
        ACCEPTED = "accepted", "Przyjęte"
        PRINTED = "printed", "Wydrukowane"
        SHIPPED = "shipped", "Nadane"
        REJECTED = "rejected", "Odrzucone"
        DEFECT = "defect", "Wada produkcyjna"
        CANCELLED = "cancelled", "Anulowane"

    external_id = models.CharField(max_length=100, unique=True)
    idempotency_key = models.CharField(max_length=100, unique=True)
    spec = models.JSONField(default=dict)
    file_url = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    tracking_number = models.CharField(max_length=100, blank=True)
    history = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.external_id} ({self.status})"

    def log(self, message, **data):
        self.history = list(self.history) + [
            {"at": timezone.now().isoformat(), "message": message, **data}
        ]

    @property
    def next_actions(self):
        """Jakie przyciski pokazać w D1 dla danego stanu."""
        return {
            self.Status.RECEIVED: ["accept", "reject"],
            self.Status.ACCEPTED: ["printed", "reject"],
            self.Status.PRINTED: ["shipped", "defect"],
            self.Status.SHIPPED: ["delivered"],
        }.get(self.status, [])
