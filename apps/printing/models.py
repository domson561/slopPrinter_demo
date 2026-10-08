import uuid

from django.db import models


class PrintJob(models.Model):
    """
    Zlecenie do GH dla całego zamówienia: koszyk = jedno zlecenie, pozycje listą
    w `spec["items"]` (kontrakt v1, `docs/kontrakt-gh-v1.md`). `spec` to dokładnie body
    wysłanego POST-a. `external_id` to numer zlecenia nadany przez GH (`N/ROK`).
    """

    class Status(models.TextChoices):
        CREATED = "created", "Utworzone"
        SENT = "sent", "Wysłane"
        ACCEPTED = "accepted", "Przyjęte"
        PRINTED = "printed", "Wydrukowane"
        SHIPPED = "shipped", "Nadane"
        REJECTED = "rejected", "Odrzucone"
        FAILED = "failed", "Błąd / wada"
        CANCELLED = "cancelled", "Anulowane"

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="print_jobs")
    shop_order_number = models.CharField(
        max_length=40, unique=True, help_text="Klucz idempotencji w GH: numer zamówienia sklepu."
    )
    external_id = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True
    )
    provider_status = models.CharField(max_length=20, blank=True, help_text="Status w GH.")
    files_state = models.JSONField(default=list, blank=True, help_text="Stany plików z GH.")
    spec = models.JSONField(default=dict, blank=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
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
        return f"{self.shop_order_number} ({self.status})"

    @property
    def job_id(self):
        return self.shop_order_number

    @property
    def is_open(self):
        return self.status in (
            self.Status.CREATED,
            self.Status.SENT,
            self.Status.ACCEPTED,
            self.Status.PRINTED,
        )

    @property
    def all_files_downloaded(self) -> bool:
        return bool(self.files_state) and all(
            f.get("state") == "downloaded" for f in self.files_state
        )

    @property
    def failed_files(self) -> list[dict]:
        return [f for f in self.files_state if f.get("state") == "failed"]


class PrintJobEvent(models.Model):
    """Historia: wysłanie, każda zmiana odczytana z GH przy pollingu, dosłania linków."""

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


class PollCursor(models.Model):
    """Kursor pollingu GH: `since` to `server_time` z ostatniej odpowiedzi, nie zegar sklepu."""

    since = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    auth_failed = models.BooleanField(
        default=False, help_text="401 z GH: polling wstrzymany do interwencji operatora."
    )

    class Meta:
        verbose_name = "Kursor pollingu GH"

    def __str__(self):
        return f"since={self.since} auth_failed={self.auth_failed}"

    @classmethod
    def get(cls):
        cursor, _ = cls.objects.get_or_create(pk=1)
        return cursor
