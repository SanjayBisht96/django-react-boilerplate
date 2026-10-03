from django.urls import path

from . import views

app_name = "mock_processor"

urlpatterns = [
    path("tokenize", views.tokenize, name="tokenize"),
    path("charge", views.charge, name="charge"),
    path("charges", views.charges, name="charges"),
]
