"""Panel operatora O1-O5. Tylko is_staff. Każda akcja przechodzi przez orders.services."""

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.assets.services import FLAG_LABELS
from apps.catalog.models import ShopSettings
from apps.orders import services as order_services
from apps.orders.models import Actor, Complaint, Order, OrderStatus
from apps.orders.services import TransitionError
from apps.printing.models import PrintJob

from .forms import FormatFormSet, ForceStatusForm, OrderFilterForm, PaperFormSet, ReasonForm, ResolveComplaintForm, ShopSettingsForm

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="/ops/login/")


class OpsLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


class OpsLogoutView(LogoutView):
    next_page = "/ops/login/"


def _attention_qs():
    return Order.objects.filter(
        Q(status__in=[OrderStatus.AWAITING_APPROVAL, OrderStatus.ON_HOLD]) | Q(complaints__status=Complaint.Status.OPEN)
    ).distinct()


@staff_required
def order_list(request):
    form = OrderFilterForm(request.GET or None)
    qs = Order.objects.exclude(status=OrderStatus.DRAFT).select_related("delivery_method").prefetch_related("items__format")
    if form.is_valid():
        d = form.cleaned_data
        if d["status"]:
            qs = qs.filter(status=d["status"])
        if d["q"]:
            qs = qs.filter(Q(number__icontains=d["q"]) | Q(email__icontains=d["q"]))
        if d["attention"]:
            qs = qs.filter(pk__in=_attention_qs().values("pk"))
    counts = {row["status"]: row["n"] for row in Order.objects.values("status").annotate(n=Count("id"))}
    return render(
        request,
        "ops/o2_orders.html",
        {"orders": qs[:200], "form": form, "attention_count": _attention_qs().count(), "counts": counts, "statuses": OrderStatus.choices},
    )


@staff_required
def queue(request):
    orders = Order.objects.filter(status__in=[OrderStatus.AWAITING_APPROVAL, OrderStatus.ON_HOLD]).order_by("created_at")
    open_complaints = Complaint.objects.filter(status=Complaint.Status.OPEN).select_related("order")
    return render(request, "ops/o4_queue.html", {"orders": orders, "open_complaints": open_complaints, "reason_form": ReasonForm()})


@staff_required
def order_detail(request, number):
    order = get_object_or_404(Order.objects.prefetch_related("items", "status_events", "payments", "shipments", "complaints"), number=number)
    item = order.item
    jobs = PrintJob.objects.filter(order_item__order=order).select_related("reprint_of").prefetch_related("events")
    analysis = getattr(item.asset, "analysis", None) if item else None
    context = {
        "order": order,
        "item": item,
        "analysis": analysis,
        "flag_labels": [FLAG_LABELS.get(f, f) for f in (analysis.content_flags if analysis else [])],
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
        "can_reprint": order.status in (OrderStatus.PRINTED, OrderStatus.SHIPPED, OrderStatus.DELIVERED),
        "can_cancel": order.status not in (OrderStatus.CANCELLED, OrderStatus.DELIVERED, OrderStatus.DRAFT),
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
            order_services.force_transition(order, form.cleaned_data["to_status"], user, form.cleaned_data["reason"])
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
            order_services.mark_delivered(order, Actor.OPERATOR, user, reason or "oznaczone ręcznie jako doręczone")
            messages.success(request, "Oznaczono jako doręczone.")
        elif action == "resolve_complaint":
            form = ResolveComplaintForm(request.POST)
            if not form.is_valid():
                raise TransitionError("Podaj decyzję i uzasadnienie.")
            complaint = get_object_or_404(Complaint, pk=form.cleaned_data["complaint_id"], order=order)
            order_services.resolve_complaint(complaint, user, form.cleaned_data["decision"], form.cleaned_data["resolution"])
            messages.success(request, "Reklamacja rozpatrzona.")
        else:
            raise TransitionError("Nieznana akcja.")
    except TransitionError as exc:
        messages.error(request, str(exc))
    next_url = request.POST.get("next") or ""
    if next_url.startswith("/ops/"):
        return redirect(next_url)
    return redirect("ops:order_detail", number=order.number)


def _require(reason):
    if not reason:
        raise TransitionError("Ta akcja wymaga podania powodu.")


@staff_required
def order_print_file(request, number):
    order = get_object_or_404(Order, number=number)
    item = order.item
    version = item.print_file if item else None
    if version is None:
        raise Http404("Brak PDF do druku.")
    return FileResponse(version.file.open("rb"), as_attachment=True, filename=f"{order.number}_{item.format.code}.pdf")


@staff_required
def settings_view(request):
    shop = ShopSettings.get()
    if request.method == "POST":
        form = ShopSettingsForm(request.POST, instance=shop)
        formats = FormatFormSet(request.POST, prefix="formats")
        papers = PaperFormSet(request.POST, prefix="papers")
        if form.is_valid() and formats.is_valid() and papers.is_valid():
            form.save()
            formats.save()
            papers.save()
            messages.success(request, "Zapisano ustawienia.")
            return redirect("ops:settings")
    else:
        form = ShopSettingsForm(instance=shop)
        formats = FormatFormSet(prefix="formats")
        papers = PaperFormSet(prefix="papers")
    return render(request, "ops/o5_settings.html", {"form": form, "formats": formats, "papers": papers, "shop": shop})
