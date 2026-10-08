"""
Płatności za wymiennym interfejsem. W demo MockPaymentProvider z ekranem S5.
Docelowo Przelewy24 (BLIK) lub Stripe [PROPOZYCJA]. Callback musi być idempotentny.
"""

import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils.module_loading import import_string

from .models import Order, Payment


@dataclass
class PaymentResult:
    payment: Payment
    success: bool
    message: str = ""
    already_processed: bool = False


class PaymentProvider(Protocol):
    name: str

    def create(self, order: Order) -> str: ...  # zwraca URL przekierowania

    def handle_callback(self, request) -> PaymentResult: ...

    def refund(self, payment: Payment, amount: Decimal) -> None: ...


class MockPaymentProvider:
    name = "MockPaymentProvider"

    def create(self, order: Order) -> str:
        payment = Payment.objects.create(
            order=order,
            provider=self.name,
            external_id=f"mockpay_{secrets.token_hex(8)}",
            amount=order.total,
            currency=order.currency,
        )
        payment.log("created")
        payment.save(update_fields=["events"])
        return reverse("orders:mock_payment", args=[payment.external_id])

    def handle_callback(self, request) -> PaymentResult:
        external_id = request.POST.get("external_id", "")
        result = request.POST.get("result", "")
        return self.process_callback(external_id, result)

    def process_callback(self, external_id: str, result: str) -> PaymentResult:
        """Rdzeń callbacku, testowalny bez requestu. Ten sam external_id dwa razy = brak skutków."""
        from .services import mark_paid

        with transaction.atomic():
            payment = (
                Payment.objects.select_for_update()
                .select_related("order")
                .get(external_id=external_id)
            )
            if payment.status == Payment.Status.SUCCEEDED:
                return PaymentResult(
                    payment, True, "Płatność była już zaksięgowana.", already_processed=True
                )
            if result == "success":
                payment.status = Payment.Status.SUCCEEDED
                payment.log("callback", result="success")
            else:
                payment.status = Payment.Status.FAILED
                payment.log("callback", result="failed")
            payment.save(update_fields=["status", "events", "updated_at"])

        # Poza transakcją: mark_paid uruchamia pipeline (w trybie immediate - synchronicznie).
        if payment.status == Payment.Status.SUCCEEDED:
            mark_paid(payment.order, payment)
            return PaymentResult(payment, True, "Płatność zaksięgowana (symulacja).")
        return PaymentResult(
            payment, False, "Płatność nie powiodła się (symulacja). Możesz spróbować ponownie."
        )

    def refund(self, payment: Payment, amount: Decimal) -> None:
        payment.status = Payment.Status.REFUNDED
        payment.log("refund", amount=str(amount))
        payment.save(update_fields=["status", "events", "updated_at"])


def get_payment_provider() -> PaymentProvider:
    return import_string(settings.PAYMENT_PROVIDER)()
