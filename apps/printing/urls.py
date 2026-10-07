from django.urls import path

from . import views

app_name = "printing"

urlpatterns = [
    path("api/printer/events/", views.printer_webhook, name="webhook"),
    path("mock-printer/", views.mock_printer_dashboard, name="mock_dashboard"),
    path("mock-printer/toggle-auto/", views.mock_printer_toggle_auto, name="mock_toggle_auto"),
    path("mock-printer/tick/", views.mock_printer_tick, name="mock_tick"),
    path("mock-printer/<str:external_id>/", views.mock_printer_job, name="mock_job"),
    path("mock-printer/<str:external_id>/action/", views.mock_printer_action, name="mock_action"),
    path("mock-printer/<str:external_id>/file/", views.mock_printer_file, name="mock_file"),
]
