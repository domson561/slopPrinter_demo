"""Widoki klienta S1-S8. Cienkie: walidacja formularza -> wywołanie services -> redirect/render."""

import io
import json

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.assets import quality
from apps.assets.models import Asset
from apps.assets.services import UploadError, create_asset_from_upload, quality_for_selection, render_fragment, render_mockup
from apps.catalog.models import Format, ShopSettings
from apps.catalog.services import active_deliveries, active_formats, active_papers, quote
from apps.orders import services as order_services
from apps.orders.models import Order, OrderStatus
from apps.orders.services import TransitionError, customer_label

from .forms import MAX_QUANTITY, CheckoutForm, ComplaintForm, ConfigureForm, UploadForm

SESSION_DRAFTS = "drafts"  # {asset_id: status_token}


# ---------------------------------------------------------------------------
# S1 - upload
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            asset = create_asset_from_upload(form.cleaned_data["file"])
        except UploadError as exc:
            form.add_error("file", str(exc))
        else:
            return redirect("web:configure", asset_id=asset.pk)
    return render(
        request,
        "web/s1_upload.html",
        {"form": form, "max_mb": 30, "formats": active_formats()},
    )


# ---------------------------------------------------------------------------
# S2 - konfiguracja
# ---------------------------------------------------------------------------


def _draft_for(request, asset):
    token = (request.session.get(SESSION_DRAFTS) or {}).get(str(asset.pk))
    if not token:
        return None
    return Order.objects.filter(status_token=token, status=OrderStatus.DRAFT).first()


def _remember_draft(request, asset, order):
    drafts = dict(request.session.get(SESSION_DRAFTS) or {})
    drafts[str(asset.pk)] = order.status_token
    request.session[SESSION_DRAFTS] = drafts


def _format_cards(asset, shop):
    cards = []
    analysis = getattr(asset, "analysis", None)
    for fmt in active_formats():
        q = analysis.quality(fmt.code) if analysis else None
        if q is None:
            q = quality.quality_for(asset.width, asset.height, fmt, shop).as_dict()
        cards.append({"format": fmt, "quality": q, "price": quote(fmt, None, 1).unit_price, "default_crop": quality.default_crop(asset.width, asset.height, fmt)})
    return cards


@require_http_methods(["GET", "POST"])
def configure(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id)
    if asset.is_expired:
        raise Http404("Plik wygasł.")
    shop = ShopSettings.get()
    draft = _draft_for(request, asset)
    item = draft.item if draft else None
    cards = _format_cards(asset, shop)

    initial = {
        "format": item.format_id if item else _default_format(cards),
        "paper": item.paper_id if item else active_papers().first(),
        "white_margin": item.white_margin if item else False,
        "quantity": item.quantity if item else 1,
        "enhancer_enabled": item.enhancer_enabled if item else False,
        "crop": json.dumps(item.crop) if item else "",
    }
    form = ConfigureForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            order = order_services.save_draft(
                asset,
                d["format"],
                d["paper"],
                white_margin=d["white_margin"],
                quantity=d["quantity"],
                crop=d["crop"],
                enhancer_enabled=d["enhancer_enabled"],
                order=draft,
            )
        except TransitionError as exc:
            form.add_error("format", str(exc))
        else:
            _remember_draft(request, asset, order)
            return redirect("web:preview", token=order.status_token)

    return render(
        request,
        "web/s2_configure.html",
        {
            "asset": asset,
            "form": form,
            "cards": cards,
            "cards_json": json.dumps({c["format"].code: {"id": c["format"].pk, "width_mm": c["format"].width_mm, "height_mm": c["format"].height_mm, "quality": c["quality"], "default_crop": c["default_crop"]} for c in cards}),
            "papers": active_papers(),
            "shop": shop,
            "max_quantity": MAX_QUANTITY,
            "flags": getattr(getattr(asset, "analysis", None), "content_flags", []),
        },
    )


def _default_format(cards):
    for c in cards:
        if not c["quality"]["blocked"]:
            return c["format"].pk
    return cards[0]["format"].pk if cards else None


