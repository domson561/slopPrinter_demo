from django.conf import settings

from .cart import cart_for


def site(request):
    cart = cart_for(request) if hasattr(request, "session") else None
    return {
        "site_name": settings.SITE_NAME,
        "site_url": settings.SITE_URL,
        "gh_mock_enabled": settings.GH_MOCK_ENABLED,
        "cart": cart,
        "cart_count": cart.items.count() if cart else 0,
    }
