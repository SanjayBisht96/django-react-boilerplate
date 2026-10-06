"""Consume payment events from Kafka and dispatch a celery email task per event."""

import json

from django.core.management.base import BaseCommand

from payments.kafka_utils import get_consumer
from payments.tasks import send_payment_email


class Command(BaseCommand):
    help = "Consume payment_events from Kafka; each event is processed by the send_payment_email celery task."

    def handle(self, *args, **options):
        consumer = get_consumer(group_id="payment-email-worker-2", topics=["payment_events"])
        for message in consumer:
            value = message.value
            # Debezium envelope format: {schema: ..., payload: {after, op, ...}}
            # or, with schemas disabled: {after, op, ...}
            if isinstance(value, dict) and isinstance(value.get("payload"), dict) and value["payload"].get("op") in ("c", "u", "d", "r"):
                envelope = value["payload"]
            elif isinstance(value, dict) and value.get("op") in ("c", "u", "d", "r"):
                envelope = value
            else:
                envelope = None

            if envelope is not None:
                if envelope.get("op") not in ("c", "r"):
                    continue  # only new/snapshot outbox rows trigger notifications
                after = envelope.get("after") or {}
                event_payload = after.get("payload")
                if isinstance(event_payload, str):
                    event_payload = json.loads(event_payload)
                event = {
                    "id": after.get("id"),
                    "event_type": after.get("event_type"),
                    "payload": event_payload,
                }
            else:
                # Legacy shape (from the old publish_outbox poller)
                event = value
            send_payment_email.delay(event)
            self.stdout.write(f"Dispatched email task for {event.get('event_type')}")
