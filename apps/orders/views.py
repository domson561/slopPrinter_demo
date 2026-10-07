"""Ekran S5 (symulowana bramka) i callback płatności."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Order, OrderStatus, Payment
from .payments import get_payment_provider


def mock_payment(request, external_id):
    payment = get_object_or_404(Payment.objects.select_related("order"), external_id=external_id)
    order = payment.order
    if order.status not in (OrderStatus.AWAITING_PAYMENT,):
        return redirect("web:order_confirmation", token=order.status_token)
    return render(request, "web/s5_payment.html", {"payment": payment, "order": order})


@require_POST
def payment_callback(request):
    provider = get_payment_provider()
    result = provider.handle_callback(request)
    order = result.payment.order
    if result.success:
        return redirect("web:order_confirmation", token=order.status_token)
    messages.error(request, result.message)
    return redirect("web:checkout", token=order.status_token)


def retry_payment(request, token):
    """Po nieudanej płatności: nowa próba na tym samym zamówieniu."""
    order = get_object_or_404(Order, status_token=token)
    if order.status != OrderStatus.AWAITING_PAYMENT:
        return redirect("web:order_status", token=token)
    return redirect(get_payment_provider().create(order))
