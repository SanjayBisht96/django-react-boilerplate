from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("payments", views.create_payment, name="create-payment"),
    path("payments/<uuid:payment_id>", views.get_payment, name="get-payment"),
    path("payments/<uuid:payment_id>/replay", views.replay_payment, name="replay-payment"),
]

webhook_urlpatterns = [
    path("processor", views.processor_webhook, name="processor-webhook"),
]
