#!/usr/bin/env bash
# Register the Debezium outbox connector (Postgres WAL → Kafka).
# Requires: docker compose up -d connect kafka db
set -euo pipefail

CONNECT_URL=${CONNECT_URL:-http://localhost:8083}

echo "Waiting for Kafka Connect at ${CONNECT_URL}..."
until curl -sf "${CONNECT_URL}/connectors" > /dev/null; do
  sleep 3
done

echo "Enabling REPLICATION on the payment_gateway user..."
docker compose exec db psql -U payment_gateway -d payment_gateway \
  -c "ALTER USER payment_gateway WITH REPLICATION;"

echo "Creating/resetting the Debezium connector..."
curl -sf -X PUT "${CONNECT_URL}/connectors/payments-outbox/config" \
  -H "Content-Type: application/json" \
  -d '{
    "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
    "database.hostname": "db",
    "database.port": "5432",
    "database.user": "payment_gateway",
    "database.password": "password",
    "database.dbname": "payment_gateway",
    "topic.prefix": "payment_gateway",
    "plugin.name": "pgoutput",
    "slot.name": "payments_outbox_slot",
    "publication.name": "payments_outbox_publication",
    "publication.autocreate.mode": "filtered",
    "table.include.list": "public.payments_outboxevent",
    "tombstones.on.delete": "false",
    "transforms": "route",
    "transforms.route.type": "org.apache.kafka.connect.transforms.RegexRouter",
    "transforms.route.regex": ".*",
    "transforms.route.replacement": "payment_events"
  }'

echo
echo "Debezium connector registered. CDC will now stream INSERT/UPDATE on"
echo "payments_outboxevent → topic 'payment_events'."
