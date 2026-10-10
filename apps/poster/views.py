"""
Widoki plakatu: S1 wgranie, S2 konfigurator z ceną na żywo, S3 podgląd, obrazy (podgląd, makieta,
wycinek). Reszta ścieżki (checkout, płatność, status) jest w rdzeniu (`apps.web`).
"""

import io
import json

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.assets.gate import UploadError
from apps.assets.models import Asset
from apps.catalog.models import ShopSettings
from apps.orders.models import Order, OrderStatus
from apps.orders.services import TransitionError
from apps.products import ProductError
from apps.web.views import PRE_PAYMENT, order_by_token

from . import quality
from .forms import MAX_QUANTITY, ConfigureForm, UploadForm
from .models import Format, PosterSettings
from .options import format_of, paper_of
from .pipeline import quality_for_selection, render_fragment, render_mockup
from .pricing import active_formats, active_papers, quote
from .services import save_poster_draft, upload_poster_asset

SESSION_DRAFTS = "drafts"  # {asset_id: status_token}


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


def _draft_for(request, asset):
    token = (request.session.get(SESSION_DRAFTS) or {}).get(str(asset.pk))
    if not token:
        return None
    return Order.objects.filter(status_token=token, status=OrderStatus.DRAFT).first()


def _remember_draft(request, asset, order):
    drafts = dict(request.session.get(SESSION_DRAFTS) or {})
    drafts[str(asset.pk)] = order.status_token
    request.session[SESSION_DRAFTS] = drafts


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
    draft = _draft_for(request, asset)
    item = draft.item if draft else None
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
            order = save_poster_draft(
                asset,
                d["format"],
                d["paper"],
                white_margin=d["white_margin"],
                quantity=d["quantity"],
                crop=d["crop"],
                enhancer_enabled=d["enhancer_enabled"],
                order=draft,
            )
        except (TransitionError, ProductError) as exc:
            form.add_error("format", str(exc))
        else:
            _remember_draft(request, asset, order)
            return redirect("web:preview", token=order.status_token)

    return render(
        request,
        "poster/s2_configure.html",
        {
            "asset": asset,
            "form": form,
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


@require_GET
def mockup_image(request, token):
    order = order_by_token(token)
    item = order.item
    if item is None:
        raise Http404
    opts = item.options
    return _png_response(
        render_mockup(item.asset, format_of(opts), opts.get("crop") or {}, opts.get("white_margin"))
    )


@require_GET
def fragment_image(request, token, mode):
    order = order_by_token(token)
    item = order.item
    if item is None or mode not in ("before", "after"):
        raise Http404
    crop = item.options.get("crop") or {}
    return _png_response(render_fragment(item.asset, crop, enhanced=(mode == "after")))


# ---------------------------------------------------------------------------
# S3 - podgląd
# ---------------------------------------------------------------------------


@require_GET
def preview(request, token):
    order = order_by_token(token, PRE_PAYMENT)
    item = order.item
    fmt = format_of(item.options)
    q = quality_for_selection(item.asset, fmt, item.options.get("crop"))
    return render(
        request,
        "poster/s3_preview.html",
        {"order": order, "item": item, "fmt": fmt, "quality": q},
    )