@require_GET
def price_partial(request, asset_id):
    """Cena i ocena jakości dla bieżącego wyboru (wołane z JS przy każdej zmianie)."""
    asset = get_object_or_404(Asset, pk=asset_id)
    fmt = get_object_or_404(Format, pk=request.GET.get("format"), is_active=True)
    paper = active_papers().filter(pk=request.GET.get("paper")).first()
    try:
        qty = max(1, min(int(request.GET.get("quantity", 1)), MAX_QUANTITY))
    except ValueError:
        qty = 1
    white_margin = request.GET.get("white_margin") in ("1", "true", "on")
    try:
        crop = json.loads(request.GET.get("crop") or "{}")
    except json.JSONDecodeError:
        crop = {}
    q = quality_for_selection(asset, fmt, crop if crop else None)
    price = quote(fmt, paper, qty, white_margin)
    suggestion = None
    if q.blocked:
        smaller = quality.suggest_smaller_format(fmt, active_formats(), asset.width, asset.height, ShopSettings.get())
        suggestion = {"id": smaller.pk, "code": smaller.code, "name": smaller.name} if smaller else None
    return JsonResponse(
        {
            "quality": q.as_dict(),
            "suggestion": suggestion,
            "unit_price": str(price.unit_price),
            "subtotal": str(price.subtotal),
            "currency": ShopSettings.get().currency,
        }
    )


# ---------------------------------------------------------------------------
# Obrazy: podgląd, mockup, wycinek
# ---------------------------------------------------------------------------


def _png_response(image):
    buf = io.BytesIO()
    image.save(buf, "PNG")
    resp = HttpResponse(buf.getvalue(), content_type="image/png")
    resp["Cache-Control"] = "no-store"
    return resp


@require_GET
def asset_preview(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id)
    version = asset.preview
    if version is None:
        raise Http404
    return FileResponse(version.file.open("rb"), content_type="image/jpeg")


def _order_by_token(token, statuses=None):
    order = get_object_or_404(Order.objects.prefetch_related("items"), status_token=token)
    if statuses and order.status not in statuses:
        raise Http404
    return order


@require_GET
def mockup_image(request, token):
    order = _order_by_token(token)
    item = order.item
    if item is None:
        raise Http404
    return _png_response(render_mockup(item.asset, item.format, item.crop, item.white_margin))


@require_GET
def fragment_image(request, token, mode):
    order = _order_by_token(token)
    item = order.item
    if item is None or mode not in ("before", "after"):
        raise Http404
    return _png_response(render_fragment(item.asset, item.crop, enhanced=(mode == "after")))


# ---------------------------------------------------------------------------
# S3 - podgląd, S4 - dane, S6 - potwierdzenie, S7 - status
# ---------------------------------------------------------------------------

_PRE_PAYMENT = (OrderStatus.DRAFT, OrderStatus.AWAITING_PAYMENT)


@require_GET
def preview(request, token):
    order = _order_by_token(token, _PRE_PAYMENT)
    item = order.item
    q = quality_for_selection(item.asset, item.format, item.crop)
    return render(request, "web/s3_preview.html", {"order": order, "item": item, "quality": q})


@require_http_methods(["GET", "POST"])
def checkout(request, token):
    order = _order_by_token(token, _PRE_PAYMENT)
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
    return render(request, "web/s4_checkout.html", {"order": order, "item": order.item, "form": form, "deliveries": active_deliveries()})


@require_GET
def order_confirmation(request, token):
    order = _order_by_token(token)
    if order.status == OrderStatus.DRAFT:
        return redirect("web:checkout", token=token)
    if order.status == OrderStatus.AWAITING_PAYMENT:
        return redirect("web:checkout", token=token)
    shop = ShopSettings.get()
    return render(request, "web/s6_confirmation.html", {"order": order, "item": order.item, "lead_time_days": shop.lead_time_days})


@require_http_methods(["GET", "POST"])
def order_status(request, token):
    order = _order_by_token(token)
    if order.status == OrderStatus.DRAFT:
        return redirect("web:preview", token=token)
    form = ComplaintForm(request.POST or None, request.FILES or None) if order.can_complain else None
    if request.method == "POST" and form is not None and form.is_valid():
        try:
            order_services.file_complaint(order, form.cleaned_data["description"], form.cleaned_data["photos"])
        except TransitionError as exc:
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
            "item": order.item,
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
    }
    if slug not in pages:
        raise Http404
    title, template = pages[slug]
    return render(request, template, {"title": title})
