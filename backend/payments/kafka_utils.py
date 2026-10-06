import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

TOPIC = "payment_events"


def get_producer():
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=getattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
        retries=3,
    )


def get_consumer(group_id: str, auto_offset_reset: str = "earliest", topics=None):
    from kafka import KafkaConsumer

    return KafkaConsumer(
        *(topics or ["payment_events"]),
        bootstrap_servers=getattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        group_id=group_id,
        auto_offset_reset=auto_offset_reset,
        enable_auto_commit=True,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
    )
