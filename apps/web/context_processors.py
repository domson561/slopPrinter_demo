from django.conf import settings


def site(request):
    return {"site_name": settings.SITE_NAME, "site_url": settings.SITE_URL}
