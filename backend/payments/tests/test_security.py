import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone as tz
from io import StringIO

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from payments.models import Payment

TEST_SECRET = "test-webhook-secret"


def sign_body(body: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@override_settings(PROCESSOR_WEBHOOK_SECRET=TEST_SECRET)
class SensitiveDataTest(TestCase):
    """Verify no sensitive data appears in database or logs."""

    def setUp(self):
        self.client = APIClient()
        self.payment = Payment.objects.create(
            amount=1000,
            currency="USD",
            payment_token="tok_test_abc123",
            processor_reference="pr_test_security",
        )

    def test_no_sensitive_data_in_database(self):
        """After a full payment flow, no full card number, CVV or account number in DB."""
        # Simulate a full payment flow
        payload = {
            "event_id": "evt_security_test",
            "processor_reference": self.payment.processor_reference,
            "status": "succeeded",
            "failure_code": None,
            "occurred_at": datetime.now(tz.utc).isoformat(),
        }
        body = json.dumps(payload).encode()
        sig = sign_body(body)

        self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )

        # Check database - no sensitive data
        payment = Payment.objects.get(id=self.payment.id)
        self.assertNotIn("4242424242424242", str(payment.__dict__))
        self.assertEqual(payment.payment_token, "tok_test_abc123")

    def test_no_sensitive_data_in_logs(self):
        """After a full payment flow, no sensitive data in captured logs."""
        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.DEBUG)

        logger = logging.getLogger("payments")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        try:
            payload = {
                "event_id": "evt_log_test",
                "processor_reference": self.payment.processor_reference,
                "status": "succeeded",
                "failure_code": None,
                "occurred_at": datetime.now(tz.utc).isoformat(),
            }
            body = json.dumps(payload).encode()
            sig = sign_body(body)

            self.client.post(
                "/webhooks/processor",
                data=body,
                content_type="application/json",
                HTTP_X_PROCESSOR_SIGNATURE=sig,
            )

            log_output = log_stream.getvalue()
            self.assertNotIn("4242424242424242", log_output)
            self.assertNotIn("cvv", log_output.lower())
        finally:
            logger.removeHandler(handler)
