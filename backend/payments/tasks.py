import logging

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail

from payment_gateway import celery_app

logger = logging.getLogger(__name__)

# ISSUE 8 FIX: Cache key prefix for email idempotency. Prevents duplicate emails
# when the same OutboxEvent is delivered to Kafka more than once (e.g., due to
# outbox publisher race conditions).
EMAIL_SENT_CACHE_PREFIX = "payment_email_sent:"
EMAIL_SENT_CACHE_TTL = 86400  # 24 hours


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def send_payment_email(self, event: dict) -> dict:
    """Send a payment status email to the customer."""
    # ISSUE 8 FIX: Deduplicate email sends by outbox event ID. If the same event
    # is delivered twice (e.g., duplicate Kafka message), skip the second send.
    event_id = event.get("id")
    if event_id:
        cache_key = f"{EMAIL_SENT_CACHE_PREFIX}{event_id}"
        if cache.get(cache_key):
            logger.info("Email already sent for event %s; skipping duplicate", event_id)
            return {"sent": False, "reason": "duplicate_event"}
        # Mark as sent before attempting to send, so a retry won't re-send
        cache.set(cache_key, "1", timeout=EMAIL_SENT_CACHE_TTL)

    payload = event.get("payload", {})
    recipient = payload.get("customer_email")
    if not recipient:
        logger.info("No customer_email for event %s; skipping email", event.get("event_type"))
        return {"sent": False, "reason": "no_recipient"}

    # Resolve the token to a last-4 card reference for the email body
    card_ref = "unknown card"
    token = payload.get("payment_token")
    if token:
        try:
            from mock_processor.models import PaymentMethod

            pm = PaymentMethod.objects.filter(token=token).first()
            if pm:
                card_ref = f"{pm.brand_or_bank_type} ****{pm.last4}"
        except Exception:  # noqa: BLE001
            logger.warning("Could not resolve payment token %s for email", token)

    subject = f"Payment {payload.get('status', 'update')}"
    amount = payload.get("amount")
    amount_str = f"{amount / 100:.2f} {payload.get('currency', 'USD')}" if isinstance(amount, (int, float)) else "unknown amount"
    message = (
        f"Payment ID: {payload.get('payment_id')}\n"
        f"Status: {payload.get('status')}\n"
        f"Amount: {amount_str}\n"
        f"Card: {card_ref}\n"
        f"Reference: {payload.get('processor_reference')}"
    )
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@example.com")
    try:
        send_mail(subject, message, from_email, [recipient], fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Email send failed: %s", exc)
        raise self.retry(exc=exc)

    logger.info("Sent '%s' email to %s", payload.get("status"), recipient)
    return {"sent": True, "to": recipient}
