import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone as tz

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from mock_processor.models import Charge
from mock_processor.webhook import deliver_webhook

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Simulate webhook deliveries for a charge in various modes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reference",
            type=str,
            required=True,
            help="The processor reference (pr_...) to simulate webhooks for.",
        )
        parser.add_argument(
            "--mode",
            type=str,
            required=True,
            choices=["normal", "duplicate", "reverse", "concurrent"],
            help="Delivery mode to simulate.",
        )

    def handle(self, *args, **options):
        reference = options["reference"]
        mode = options["mode"]

        try:
            charge = Charge.objects.get(processor_reference=reference)
        except Charge.DoesNotExist:
            raise CommandError(f"Charge with reference '{reference}' not found.")

        self.stdout.write(
            f"Simulating webhooks for charge {reference} in mode: {mode}"
        )

        events = self._build_events(charge)

        if mode == "normal":
            self._deliver_normal(events)
        elif mode == "duplicate":
            self._deliver_duplicate(events)
        elif mode == "reverse":
            self._deliver_reverse(events)
        elif mode == "concurrent":
            self._deliver_concurrent(events)

        self.stdout.write(self.style.SUCCESS("Done."))

    def _build_events(self, charge):
        """Build the webhook events for a charge based on test value rules."""
        last4 = charge.payment_method.last4
        method = charge.payment_method.method

        if last4 == "0002":
            if method == "card":
                final_status, final_failure = "failed", "card_declined"
            else:
                final_status, final_failure = "failed", "insufficient_funds"
        elif last4 == "0119":
            final_status, final_failure = "failed", "processor_error"
        elif last4 == "0341":
            final_status, final_failure = "succeeded", None
        else:
            final_status, final_failure = "succeeded", None

        now = datetime.now(tz.utc).isoformat()

        pending_event = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "processor_reference": charge.processor_reference,
            "status": "pending",
            "failure_code": None,
            "occurred_at": now,
        }

        final_event = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "processor_reference": charge.processor_reference,
            "status": final_status,
            "failure_code": final_failure,
            "occurred_at": now,
        }

        return [pending_event, final_event]

    def _get_webhook_url(self):
        base = getattr(settings, "PAYMENTS_WEBHOOK_URL", "http://localhost:8000")
        return f"{base}/webhooks/processor"

    def _get_secret(self):
        return getattr(settings, "PROCESSOR_WEBHOOK_SECRET", "test-secret")

    def _deliver_normal(self, events):
        """Deliver pending first, then the final event."""
        for event in events:
            self._deliver(event)

    def _deliver_duplicate(self, events):
        """Deliver every event twice."""
        for event in events:
            self._deliver(event)
            self._deliver(event)

    def _deliver_reverse(self, events):
        """Deliver the final event first, then pending."""
        for event in reversed(events):
            self._deliver(event)

    def _deliver_concurrent(self, events):
        """Deliver two copies of the final event simultaneously."""
        final_event = events[-1]
        self._deliver_concurrent_pair(final_event)

    def _deliver(self, event):
        url = self._get_webhook_url()
        secret = self._get_secret()
        success = deliver_webhook(url, event, secret)
        status_str = "OK" if success else "FAILED"
        self.stdout.write(f"  Delivered {event['status']} event: {status_str}")

    def _deliver_concurrent_pair(self, event):
        """Deliver two copies of the same event using threads."""
        url = self._get_webhook_url()
        secret = self._get_secret()
        raw_body = json.dumps(event).encode()

        results = []

        def deliver():
            try:
                response = requests.post(
                    url,
                    data=raw_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Processor-Signature": self._sign(raw_body, secret),
                    },
                    timeout=10,
                )
                results.append(response.status_code == 200)
            except requests.RequestException:
                results.append(False)

        threads = [threading.Thread(target=deliver) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        success_count = sum(1 for r in results if r)
        self.stdout.write(
            f"  Concurrent delivery: {success_count}/2 succeeded"
        )

    def _sign(self, raw_body: bytes, secret: str) -> str:
        import hashlib
        import hmac

        return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
