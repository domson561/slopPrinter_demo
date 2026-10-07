from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("payments/mock/callback/", views.payment_callback, name="payment_callback"),
    path("payments/mock/<str:external_id>/", views.mock_payment, name="mock_payment"),
    path("payments/retry/<str:token>/", views.retry_payment, name="retry_payment"),
]
