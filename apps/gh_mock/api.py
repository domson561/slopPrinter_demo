"""
Endpointy sklepowe atrapy GH, `/shop-api/v1/` (kontrakt pkt 2-5, 7). Tylko token sklepu,
bez sesji i CSRF. Prawdziwe GH wystawi to samo w DRF; kształt odpowiedzi ma się zgadzać.
"""

import json
import logging
from functools import wraps

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import GhOrder, GhShop
from .services import (
    Conflict,
    NotAllowed,
    ValidationFailed,
    accept_order,
    changes_since,
    order_response,
    replace_file_link,
)

log = logging.getLogger("slop.gh_mock")


def _client_ip(request) -> str:
    return request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "?"))


def shop_token_required(view):
    @csrf_exempt
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        shop = GhShop.authenticate(token.strip()) if scheme == "Token" else None
        if shop is None:
            log.warning("GH: odrzucony token (%s…) z %s", token[:4], _client_ip(request))
            return JsonResponse({"detail": "nieprawidłowy lub cofnięty token"}, status=401)
        request.gh_shop = shop
        return view(request, *args, **kwargs)

    return wrapper


def _json_body(request):
    try:
        return json.loads(request.body.decode() or "{}"), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse({"errors": {"body": ["nieprawidłowy JSON"]}}, status=400)


@shop_token_required
@require_POST
def orders_create(request):
    body, error = _json_body(request)
    if error:
        return error
    try:
        order, created = accept_order(request.gh_shop, body)
    except ValidationFailed as exc:
        return JsonResponse({"errors": exc.errors}, status=400)
    except Conflict:
        return JsonResponse(
            {"errors": {"shop_order_number": ["numer istnieje z inną treścią"]}}, status=409
        )
    return JsonResponse(order_response(order), status=201 if created else 200)


def _get_order(request, shop_order_number):
    return GhOrder.objects.filter(shop=request.gh_shop, shop_order_number=shop_order_number).first()


@shop_token_required
@require_GET
def orders_changes(request):
    since_raw = request.GET.get("since")
    since = None
    if since_raw:
        since = parse_datetime(since_raw)
        if since is None:
            return JsonResponse({"errors": {"since": ["data ISO 8601"]}}, status=400)
        if timezone.is_naive(since):
            since = timezone.make_aware(since, timezone.get_default_timezone())
    server_time = timezone.now()
    orders = changes_since(request.gh_shop, since)
    return JsonResponse(
        {"server_time": server_time.isoformat(), "orders": [o.to_state() for o in orders]}
    )


@shop_token_required
@require_http_methods(["GET"])
def order_detail(request, shop_order_number):
    order = _get_order(request, shop_order_number)
    if order is None:
        return JsonResponse({"detail": "nieznany numer zamówienia"}, status=404)
    return JsonResponse(order.to_state())


@shop_token_required
@require_POST
def order_files(request, shop_order_number):
    order = _get_order(request, shop_order_number)
    if order is None:
        return JsonResponse({"detail": "nieznany numer zamówienia"}, status=404)
    body, error = _json_body(request)
    if error:
        return error
    try:
        replace_file_link(order, body)
    except ValidationFailed as exc:
        return JsonResponse({"errors": exc.errors}, status=400)
    except NotAllowed as exc:
        return JsonResponse({"errors": {"file": [str(exc)]}}, status=400)
    return JsonResponse(order.to_state())
