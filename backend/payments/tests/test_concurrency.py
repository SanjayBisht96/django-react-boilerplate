import hashlib
import hmac
import json
import threading
import uuid
from datetime import datetime, timezone as tz
from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from payments.models import Payment

TEST_SECRET = "test-webhook-secret"

# SQLite doesn't support concurrent writes — these tests require PostgreSQL
IS_SQLITE = connection.vendor == "sqlite"


def sign_body(body: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@override_settings(PROCESSOR_WEBHOOK_SECRET=TEST_SECRET)
class ConcurrentWebhookTest(TransactionTestCase):
    """
    Test that two truly concurrent deliveries of the same final event,
    on separate database connections, end in one correct state with one ledger entry.
    """

    def setUp(self):
        self.url = "/webhooks/processor"
        self.payment = Payment.objects.create(
            amount=1000,
            currency="USD",
            payment_token="tok_test_abc123",
            processor_reference="pr_test_concurrent",
        )

    @skipIf(IS_SQLITE, "SQLite does not support concurrent writes; use PostgreSQL")
    def test_concurrent_same_final_event(self):
        """Two simultaneous deliveries of the same final event → one ledger entry."""
        payload = {
            "event_id": "evt_concurrent_test",
            "processor_reference": self.payment.processor_reference,
            "status": "succeeded",
            "failure_code": None,
            "occurred_at": datetime.now(tz.utc).isoformat(),
        }
        body = json.dumps(payload).encode()
        signature = sign_body(body)

        results = []
        errors = []

        def deliver():
            try:
                client = APIClient()
                response = client.post(
                    self.url,
                    data=body,
                    content_type="application/json",
                    HTTP_X_PROCESSOR_SIGNATURE=signature,
                )
                results.append(response.status_code)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=deliver) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r == 200 for r in results))

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "succeeded")
        self.assertEqual(self.payment.ledger_entries.count(), 1)

    @skipIf(IS_SQLITE, "SQLite does not support concurrent writes; use PostgreSQL")
    def test_concurrent_different_events(self):
        """Two simultaneous different events → both processed, correct final state."""
        payload1 = {
            "event_id": "evt_concurrent_1",
            "processor_reference": self.payment.processor_reference,
            "status": "pending",
            "failure_code": None,
            "occurred_at": datetime.now(tz.utc).isoformat(),
        }
        payload2 = {
            "event_id": "evt_concurrent_2",
            "processor_reference": self.payment.processor_reference,
            "status": "succeeded",
            "failure_code": None,
            "occurred_at": datetime.now(tz.utc).isoformat(),
        }

        body1 = json.dumps(payload1).encode()
        sig1 = sign_body(body1)
        body2 = json.dumps(payload2).encode()
        sig2 = sign_body(body2)

        results = []
        errors = []

        def deliver(body, sig):
            try:
                client = APIClient()
                response = client.post(
                    self.url,
                    data=body,
                    content_type="application/json",
                    HTTP_X_PROCESSOR_SIGNATURE=sig,
                )
                results.append(response.status_code)
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=deliver, args=(body1, sig1))
        t2 = threading.Thread(target=deliver, args=(body2, sig2))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(results), 2)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "succeeded")
        self.assertEqual(self.payment.ledger_entries.count(), 2)
