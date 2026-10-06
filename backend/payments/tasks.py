import logging

from django.conf import settings
from django.core.mail import send_mail

from payment_gateway import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def send_payment_email(self, event: dict) -> dict:
    """Send a payment status email to the customer."""
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
