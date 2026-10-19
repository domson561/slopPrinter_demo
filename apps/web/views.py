"""
Widoki klienta rdzenia: koszyk, S4 dane i dostawa, S6 potwierdzenie, S7 status z reklamacją,
S8 strony statyczne. Cienkie: walidacja formularza -> wywołanie services -> redirect/render.
S1-S3 (wgranie, konfigurator, podgląd pozycji) dostarcza produkt, np. `apps.poster.views`.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.assets.gate import UploadError
from apps.catalog.models import ShopSettings
from apps.catalog.services import active_deliveries
from apps.orders import services as order_services
from apps.orders.models import Order, OrderStatus
from apps.orders.services import TransitionError, customer_label

from .forms import CheckoutForm, ComplaintForm

PRE_PAYMENT = (OrderStatus.DRAFT, OrderStatus.AWAITING_PAYMENT)


def order_by_token(token, statuses=None):
    order = get_object_or_404(Order.objects.prefetch_related("items"), status_token=token)
    if statuses and order.status not in statuses:
        raise Http404
    return order


# ---------------------------------------------------------------------------
# Koszyk
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def cart(request, token):
    order = order_by_token(token, PRE_PAYMENT)
    if request.method == "POST":
        if not order.is_draft:
            raise Http404
        item = get_object_or_404(order.items, pk=request.POST.get("item"))
        order_services.remove_item(order, item)
        messages.success(request, "Usunęliśmy pozycję z koszyka.")
        return redirect("web:cart", token=token)
    return render(
        request,
        "web/cart.html",
        {"order": order, "items": order.items.select_related("asset").order_by("pk")},
    )


# ---------------------------------------------------------------------------
# S4 - dane, S6 - potwierdzenie, S7 - status
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def checkout(request, token):
    order = order_by_token(token, PRE_PAYMENT)
    if not order.items.exists():
        messages.error(request, "Koszyk jest pusty.")
        return redirect("web:cart", token=token)
    a = order.shipping_address or {}
    initial = {
        "email": order.email,
        "full_name": order.full_name,
        "phone": order.phone,
        "line1": a.get("line1", ""),
        "line2": a.get("line2", ""),
        "postal_code": a.get("postal_code", ""),
        "city": a.get("city", ""),
        "delivery_method": order.delivery_method_id or active_deliveries().first(),
    }
    form = CheckoutForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        payment_url = order_services.start_checkout(
            order,
            email=d["email"],
            full_name=d["full_name"],
            phone=d["phone"],
            address=form.address(),
            delivery_method=d["delivery_method"],
            consents=form.consents(),
        )
        return redirect(payment_url)
    return render(
        request,
        "web/s4_checkout.html",
        {"order": order, "form": form, "deliveries": active_deliveries()},
    )


@require_GET
def order_confirmation(request, token):
    order = order_by_token(token)
    if order.status in PRE_PAYMENT:
        return redirect("web:checkout", token=token)
    shop = ShopSettings.get()
    return render(
        request,
        "web/s6_confirmation.html",
        {"order": order, "lead_time_days": shop.lead_time_days},
    )


@require_http_methods(["GET", "POST"])
def order_status(request, token):
    order = order_by_token(token)
    if order.status == OrderStatus.DRAFT:
        return redirect("web:cart", token=token)
    form = (
        ComplaintForm(request.POST or None, request.FILES or None) if order.can_complain else None
    )
    if request.method == "POST" and form is not None and form.is_valid():
        try:
            order_services.file_complaint(
                order, form.cleaned_data["description"], form.cleaned_data["photos"]
            )
        except (TransitionError, UploadError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Dziękujemy, przyjęliśmy reklamację. Odpowiemy e-mailem.")
        return redirect("web:order_status", token=token)

    title, description = customer_label(order.status)
    history = [
        {"event": e, "label": customer_label(e.to_status)[0]}
        for e in order.status_events.all()
        if e.to_status != OrderStatus.DRAFT and e.from_status != e.to_status
    ]
    return render(
        request,
        "web/s7_status.html",
        {
            "order": order,
            "items": order.items.select_related("asset").order_by("pk"),
            "title": title,
            "description": description,
            "history": history,
            "shipment": order.latest_shipment,
            "form": form,
            "complaints": order.complaints.all(),
        },
    )


# ---------------------------------------------------------------------------
# S8 - strony statyczne
# ---------------------------------------------------------------------------


def static_page(request, slug):
    pages = {
        "regulamin": ("Regulamin", "web/s8_terms.html"),
        "prywatnosc": ("Polityka prywatności", "web/s8_privacy.html"),
        "faq": ("Najczęstsze pytania", "web/s8_faq.html"),
        "o-nas": ("O nas", "web/s8_about.html"),
    }
    if slug not in pages:
        raise Http404
    title, template = pages[slug]
    return render(request, template, {"title": title})
