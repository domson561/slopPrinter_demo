"""Koszyk w sesji: token jednego szkicu zamówienia. Szkic po przejściu do płatności przestaje być koszykiem."""

from apps.orders.models import Order, OrderStatus

SESSION_CART = "cart_token"


def cart_for(request) -> Order | None:
    token = request.session.get(SESSION_CART)
    if not token:
        return None
    return Order.objects.filter(status_token=token, status=OrderStatus.DRAFT).first()


def remember_cart(request, order: Order) -> None:
    request.session[SESSION_CART] = order.status_token
