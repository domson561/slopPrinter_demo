from django.urls import path

from . import views

app_name = "ops"

urlpatterns = [
    path("login/", views.OpsLoginView.as_view(), name="login"),
    path("logout/", views.OpsLogoutView.as_view(), name="logout"),
    path("", views.order_list, name="order_list"),
    path("queue/", views.queue, name="queue"),
    path("settings/", views.settings_view, name="settings"),
    path("orders/<str:number>/", views.order_detail, name="order_detail"),
    path("orders/<str:number>/action/", views.order_action, name="order_action"),
    path("orders/<str:number>/print.pdf", views.order_print_file, name="order_print_file"),
]
