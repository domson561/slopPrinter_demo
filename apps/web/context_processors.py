from django.conf import settings


def site(request):
    return {
        "site_name": settings.SITE_NAME,
        "site_url": settings.SITE_URL,
        "gh_mock_enabled": settings.GH_MOCK_ENABLED,
    }
