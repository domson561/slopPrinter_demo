"""
Widoki plakatu: S1 wgranie, S2 konfigurator z ceną na żywo, S3 podgląd pozycji, obrazy (podgląd,
makieta, wycinek). Koszyk, checkout, płatność i status są w rdzeniu (`apps.web`).
"""

import io
import json
import logging
from urllib.parse import quote as url_quote
from urllib.parse import urlencode

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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

# Tła strony głównej do wyboru (palety w tokens.css, klasy .tlo-N). 0 = karuzela zdjęć.
# Kolejność = kolejność w przełączniku, domyślne pierwsze.
HOME_BACKGROUNDS = {
    4: "Kosmiczny wieczór",
    1: "Słoneczna galeria",
    2: "Pastelowa poświata",
    3: "Ciepły salon",
    0: "Karuzela",
}
HOME_BACKGROUND_DEFAULT = 4
# Klikalna rama na tle: obrazy i czworokąt ramy składa `scripts/ramy.py` do manifestu.
HOME_FRAMES_MANIFEST = settings.BASE_DIR / "static" / "img" / "tla" / "ramy" / "manifest.json"
# Końcówka doklejana do każdego promptu z kartki przy ramie: to, co służy drukowi.
PROMPT_SUFFIX = (
    "Pionowy format 2:3, bez napisów i znaków wodnych, pełny kadr bez marginesów, "
    "jak największa rozdzielczość."
)
log = logging.getLogger("slop.poster")


def _home_background(request):
    """Przy HOME_BACKGROUND_SWITCH tło można przełączać `?tlo=N`; wybór zostaje w sesji."""
    if not settings.HOME_BACKGROUND_SWITCH:
        return HOME_BACKGROUND_DEFAULT
    raw = request.GET.get("tlo")
    if raw is not None:
        known = raw.isdigit() and int(raw) in HOME_BACKGROUNDS
        request.session["tlo"] = int(raw) if known else HOME_BACKGROUND_DEFAULT
    return request.session.get("tlo", HOME_BACKGROUND_DEFAULT)


def _frames_manifest():
    try:
        return json.loads(HOME_FRAMES_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fonts_url(ramy):
    """Czcionki podpisów z Google Fonts, przycięte do liter użytych w podpisach (`text=`)."""
    families = sorted({r["font_spec"] for r in ramy if r.get("font_spec")})
    letters = "".join(sorted({ch for r in ramy for ch in r.get("caption", "")}))
    if not families or not letters:
        return ""
    query = [("family", family) for family in families] + [("text", letters), ("display", "swap")]
    return "https://fonts.googleapis.com/css2?" + urlencode(query, quote_via=url_quote)


def _home_frames(tlo):
    """Warianty plakatu w ramie dla wybranego tła albo None, gdy tło ich nie ma."""
    manifest = _frames_manifest()
    if not manifest or manifest.get("tlo") != tlo or not manifest.get("ramy"):
        return None
    ramy = manifest["ramy"]
    return {
        "ramy": ramy,
        "size": manifest["size"],
        "frame": json.dumps(manifest["frame"], separators=(",", ":")),
        "fonts_url": _fonts_url(ramy),
        "prompt_suffix": PROMPT_SUFFIX,
    }


# ---------------------------------------------------------------------------
# Inspiracje: galeria plakatów z promptami (prototyp na manifeście ram, bez bazy)
# ---------------------------------------------------------------------------


def _inspirations():
    manifest = _frames_manifest() or {}
    return [r for r in manifest.get("ramy", []) if r.get("prompt") and not r.get("empty")]


# Filtry galerii: klucz w manifeście (kolumna w podpisy.txt) i etykieta na stronie
INSPIRATION_FILTERS = [("pasja", "Pasja"), ("styl", "Styl"), ("okazja", "Okazja")]


@require_GET
def inspirations(request):
    wpisy = _inspirations()
    active = {}
    filters = []
    for key, label in INSPIRATION_FILTERS:
        values = sorted({t for w in wpisy for t in w.get(key, [])}, key=str.lower)
        chosen = request.GET.get(key, "").strip()
        if chosen in values:
            active[key] = chosen
        filters.append((key, label, values))
    for key, chosen in active.items():
        wpisy = [w for w in wpisy if chosen in w.get(key, [])]

    def link(key, value):
        params = {k: v for k, v in active.items() if k != key}
        if value:
            params[key] = value
        return "?" + urlencode(params) if params else reverse("web:inspirations")

    return render(
        request,
        "poster/inspiracje.html",
        {
            "wpisy": wpisy,
            "filtry": [
                {
                    "label": label,
                    "all_url": link(key, ""),
                    "all_current": key not in active,
                    "options": [(v, link(key, v), active.get(key) == v) for v in values],
                }
                for key, label, values in filters
            ],
            "fonts_url": _fonts_url(wpisy),
        },
    )


INSPIRATIONS_MORE = 4  # „Inne inspiracje” pod promptem


def _similar(wpis, wpisy):
    """Inne wpisy, najpierw te ze wspólnym tagiem (pasja, styl, okazja), dalej w kolejności galerii."""
    tags = {t for key, _ in INSPIRATION_FILTERS for t in wpis.get(key, [])}
    others = [w for w in wpisy if w["file"] != wpis["file"]]
    shared = [
        w for w in others if tags & {t for key, _ in INSPIRATION_FILTERS for t in w.get(key, [])}
    ]
    return (shared + [w for w in others if w not in shared])[:INSPIRATIONS_MORE]


@require_GET
def inspiration(request, slug):
    wpisy = _inspirations()
    wpis = next((w for w in wpisy if w["file"] == slug), None)
    if wpis is None:
        raise Http404
    inne = _similar(wpis, wpisy)
    return render(
        request,
        "poster/inspiracja.html",
        {
            "wpis": wpis,
            "prompt": f"{wpis['prompt']} {PROMPT_SUFFIX}".strip(),
            "inne": inne,
            "fonts_url": _fonts_url([wpis, *inne]),
        },
    )


@require_http_methods(["POST"])
def prompt_copied(request):
    """Klient skopiował prompt z kartki przy ramie: na razie tylko wpis w logu, licznik na później."""
    log.info("prompt skopiowany: %s", request.POST.get("rama", "")[:60])
    return HttpResponse(status=204)


@require_http_methods(["GET", "POST"])
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            asset = upload_poster_asset(form.cleaned_data["file"])
        except UploadError as exc:
            form.add_error("file", str(exc))
        else:
            # Format kliknięty na stronie głównej ("Zamów") przechodzi do konfiguratora
            url = reverse("web:configure", args=[asset.pk])
            wanted = request.POST.get("format", "")
            if wanted and active_formats().filter(code=wanted).exists():
                url += "?" + urlencode({"format": wanted})
            return redirect(url)
    tlo = _home_background(request)
    return render(
        request,
        "poster/s1_upload.html",
        {
            "form": form,
            "max_mb": 30,
            "formats": active_formats(),
            "tlo": tlo,
            "ramy": _home_frames(tlo),
            "tlo_switch": HOME_BACKGROUNDS.items() if settings.HOME_BACKGROUND_SWITCH else None,
        },
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
        "format": format_of(opts).pk if item else _default_format(cards, request.GET.get("format")),
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


def _default_format(cards, wanted=None):
    """Format wybrany na stronie głównej, jeśli plik się do niego nadaje, inaczej pierwszy dostępny."""
    for c in cards:
        if wanted and c["format"].code == wanted and not c["quality"]["blocked"]:
            return c["format"].pk
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
