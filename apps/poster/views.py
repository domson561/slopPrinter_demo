"""
Widoki plakatu: S1 wgranie, S2 konfigurator z ceną na żywo, S3 podgląd pozycji, obrazy (podgląd,
makieta, wycinek). Koszyk, checkout, płatność i status są w rdzeniu (`apps.web`).
"""

import io
import json

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.assets.gate import UploadError
from apps.assets.models import Asset
from apps.catalog.models import ShopSettings
from apps.orders.services import TransitionError
from apps.products import ProductError
from apps.web.cart import cart_for, remember_cart
from apps.web.views import PRE_PAYMENT, order_by_token

from . import quality
from .forms import MAX_QUANTITY, ConfigureForm, UploadForm
from .models import Format, PosterSettings
from .options import format_of, paper_of
from .pipeline import quality_for_selection, render_fragment, render_mockup
from .pricing import active_formats, active_papers, quote
from .services import save_poster_draft, upload_poster_asset

# ---------------------------------------------------------------------------
# S1 - upload
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            asset = upload_poster_asset(form.cleaned_data["file"])
        except UploadError as exc:
            form.add_error("file", str(exc))
        else:
            return redirect("web:configure", asset_id=asset.pk)
    return render(
        request,
        "poster/s1_upload.html",
        {"form": form, "max_mb": 30, "formats": active_formats()},
    )


# ---------------------------------------------------------------------------
# S2 - konfiguracja
# ---------------------------------------------------------------------------


def _cart_item(request, asset):
    """Koszyk z sesji i edytowana pozycja (`?item=` / pole `item`), jeśli należy do niego."""
    cart = cart_for(request)
    item_id = request.POST.get("item") or request.GET.get("item") or ""
    item = None
    if cart is not None and item_id.isdigit():
        item = cart.items.filter(pk=int(item_id), asset=asset).first()
    return cart, item


def _format_cards(asset, poster_settings):
    cards = []
    analysis = getattr(asset, "analysis", None)
    for fmt in active_formats():
        q = analysis.quality(fmt.code) if analysis else None
        if q is None:
            q = quality.quality_for(asset.width, asset.height, fmt, poster_settings).as_dict()
        cards.append(
            {
                "format": fmt,
                "quality": q,
                "price": quote(fmt, None, 1).unit_price,
                "default_crop": quality.default_crop(asset.width, asset.height, fmt),
            }
        )
    return cards


@require_http_methods(["GET", "POST"])
def configure(request, asset_id):
    asset = get_object_or_404(Asset, pk=asset_id)
    if asset.is_expired:
        raise Http404("Plik wygasł.")
    poster_settings = PosterSettings.get()
    cart, item = _cart_item(request, asset)
    opts = item.options if item else {}
    cards = _format_cards(asset, poster_settings)

    initial = {
        "format": format_of(opts).pk if item else _default_format(cards),
        "paper": paper_of(opts) if item else active_papers().first(),
        "white_margin": opts.get("white_margin", False),
        "quantity": item.quantity if item else 1,
        "enhancer_enabled": opts.get("enhancer", False),
        "crop": json.dumps(opts.get("crop") or {}) if item else "",
    }
    form = ConfigureForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            item = save_poster_draft(
                asset,
                d["format"],
                d["paper"],
                white_margin=d["white_margin"],
                quantity=d["quantity"],
                crop=d["crop"],
                enhancer_enabled=d["enhancer_enabled"],
                order=cart,
                item=item,
            )
        except (TransitionError, ProductError) as exc:
            form.add_error("format", str(exc))
        else:
            remember_cart(request, item.order)
            return redirect("web:preview", token=item.order.status_token, item_id=item.pk)

    return render(
        request,
        "poster/s2_configure.html",
        {
            "asset": asset,
            "form": form,
            "item": item,
            "cards": cards,
            "cards_json": json.dumps(
                {
                    c["format"].code: {
                        "id": c["format"].pk,
                        "width_mm": c["format"].width_mm,
                        "height_mm": c["format"].height_mm,
                        "quality": c["quality"],
                        "default_crop": c["default_crop"],
                    }
                    for c in cards
                }
            ),
            "papers": active_papers(),
            "poster_settings": poster_settings,
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
        smaller = quality.suggest_smaller_format(
            fmt, active_formats(), asset.width, asset.height, PosterSettings.get()
        )
        suggestion = (
            {"id": smaller.pk, "code": smaller.code, "name": smaller.name} if smaller else None
        )
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


def _item(token, item_id, statuses=None):
    order = order_by_token(token, statuses)
    return order, get_object_or_404(order.items.select_related("asset"), pk=item_id)


@require_GET
def mockup_image(request, token, item_id):
    _, item = _item(token, item_id)
    opts = item.options
    return _png_response(
        render_mockup(item.asset, format_of(opts), opts.get("crop") or {}, opts.get("white_margin"))
    )


@require_GET
def fragment_image(request, token, item_id, mode):
    if mode not in ("before", "after"):
        raise Http404
    _, item = _item(token, item_id)
    crop = item.options.get("crop") or {}
    return _png_response(render_fragment(item.asset, crop, enhanced=(mode == "after")))


# ---------------------------------------------------------------------------
# S3 - podgląd pozycji
# ---------------------------------------------------------------------------


@require_GET
def preview(request, token, item_id):
    order, item = _item(token, item_id, PRE_PAYMENT)
    fmt = format_of(item.options)
    q = quality_for_selection(item.asset, fmt, item.options.get("crop"))
    return render(
        request,
        "poster/s3_preview.html",
        {"order": order, "item": item, "fmt": fmt, "quality": q},
    )
