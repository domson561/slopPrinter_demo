"""Pulpit „biura GH” (/gh-mock/): lista zleceń ze sklepów, pliki, zmiany statusów, auto-symulacja."""

import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import GhMockSettings, GhOrder, GhOrderFile
from .services import (
    NotAllowed,
    advance_all,
    download_file,
    download_pending_files,
    expire_file,
    set_status,
)

STATUS_LABELS = {
    GhOrder.Status.IN_PROGRESS: "W realizacji",
    GhOrder.Status.READY: "Gotowe",
    GhOrder.Status.SHIPPED: "Wysłane",
}


def dashboard(request):
    orders = GhOrder.objects.select_related("shop").prefetch_related("items", "files")
    return render(
        request,
        "gh_mock/dashboard.html",
        {"orders": orders, "sim": GhMockSettings.get(), "status_labels": STATUS_LABELS},
    )


def order_detail(request, pk):
    order = get_object_or_404(GhOrder.objects.select_related("shop"), pk=pk)
    return render(
        request,
        "gh_mock/order.html",
        {
            "order": order,
            "status_labels": STATUS_LABELS,
            "state_json": json.dumps(order.to_state(), indent=2, ensure_ascii=False),
        },
    )


@require_POST
def order_action(request, pk):
    order = get_object_or_404(GhOrder, pk=pk)
    action = request.POST.get("action", "")
    try:
        if action == "download":
            pending = list(order.files.filter(state=GhOrderFile.State.PENDING))
            for file in pending:
                download_file(file)
            messages.info(request, f"Prób pobrania: {len(pending)}.")
        elif action == "expire":
            file = get_object_or_404(order.files, pk=request.POST.get("file_id"))
            if file.state == GhOrderFile.State.DOWNLOADED:
                raise NotAllowed("plik już pobrany")
            expire_file(file)
            messages.warning(
                request, "Link oznaczony jako wygasły. Sklep dośle świeży przy pollingu."
            )
        elif action in STATUS_LABELS:
            set_status(order, action)
            messages.success(request, f"Status: {order.get_status_display()}.")
        else:
            raise NotAllowed("nieznana akcja")
    except NotAllowed as exc:
        messages.error(request, f"Nie można: {exc}.")
    return redirect("gh_mock:order", pk=pk)


@require_POST
def download_all(request):
    count = download_pending_files()
    messages.info(request, f"Cron GH: prób pobrania {count}.")
    return redirect("gh_mock:dashboard")


@require_POST
def toggle_auto(request):
    sim = GhMockSettings.get()
    sim.auto_simulate = not sim.auto_simulate
    try:
        interval = int(request.POST.get("interval", sim.auto_interval_seconds))
        sim.auto_interval_seconds = max(2, min(interval, 600))
    except ValueError:
        pass
    sim.save()
    messages.info(request, "Auto-symulacja " + ("włączona." if sim.auto_simulate else "wyłączona."))
    return redirect("gh_mock:dashboard")


@require_POST
def tick(request):
    """Jeden krok auto-symulacji: pobrania plus każde zlecenie o jeden status."""
    advanced = advance_all()
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"advanced": advanced, "auto": GhMockSettings.get().auto_simulate})
    messages.info(request, f"Posunięto: {advanced}")
    return redirect("gh_mock:dashboard")
