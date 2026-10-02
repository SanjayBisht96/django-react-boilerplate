import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone as tz
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from mock_processor.models import Charge, PaymentMethod
from payments.models import LedgerEntry, Payment

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
class EndToEndPaymentFlowTest(TestCase):
    """End-to-end tests using seeded dummy data."""

    def setUp(self):
        self.client = APIClient()
        self._seed_data()

    def _seed_data(self):
        """Create test payment methods and charges."""
        # Create a normal card (succeeds)
        self.card_pm = PaymentMethod.objects.create(
            token="tok_test_card_success",
            method="card",
            last4="4242",
            brand_or_bank_type="visa",
        )
        # Create a card that will be declined
        self.declined_card_pm = PaymentMethod.objects.create(
            token="tok_test_card_declined",
            method="card",
            last4="0002",
            brand_or_bank_type="visa",
        )
        # Create a bank account (succeeds)
        self.bank_pm = PaymentMethod.objects.create(
            token="tok_test_bank_success",
            method="bank",
            last4="6789",
            brand_or_bank_type="checking",
        )

    def test_full_payment_flow_success(self):
        """Tokenize → Create → Webhook pending → Webhook succeeded."""
        # Step 1: Create payment
        response = self.client.post(
            "/api/payments",
            data=json.dumps({
                "amount": 5000,
                "currency": "USD",
                "payment_token": "tok_test_card_success",
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-flow-1",
        )
        self.assertEqual(response.status_code, 201)
        payment_id = response.json()["id"]
        processor_ref = response.json().get("processor_reference")

        # Step 2: Send pending webhook
        payload = make_webhook_payload(processor_ref, status="pending")
        body = json.dumps(payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)

        # Step 3: Send succeeded webhook
        payload = make_webhook_payload(processor_ref, status="succeeded")
        body = json.dumps(payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)

        # Step 4: Verify final state
        response = self.client.get(f"/api/payments/{payment_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "succeeded")
        self.assertEqual(len(data["ledger"]), 2)

    def test_full_payment_flow_failure(self):
        """Tokenize → Create → Webhook failed."""
        response = self.client.post(
            "/api/payments",
            data=json.dumps({
                "amount": 3000,
                "currency": "USD",
                "payment_token": "tok_test_card_declined",
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-flow-2",
        )
        self.assertEqual(response.status_code, 201)
        payment_id = response.json()["id"]
        processor_ref = response.json().get("processor_reference")

        # Send failed webhook
        payload = make_webhook_payload(
            processor_ref, status="failed", failure_code="card_declined"
        )
        body = json.dumps(payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)

        # Verify final state
        response = self.client.get(f"/api/payments/{payment_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["failure_code"], "card_declined")

    def test_idempotent_create_with_dummy_data(self):
        """Same idempotency key returns same payment."""
        body = {
            "amount": 7500,
            "currency": "USD",
            "payment_token": "tok_test_bank_success",
        }

        response1 = self.client.post(
            "/api/payments",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-idempotent-1",
        )
        self.assertEqual(response1.status_code, 201)

        response2 = self.client.post(
            "/api/payments",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-idempotent-1",
        )
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(response1.json()["id"], response2.json()["id"])

    def test_duplicate_webhook_ignored(self):
        """Duplicate webhook with same event_id is ignored."""
        # Create payment
        response = self.client.post(
            "/api/payments",
            data=json.dumps({
                "amount": 2000,
                "currency": "USD",
                "payment_token": "tok_test_card_success",
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-duplicate-webhook",
        )
        processor_ref = response.json().get("processor_reference")

        # Send same webhook twice
        payload = make_webhook_payload(processor_ref, status="succeeded")
        body = json.dumps(payload).encode()
        sig = sign_body(body)

        response1 = self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response1.status_code, 200)

        response2 = self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response2.status_code, 200)

        # Verify only one ledger entry
        payment = Payment.objects.get(processor_reference=processor_ref)
        self.assertEqual(payment.ledger_entries.count(), 1)

    def test_reverse_order_webhooks(self):
        """Final event first, then pending — correct final status."""
        # Create payment
        response = self.client.post(
            "/api/payments",
            data=json.dumps({
                "amount": 1000,
                "currency": "USD",
                "payment_token": "tok_test_card_success",
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-reverse",
        )
        processor_ref = response.json().get("processor_reference")

        # Send succeeded first
        payload = make_webhook_payload(processor_ref, status="succeeded")
        body = json.dumps(payload).encode()
        sig = sign_body(body)

        self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )

        # Send pending second (should be ignored)
        payload = make_webhook_payload(processor_ref, status="pending")
        body = json.dumps(payload).encode()
        sig = sign_body(body)

        self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )

        # Verify status is still succeeded
        payment = Payment.objects.get(processor_reference=processor_ref)
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(payment.ledger_entries.count(), 1)

    def test_invalid_signature_rejected(self):
        """Webhook with invalid signature is rejected."""
        # Create payment
        response = self.client.post(
            "/api/payments",
            data=json.dumps({
                "amount": 500,
                "currency": "USD",
                "payment_token": "tok_test_card_success",
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-invalid-sig",
        )
        processor_ref = response.json().get("processor_reference")

        # Send webhook with invalid signature
        payload = make_webhook_payload(processor_ref, status="succeeded")
        body = json.dumps(payload).encode()

        response = self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE="invalid",
        )
        self.assertEqual(response.status_code, 400)

        # Verify payment unchanged
        payment = Payment.objects.get(processor_reference=processor_ref)
        self.assertEqual(payment.status, "pending")

    def test_no_sensitive_data_in_db(self):
        """No full card number or CVV in database after payment flow."""
        # Create payment
        response = self.client.post(
            "/api/payments",
            data=json.dumps({
                "amount": 1000,
                "currency": "USD",
                "payment_token": "tok_test_card_success",
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-no-sensitive",
        )
        payment_id = response.json()["id"]

        # Send webhook
        processor_ref = response.json().get("processor_reference")
        payload = make_webhook_payload(processor_ref, status="succeeded")
        body = json.dumps(payload).encode()
        sig = sign_body(body)

        self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )

        # Check database
        payment = Payment.objects.get(id=payment_id)
        self.assertNotIn("4242424242424242", str(payment.__dict__))
        self.assertNotIn("123", payment.payment_token)


@override_settings(PROCESSOR_WEBHOOK_SECRET=TEST_SECRET)
class MockProcessorIntegrationTest(TestCase):
    """Integration tests using mock_processor models directly."""

    def setUp(self):
        self.client = APIClient()

    def test_charge_creates_processor_reference(self):
        """Charging a token creates a charge with processor reference."""
        # Create payment method
        pm = PaymentMethod.objects.create(
            token="tok_integration_1",
            method="card",
            last4="4242",
            brand_or_bank_type="visa",
        )

        # Create charge
        charge = Charge.objects.create(
            processor_reference="pr_integration_1",
            payment_method=pm,
            amount=5000,
            currency="USD",
        )

        self.assertEqual(charge.status, "pending")
        self.assertEqual(charge.amount, 5000)

    def test_payment_lifecycle_with_mock_processor(self):
        """Full lifecycle: create payment → webhook → verify."""
        # Create payment method
        pm = PaymentMethod.objects.create(
            token="tok_lifecycle_1",
            method="card",
            last4="4242",
            brand_or_bank_type="visa",
        )

        # Create payment via API
        response = self.client.post(
            "/api/payments",
            data=json.dumps({
                "amount": 10000,
                "currency": "USD",
                "payment_token": "tok_lifecycle_1",
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="test-lifecycle",
        )
        self.assertEqual(response.status_code, 201)
        payment_id = response.json()["id"]
        processor_ref = response.json().get("processor_reference")

        # Simulate webhook from processor
        payload = make_webhook_payload(processor_ref, status="succeeded")
        body = json.dumps(payload).encode()
        sig = sign_body(body)

        response = self.client.post(
            "/webhooks/processor",
            data=body,
            content_type="application/json",
            HTTP_X_PROCESSOR_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, 200)

        # Verify
        payment = Payment.objects.get(id=payment_id)
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(payment.ledger_entries.count(), 1)
