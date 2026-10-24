"""CDC-style outbox publisher: polls OutboxEvent and publishes to Kafka."""

import time

from django.core.management.base import BaseCommand
from django.db import transaction

from payments.kafka_utils import get_producer
from payments.models import OutboxEvent


class Command(BaseCommand):
    help = "Publish undelivered OutboxEvent rows to Kafka (outbox/CDC worker)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep polling")
        parser.add_argument("--interval", type=float, default=1.0)

    def handle(self, *args, **options):
        producer = get_producer()
        try:
            while True:
                # ISSUE 7 FIX: Atomically claim undelivered events using
                # select_for_update(nowait=True) within a transaction. This locks
                # the rows so concurrent publisher workers cannot fetch and deliver
                # the same events, preventing duplicate Kafka message delivery.
                with transaction.atomic():
                    events = list(
                        OutboxEvent.objects.select_for_update(nowait=True)
                        .filter(delivered=False)
                        .order_by("created")[:100]
                    )
                    for event in events:
                        event.delivered = True
                        event.save(update_fields=["delivered"])

                count = 0
                for event in events:
                    producer.send("payment_events", value={
                        "id": str(event.id),
                        "event_type": event.event_type,
                        "payload": event.payload,
                    })
                    count += 1
                producer.flush()
                if count:
                    self.stdout.write(f"Published {count} outbox events")
                if not options["loop"]:
                    break
                time.sleep(options["interval"])
        finally:
            producer.close()
