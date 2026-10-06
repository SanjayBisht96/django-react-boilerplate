import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import IndexedTimeStampedModel


class Payment(IndexedTimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUCCEEDED = "succeeded", _("Succeeded")
        CREATED = "created", _("Created")
        AUTHORIZED = "authorized", _("Authorized")
        CAPTURED = "captured", _("Captured")
        SETTLED = "settled", _("Settled")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(
        _("idempotency key"), max_length=255, null=True, blank=True
    )
    idempotency_body_hash = models.CharField(
        _("idempotency body hash"), max_length=64, null=True, blank=True
    )
    amount = models.PositiveIntegerField(_("amount in minor units"))
    currency = models.CharField(_("currency"), max_length=3, default="USD")
    payment_token = models.CharField(_("payment token"), max_length=255)
    customer_email = models.CharField(
        _("customer email"), max_length=255, null=True, blank=True
    )
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.PENDING
    )
    failure_code = models.CharField(
        _("failure code"), max_length=50, null=True, blank=True
    )
    processor_reference = models.CharField(
        _("processor reference"), max_length=255, null=True, blank=True
    )

    class Meta:
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key", "idempotency_body_hash"],
                name="unique_idempotency_key_body",
            ),
        ]

    def __str__(self):
        return f"Payment {self.id} ({self.status})"

    @property
    def ledger(self):
        """Return ledger entries ordered by occurred_at."""
        return self.ledger_entries.order_by("occurred_at", "created")


class LedgerEntry(models.Model):
    """Append-only ledger entry. No updates or deletes allowed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
        verbose_name=_("payment"),
    )
    event_id = models.CharField(_("event ID"), max_length=255)
    status = models.CharField(_("status"), max_length=16)
    failure_code = models.CharField(
        _("failure code"), max_length=50, null=True, blank=True
    )
    occurred_at = models.DateTimeField(_("occurred at"))
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("ledger entry")
        verbose_name_plural = _("ledger entries")
        unique_together = ("payment", "event_id")
        ordering = ["occurred_at", "created"]

    def __str__(self):
        return f"LedgerEntry {self.event_id} → {self.status}"

    def save(self, *args, **kwargs):
        """Prevent updates to existing ledger entries."""
        if self.pk is not None and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValueError("Ledger entries are append-only and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion of ledger entries."""
        raise ValueError("Ledger entries are append-only and cannot be deleted.")


class OutboxEvent(models.Model):
    """Outbox row written atomically with the payment state transition.

    A CDC/publisher worker later publishes these rows to Kafka, which the
    Webhook Listener consumes to notify the merchant backend.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="outbox_events",
        verbose_name=_("payment"),
    )
    event_type = models.CharField(_("event type"), max_length=64)
    payload = models.JSONField(_("payload"))
    delivered = models.BooleanField(_("delivered"), default=False, db_index=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("outbox event")
        verbose_name_plural = _("outbox events")
        ordering = ["created"]

    def __str__(self):
        return f"OutboxEvent {self.event_type} for payment {self.payment_id}"
