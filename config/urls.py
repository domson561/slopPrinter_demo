from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("ops/", include("apps.ops.urls")),
]
if settings.GH_MOCK_ENABLED:
    urlpatterns.append(path("", include("apps.gh_mock.urls")))
urlpatterns += [
    path("", include("apps.orders.urls")),
    path("", include("apps.web.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
