from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.upload, name="upload"),
    path("configure/<uuid:asset_id>/", views.configure, name="configure"),
    path("configure/<uuid:asset_id>/price/", views.price_partial, name="price_partial"),
    path("assets/<uuid:asset_id>/preview.jpg", views.asset_preview, name="asset_preview"),
    path("orders/<str:token>/preview/", views.preview, name="preview"),
    path("orders/<str:token>/mockup.png", views.mockup_image, name="mockup_image"),
    path("orders/<str:token>/fragment/<str:mode>.png", views.fragment_image, name="fragment_image"),
    path("orders/<str:token>/checkout/", views.checkout, name="checkout"),
    path("orders/<str:token>/confirmation/", views.order_confirmation, name="order_confirmation"),
    path("status/<str:token>/", views.order_status, name="order_status"),
    path("strony/<slug:slug>/", views.static_page, name="static_page"),
]
