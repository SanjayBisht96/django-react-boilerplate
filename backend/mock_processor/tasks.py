import time
import uuid
from datetime import datetime, timezone as tz

from django.conf import settings

from payment_gateway import celery_app

from .models import Charge
from .webhook import deliver_webhook


def _final_outcome(last4: str, method: str):
    if last4 == "0002":
        return ("failed", "card_declined") if method == "card" else ("failed", "insufficient_funds")
    if last4 == "0119":
        return ("failed", "processor_error")
    return ("succeeded", None)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5)
def process_charge_webhook(self, processor_reference: str):
    """Simulate async processor settlement and notify payments via webhook."""
    try:
        charge = Charge.objects.get(processor_reference=processor_reference)
    except Charge.DoesNotExist:
        raise self.retry(exc=ValueError(f"Charge {processor_reference} not found"))

    base = getattr(settings, "PAYMENTS_WEBHOOK_URL", "http://localhost:8000")
    url = f"{base}/webhooks/processor"
    secret = getattr(settings, "PROCESSOR_WEBHOOK_SECRET", "test-secret")

    def _event(status, failure_code):
        return {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "processor_reference": charge.processor_reference,
            "status": status,
            "failure_code": failure_code,
            "occurred_at": datetime.now(tz.utc).isoformat(),
        }

    # Pending first, then final outcome (mirrors simulate_webhooks 'normal')
    deliver_webhook(url, _event("pending", None), secret)
    time.sleep(2)

    final_status, failure_code = _final_outcome(charge.payment_method.last4, charge.payment_method.method)
    deliver_webhook(url, _event(final_status, failure_code), secret)

    charge.status = final_status
    charge.failure_code = failure_code
    charge.save(update_fields=["status", "failure_code", "modified"])
    return {"processor_reference": processor_reference, "status": final_status}
