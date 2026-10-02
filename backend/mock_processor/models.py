import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import IndexedTimeStampedModel


class PaymentMethod(IndexedTimeStampedModel):
    class Method(models.TextChoices):
        CARD = "card", _("Card")
        BANK = "bank", _("Bank")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.CharField(_("token"), max_length=255, unique=True)
    method = models.CharField(_("method"), max_length=10, choices=Method.choices)
    last4 = models.CharField(_("last4"), max_length=4)
    brand_or_bank_type = models.CharField(_("brand_or_bank_type"), max_length=50)

    class Meta:
        verbose_name = _("payment method")
        verbose_name_plural = _("payment methods")

    def __str__(self):
        return f"{self.method} ending in {self.last4}"


class Charge(IndexedTimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    processor_reference = models.CharField(
        _("processor reference"), max_length=255, unique=True
    )
    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.PROTECT,
        related_name="charges",
        verbose_name=_("payment method"),
    )
    amount = models.PositiveIntegerField(_("amount in minor units"))
    currency = models.CharField(_("currency"), max_length=3, default="USD")
    status = models.CharField(
        _("status"), max_length=10, choices=Status.choices, default=Status.PENDING
    )
    failure_code = models.CharField(
        _("failure code"), max_length=50, null=True, blank=True
    )

    class Meta:
        verbose_name = _("charge")
        verbose_name_plural = _("charges")

    def __str__(self):
        return f"{self.processor_reference} ({self.status})"
