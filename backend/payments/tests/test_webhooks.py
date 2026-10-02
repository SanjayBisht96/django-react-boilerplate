import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone as tz
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from payments.models import Payment

TEST_SECRET = "test-webhook-secret"


def sign_body(body: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def make_webhook_payload(processor_reference, status="pending", failure_code=None):
    return {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "processor_reference": processor_reference,
        "status": status,
        "failure_code": failure_code,
        "occurred_at": datetime.now(tz.utc).isoformat(),
    }


@override_settings(PROCESSOR_WEBHOOK_SECRET=TEST_SECRET)
class WebhookSignatureTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/webhooks/processor"
        self.payment = Payment.objects.create(
            amount=1000,
            currency="USD",
            payment_token="tok_test_abc123",
            processor_reference="pr_test_123",
        )

    def test_valid_signature_accepted(self):
        """Webhook with valid signature is processed."""
        payload = make_webhook_payload(self.payment.processor_reference)
        body = json.dumps(payload).encode()
        signature = sign_body(body)

        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 200)

    def test_missing_signature_rejected(self):
        """Webhook without signature is rejected."""
        payload = make_webhook_payload(self.payment.processor_reference)
        body = json.dumps(payload).encode()

        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "pending")

    def test_invalid_signature_rejected(self):
        """Webhook with invalid signature is rejected."""
        payload = make_webhook_payload(self.payment.processor_reference)
        body = json.dumps(payload).encode()

        response = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE="invalid_signature",
        )
        self.assertEqual(response.status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "pending")

    def test_tampered_body_rejected(self):
        """Webhook with valid signature but tampered body is rejected."""
        payload = make_webhook_payload(self.payment.processor_reference)
        body = json.dumps(payload).encode()
        signature = sign_body(body)

        # Tamper with the body after signing
        tampered = json.dumps({**payload, "status": "succeeded"}).encode()

        response = self.client.post(
            self.url,
            data=tampered,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=signature,
        )
        self.assertEqual(response.status_code, 400)


@override_settings(PROCESSOR_WEBHOOK_SECRET=TEST_SECRET)
class WebhookIdempotencyTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/webhooks/processor"
        self.payment = Payment.objects.create(
            amount=1000,
            currency="USD",
            payment_token="tok_test_abc123",
            processor_reference="pr_test_123",
        )

    def test_duplicate_webhook_same_event_id_one_ledger_entry(self):
        """Same event_id delivered twice writes exactly one ledger entry."""
        payload = make_webhook_payload(self.payment.processor_reference, status="succeeded")
        body = json.dumps(payload).encode()
        signature = sign_body(body)

        response1 = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=signature,
        )
        self.assertEqual(response1.status_code, 200)

        response2 = self.client.post(
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=signature,
        )
        self.assertEqual(response2.status_code, 200)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "succeeded")
        self.assertEqual(self.payment.ledger_entries.count(), 1)

    def test_pending_then_succeeded(self):
        """Normal flow: pending then succeeded."""
        pending_payload = make_webhook_payload(
            self.payment.processor_reference, status="pending"
        )
        body = json.dumps(pending_payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "pending")

        success_payload = make_webhook_payload(
            self.payment.processor_reference, status="succeeded"
        )
        body = json.dumps(success_payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "succeeded")
        self.assertEqual(self.payment.ledger_entries.count(), 2)

    def test_reverse_order_webhooks(self):
        """Final event first, then pending — ends in correct final status."""
        success_payload = make_webhook_payload(
            self.payment.processor_reference, status="succeeded"
        )
        body = json.dumps(success_payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "succeeded")

        # Late pending should be ignored
        pending_payload = make_webhook_payload(
            self.payment.processor_reference, status="pending"
        )
        body = json.dumps(pending_payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "succeeded")
        self.assertEqual(self.payment.ledger_entries.count(), 1)

    def test_late_pending_after_terminal_ignored(self):
        """A late pending after a terminal event must not regress the status."""
        success_payload = make_webhook_payload(
            self.payment.processor_reference, status="succeeded"
        )
        body = json.dumps(success_payload).encode()
        sig = sign_body(body)

        self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )

        pending_payload = make_webhook_payload(
            self.payment.processor_reference, status="pending"
        )
        body = json.dumps(pending_payload).encode()
        sig = sign_body(body)

        self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "succeeded")
        self.assertEqual(self.payment.ledger_entries.count(), 1)

    def test_conflicting_terminal_events_first_wins(self):
        """Conflicting terminal events: first terminal wins, second is rejected."""
        success_payload = make_webhook_payload(
            self.payment.processor_reference, status="succeeded"
        )
        body = json.dumps(success_payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)

        failed_payload = make_webhook_payload(
            self.payment.processor_reference, status="failed", failure_code="card_declined"
        )
        body = json.dumps(failed_payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "succeeded")
        self.assertEqual(self.payment.ledger_entries.count(), 1)

    def test_failed_payment(self):
        """Payment can fail."""
        failed_payload = make_webhook_payload(
            self.payment.processor_reference, status="failed", failure_code="card_declined"
        )
        body = json.dumps(failed_payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            self.url, data=body, content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "failed")
        self.assertEqual(self.payment.failure_code, "card_declined")
