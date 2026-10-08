from django.urls import path

from . import api, views

app_name = "gh_mock"

urlpatterns = [
    # API z kontraktu (pkt 2: prefiks /shop-api/v1/, poza /api/ GH).
    path("shop-api/v1/orders/", api.orders_create, name="api_orders"),
    path("shop-api/v1/orders/changes/", api.orders_changes, name="api_changes"),
    path("shop-api/v1/orders/<str:shop_order_number>/", api.order_detail, name="api_order"),
    path("shop-api/v1/orders/<str:shop_order_number>/files/", api.order_files, name="api_files"),
    # Pulpit biura GH (symulacja).
    path("gh-mock/", views.dashboard, name="dashboard"),
    path("gh-mock/download/", views.download_all, name="download_all"),
    path("gh-mock/toggle-auto/", views.toggle_auto, name="toggle_auto"),
    path("gh-mock/tick/", views.tick, name="tick"),
    path("gh-mock/<int:pk>/", views.order_detail, name="order"),
    path("gh-mock/<int:pk>/action/", views.order_action, name="action"),
]
