from django.urls import path

from . import views

app_name = "printing"

urlpatterns = [
    path("files/signed/<str:token>.pdf", views.signed_file, name="file"),
]
