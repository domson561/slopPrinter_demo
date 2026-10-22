from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.http import HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve


def healthz(request):
    """Healthcheck przy deployu (Railway): proces odpowiada i baza jest osiągalna."""
    connection.ensure_connection()
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("healthz/", healthz),
    path("admin/", admin.site.urls),
    path("ops/", include("apps.ops.urls")),
]
if settings.GH_MOCK_ENABLED:
    urlpatterns.append(path("", include("apps.gh_mock.urls")))
urlpatterns += [
    path("", include("apps.printing.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.web.urls")),
]

if settings.DEBUG or settings.SERVE_MEDIA:
    urlpatterns.append(
        re_path(
            rf"^{settings.MEDIA_URL.lstrip('/')}(?P<path>.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT},
        )
    )
