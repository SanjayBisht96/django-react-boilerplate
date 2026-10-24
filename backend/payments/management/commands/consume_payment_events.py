"""Consume payment events from Kafka and dispatch a celery email task per event."""

import json
import logging

from django.core.management.base import BaseCommand

from payments.kafka_utils import get_consumer
from payments.tasks import send_payment_email

logger = logging.getLogger(__name__)


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

            # ISSUE 9 FIX: Dispatch the email task and only commit the Kafka offset
            # after successful dispatch. Previously, enable_auto_commit=True meant
            # offsets were committed automatically, so a crash after dispatch but
            # before processing would lose the event. Now we use manual commit to
            # ensure at-least-once delivery semantics.
            try:
                send_payment_email.delay(event)
                self.stdout.write(f"Dispatched email task for {event.get('event_type')}")
                # Commit offset only after successful task dispatch
                consumer.commit()
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to dispatch email task: %s", exc)
                # Do not commit offset so the message will be redelivered
