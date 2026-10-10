from django.urls import path

from apps.poster import views as poster

from . import views

app_name = "web"

# S1-S3 i obrazy dostarcza produkt (plakat); nazwy tras zostają wspólne dla szablonów.
urlpatterns = [
    path("", poster.upload, name="upload"),
    path("configure/<uuid:asset_id>/", poster.configure, name="configure"),
    path("configure/<uuid:asset_id>/price/", poster.price_partial, name="price_partial"),
    path("assets/<uuid:asset_id>/preview.jpg", poster.asset_preview, name="asset_preview"),
    path("orders/<str:token>/preview/", poster.preview, name="preview"),
    path("orders/<str:token>/mockup.png", poster.mockup_image, name="mockup_image"),
    path(
        "orders/<str:token>/fragment/<str:mode>.png", poster.fragment_image, name="fragment_image"
    ),
    path("orders/<str:token>/checkout/", views.checkout, name="checkout"),
    path("orders/<str:token>/confirmation/", views.order_confirmation, name="order_confirmation"),
    path("status/<str:token>/", views.order_status, name="order_status"),
    path("strony/<slug:slug>/", views.static_page, name="static_page"),
]
