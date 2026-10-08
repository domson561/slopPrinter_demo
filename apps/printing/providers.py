"""
Providery drukarni za wspólnym interfejsem. W demo MockPrintProvider [DECYZJA na demo].
Pozostałe klasy to puste szkielety do wyboru po rozmowie z drukarnią [ZAŁOŻENIE: sposób przyjmowania zleceń].
"""

import secrets
from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string

from .models import MockPrinterJob, PrintJob


class ProviderError(Exception):
    """Błąd przejściowy po stronie providera (sieć, 5xx). Serwis ponawia z backoffem."""


class PrintProvider(Protocol):
    name: str

    def submit(self, job: PrintJob) -> str: ...  # zwraca external_id

    def cancel(self, external_id: str) -> None: ...

    def fetch_status(self, external_id: str) -> str: ...  # pull, opcjonalne


class MockPrintProvider:
    """
    Zapisuje zlecenie do tabeli widocznej w D1. Idempotentne: ten sam idempotency_key
    zwraca ten sam external_id i nie tworzy drugiego zlecenia.
    """

    name = "MockPrintProvider"
    fail_next_submits = 0  # do testów: ile kolejnych submitów ma rzucić ProviderError

    def submit(self, job: PrintJob) -> str:
        if MockPrintProvider.fail_next_submits > 0:
            MockPrintProvider.fail_next_submits -= 1
            raise ProviderError("symulowany błąd połączenia z drukarnią")
        mock_job, created = MockPrinterJob.objects.get_or_create(
            idempotency_key=str(job.idempotency_key),
            defaults={
                "external_id": f"MOCK-{secrets.token_hex(4).upper()}",
                "spec": job.spec,
                "file_url": (job.spec.get("file") or {}).get("url", ""),
                "status": MockPrinterJob.Status.RECEIVED,
            },
        )
        if created:
            mock_job.log("otrzymano zlecenie", idempotency_key=str(job.idempotency_key))
        else:
            mock_job.log("ponowny submit z tym samym idempotency_key, zwracam istniejące zlecenie")
        mock_job.save(update_fields=["history", "updated_at"])
        return mock_job.external_id

    def cancel(self, external_id: str) -> None:
        mock_job = MockPrinterJob.objects.filter(external_id=external_id).first()
        if mock_job and mock_job.status in (
            MockPrinterJob.Status.RECEIVED,
            MockPrinterJob.Status.ACCEPTED,
        ):
            mock_job.status = MockPrinterJob.Status.CANCELLED
            mock_job.log("anulowane przez serwis")
            mock_job.save(update_fields=["status", "history", "updated_at"])

    def fetch_status(self, external_id: str) -> str:
        mock_job = MockPrinterJob.objects.filter(external_id=external_id).first()
        return mock_job.status if mock_job else "unknown"


class HotFolderPrintProvider:
    """
    Wariant: drukarnia odbiera zlecenia z katalogu (SFTP/udział sieciowy): PDF + plik JSON ze specyfikacją.
    Statusy wracałyby przez pliki `.status` albo e-mail. Do implementacji po ustaleniach z drukarnią.
    """

    name = "HotFolderPrintProvider"

    def submit(self, job):
        raise NotImplementedError

    def cancel(self, external_id):
        raise NotImplementedError

    def fetch_status(self, external_id):
        raise NotImplementedError


class EmailPrintProvider:
    """
    Wariant: zlecenie wysyłane e-mailem z linkiem do PDF i specyfikacją w treści.
    Statusy ręcznie w panelu operatora. Do implementacji po ustaleniach z drukarnią.
    """

    name = "EmailPrintProvider"

    def submit(self, job):
        raise NotImplementedError

    def cancel(self, external_id):
        raise NotImplementedError

    def fetch_status(self, external_id):
        raise NotImplementedError


class HttpPrintProvider:
    """
    Wariant: REST API drukarni (POST zlecenie z idempotency_key, webhook statusów z podpisem HMAC).
    Najbliższy temu, co robi MockPrintProvider. Do implementacji po ustaleniach z drukarnią.
    """

    name = "HttpPrintProvider"

    def submit(self, job):
        raise NotImplementedError

    def cancel(self, external_id):
        raise NotImplementedError

    def fetch_status(self, external_id):
        raise NotImplementedError


def get_print_provider() -> PrintProvider:
    return import_string(settings.PRINT_PROVIDER)()
