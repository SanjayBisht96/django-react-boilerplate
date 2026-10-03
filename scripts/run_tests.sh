#!/usr/bin/env bash
# Run the Django test suite against the dedicated Postgres test DB.
# - starts the db-test container
# - waits until Postgres is ready
# - runs the tests (Django creates/migrates the test database itself)
# - drops the test database afterwards
set -uo pipefail

cd "$(dirname "$0")/.."

echo "==> Starting db-test container"
docker compose up -d db-test

echo "==> Waiting for Postgres to accept connections"
until docker compose exec -T db-test pg_isready -U payment_gateway >/dev/null 2>&1; do
  sleep 1
done

echo "==> Running tests"
if [ $# -eq 0 ]; then
  .venv/bin/python backend/manage.py test backend/ --noinput --parallel
else
  .venv/bin/python backend/manage.py test "$@" --noinput --parallel
fi
status=$?

echo "==> Dropping test database(s)"
docker compose exec -T db-test psql -U payment_gateway -d postgres -tAc \
  "SELECT 'DROP DATABASE IF EXISTS \"' || datname || '\" WITH (FORCE);' FROM pg_database WHERE datname LIKE 'test_payment_gateway_test%'" |
  docker compose exec -T db-test psql -U payment_gateway -d postgres

exit $status
