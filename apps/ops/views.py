"""Panel operatora O1-O5. Tylko is_staff. Każda akcja przechodzi przez orders.services."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.catalog.models import ShopSettings
from apps.orders import services as order_services
from apps.orders.models import Actor, Complaint, ComplaintPhoto, Order, OrderItem, OrderStatus
from apps.orders.services import TransitionError
from apps.printing.models import PollCursor, PrintJob
from apps.printing.services import poll_changes, reset_poll_auth
from apps.products import all_products

from .forms import (
    ForceStatusForm,
    OrderFilterForm,
    ReasonForm,
    ResolveComplaintForm,
    ShopSettingsForm,
)

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="/ops/login/")


class OpsLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


class OpsLogoutView(LogoutView):
    next_page = "/ops/login/"


def _attention_qs():
    return Order.objects.filter(
        Q(status__in=[OrderStatus.AWAITING_APPROVAL, OrderStatus.ON_HOLD])
        | Q(complaints__status=Complaint.Status.OPEN)
    ).distinct()


@staff_required
def order_list(request):
    form = OrderFilterForm(request.GET or None)
    qs = (
        Order.objects.exclude(status=OrderStatus.DRAFT)
        .select_related("delivery_method")
        .prefetch_related("items")
    )
    if form.is_valid():
        d = form.cleaned_data
        if d["status"]:
            qs = qs.filter(status=d["status"])
        if d["q"]:
            qs = qs.filter(Q(number__icontains=d["q"]) | Q(email__icontains=d["q"]))
        if d["attention"]:
            qs = qs.filter(pk__in=_attention_qs().values("pk"))
    counts = {
        row["status"]: row["n"] for row in Order.objects.values("status").annotate(n=Count("id"))
    }
    return render(
        request,
        "ops/o2_orders.html",
        {
            "orders": qs[:200],
            "form": form,
            "attention_count": _attention_qs().count(),
            "counts": counts,
            "statuses": OrderStatus.choices,
        },
    )


@staff_required
def queue(request):
    orders = Order.objects.filter(
        status__in=[OrderStatus.AWAITING_APPROVAL, OrderStatus.ON_HOLD]
    ).order_by("created_at")
    open_complaints = Complaint.objects.filter(status=Complaint.Status.OPEN).select_related("order")
    return render(
        request,
        "ops/o4_queue.html",
        {"orders": orders, "open_complaints": open_complaints, "reason_form": ReasonForm()},
    )


@staff_required
def order_detail(request, number):
    order = get_object_or_404(
        Order.objects.prefetch_related(
            "items", "status_events", "payments", "shipments", "complaints"
        ),
        number=number,
    )
    items = list(order.items.select_related("asset").order_by("pk"))
    jobs = (
        PrintJob.objects.filter(order=order).select_related("reprint_of").prefetch_related("events")
    )
    poll_cursor = PollCursor.get()
    context = {
        "order": order,
        "items_ctx": [(item, item.definition.operator_context(item)) for item in items],
        "poll_cursor": poll_cursor,
        "jobs": jobs,
        "events": order.status_events.select_related("actor_user"),
        "payments": order.payments.all(),
        "shipments": order.shipments.all(),
        "complaints": order.complaints.prefetch_related("photos"),
        "reason_form": ReasonForm(),
        "force_form": ForceStatusForm(),
        "resolve_form": ResolveComplaintForm(),
        "can_approve": order.status in (OrderStatus.AWAITING_APPROVAL, OrderStatus.ON_HOLD),
        "can_reject": order.status in (OrderStatus.AWAITING_APPROVAL, OrderStatus.ON_HOLD),
        "can_reprocess": order.status in (OrderStatus.AWAITING_APPROVAL, OrderStatus.ON_HOLD),
        "can_reprint": order.status
        in (OrderStatus.PRINTED, OrderStatus.SHIPPED, OrderStatus.DELIVERED),
        "can_cancel": order.status
        not in (OrderStatus.CANCELLED, OrderStatus.DELIVERED, OrderStatus.DRAFT),
        "can_deliver": order.status == OrderStatus.SHIPPED,
        "S": OrderStatus,
    }
    return render(request, "ops/o3_order.html", context)


@staff_required
@require_POST
def order_action(request, number):
    order = get_object_or_404(Order, number=number)
    action = request.POST.get("action", "")
    reason = request.POST.get("reason", "").strip()
    user = request.user
    try:
        if action == "approve":
            order_services.approve_order(order, Actor.OPERATOR, user, reason)
            messages.success(request, "Zatwierdzono i wysłano do drukarni.")
        elif action == "reject":
            _require(reason)
            order_services.reject_order(order, user, reason)
            messages.success(request, "Odrzucono, zwrot zlecony (symulacja).")
        elif action == "reprocess":
            order_services.reprocess_order(order, user, reason or "ponowne przetworzenie")
            messages.success(request, "Uruchomiono ponowne przetworzenie.")
        elif action == "force":
            form = ForceStatusForm(request.POST)
            if not form.is_valid():
                raise TransitionError("Wybierz stan i podaj powód.")
            order_services.force_transition(
                order, form.cleaned_data["to_status"], user, form.cleaned_data["reason"]
            )
            messages.warning(request, "Stan zmieniony ręcznie (zalogowane jako wymuszenie).")
        elif action == "reprint":
            _require(reason)
            order_services.request_reprint(order, Actor.OPERATOR, reason, user)
            messages.success(request, "Zlecono dodruk.")
        elif action == "cancel":
            _require(reason)
            order_services.cancel_order(order, Actor.OPERATOR, reason, user)
            messages.success(request, "Anulowano ze zwrotem (symulacja).")
        elif action == "deliver":
            order_services.mark_delivered(
                order, Actor.OPERATOR, user, reason or "oznaczone ręcznie jako doręczone"
            )
            messages.success(request, "Oznaczono jako doręczone.")
        elif action == "resolve_complaint":
            form = ResolveComplaintForm(request.POST)
            if not form.is_valid():
                raise TransitionError("Podaj decyzję i uzasadnienie.")
            complaint = get_object_or_404(
                Complaint, pk=form.cleaned_data["complaint_id"], order=order
            )
            order_services.resolve_complaint(
                complaint, user, form.cleaned_data["decision"], form.cleaned_data["resolution"]
            )
            messages.success(request, "Reklamacja rozpatrzona.")
        else:
            raise TransitionError("Nieznana akcja.")
    except TransitionError as exc:
        messages.error(request, str(exc))
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/ops/"):
        return redirect(next_url)
    return redirect("ops:order_detail", number=order.number)


@staff_required
@require_POST
def poll_gh(request):
    """Ręczny cykl pollingu GH (bez workera) albo wznowienie po wymianie tokenu."""
    if request.POST.get("action") == "reset_auth":
        reset_poll_auth()
        messages.info(request, "Polling GH wznowiony.")
    summary = poll_changes()
    if summary["error"]:
        messages.error(request, f"GH: {summary['error']}")
    else:
        messages.success(
            request,
            f"GH odpytane: zleceń {summary['orders']}, zmian {summary['changed']}, "
            f"dosłanych linków {summary['resent']}.",
        )
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/ops/"):
        return redirect(next_url)
    return redirect("ops:order_list")


def _require(reason):
    if not reason:
        raise TransitionError("Ta akcja wymaga podania powodu.")


@staff_required
def complaint_photo(request, number, pk):
    """Zdjęcie do reklamacji z prywatnego katalogu; tylko dla operatora."""
    photo = get_object_or_404(ComplaintPhoto, pk=pk, complaint__order__number=number)
    content_type = mimetypes.guess_type(photo.image.name)[0] or "application/octet-stream"
    resp = FileResponse(photo.image.open("rb"), content_type=content_type)
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@staff_required
def order_print_file(request, number, pk):
    order = get_object_or_404(Order, number=number)
    item = get_object_or_404(OrderItem, pk=pk, order=order)
    files = item.print_files
    if not files:
        raise Http404("Brak PDF do druku.")
    return FileResponse(
        files[0].file.open("rb"),
        as_attachment=True,
        filename=f"{order.number}_{item.pk}_{item.definition.gh_line(item).product_code}.pdf",
    )


@staff_required
def settings_view(request):
    """Ustawienia rdzenia plus sekcje ustawień każdego produktu (np. cennik)."""
    shop = ShopSettings.get()
    data = request.POST if request.method == "POST" else None
    form = ShopSettingsForm(data, instance=shop)
    sections = [
        (product, product.settings_forms(data))
        for product in all_products()
        if product.settings_template
    ]
    product_forms = [f for _, forms in sections for f in forms.values()]
    if data is not None and form.is_valid() and all(f.is_valid() for f in product_forms):
        form.save()
        for f in product_forms:
            f.save()
        messages.success(request, "Zapisano ustawienia.")
        return redirect("ops:settings")
    return render(
        request,
        "ops/o5_settings.html",
        {"form": form, "sections": sections, "shop": shop},
    )
