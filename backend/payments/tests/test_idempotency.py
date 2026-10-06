import hashlib
import json

from django.test import TestCase
from rest_framework.test import APIClient

from payments.models import Payment
from mock_processor.models import PaymentMethod


class IdempotencyCreateTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/payments"
        self.key = "test-key-123"
        self.body = {
            "amount": 1000,
            "currency": "USD",
            "payment_token": "tok_test_abc123",
        }
        PaymentMethod.objects.create(
            token="tok_test_abc123",
            method="card",
            last4="4242",
            brand_or_bank_type="visa",
        )

    def _body_hash(self, body):
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def test_create_payment_success(self):
        """POST /api/payments creates a payment and returns 201."""
        response = self.client.post(
            self.url,
            data=json.dumps(self.body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=self.key,
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("id", data)
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["amount"], 1000)
        self.assertEqual(data["currency"], "USD")
        self.assertEqual(data["payment_token"], "tok_test_abc123")

    def test_idempotent_create_same_key_same_body(self):
        """Same key + same body returns the original response, creates nothing new."""
        response1 = self.client.post(
            self.url,
            data=json.dumps(self.body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=self.key,
        )
        self.assertEqual(response1.status_code, 201)
        payment_id_1 = response1.json()["id"]

        response2 = self.client.post(
            self.url,
            data=json.dumps(self.body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=self.key,
        )
        self.assertEqual(response2.status_code, 200)
        payment_id_2 = response2.json()["id"]

        self.assertEqual(payment_id_1, payment_id_2)
        self.assertEqual(Payment.objects.count(), 1)

    def test_idempotent_create_same_key_different_body_rejected(self):
        """Same key + different body is rejected with 409."""
        response1 = self.client.post(
            self.url,
            data=json.dumps(self.body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=self.key,
        )
        self.assertEqual(response1.status_code, 201)

        different_body = {
            "amount": 2000,
            "currency": "USD",
            "payment_token": "tok_test_abc123",
        }
        response2 = self.client.post(
            self.url,
            data=json.dumps(different_body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=self.key,
        )
        self.assertEqual(response2.status_code, 409)
        self.assertIn("error", response2.json())
        self.assertEqual(Payment.objects.count(), 1)

    def test_missing_idempotency_key_rejected(self):
        """POST without Idempotency-Key header is rejected."""
        response = self.client.post(
            self.url,
            data=json.dumps(self.body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_get_payment_returns_status_and_ledger(self):
        """GET /api/payments/{id} returns current status and ledger history."""
        create_response = self.client.post(
            self.url,
            data=json.dumps(self.body),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=self.key,
        )
        payment_id = create_response.json()["id"]

        response = self.client.get(f"/api/payments/{payment_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], payment_id)
        self.assertEqual(data["status"], "pending")
        self.assertEqual(
            [entry["status"] for entry in data["ledger"]], ["created", "pending"]
        )

    def test_get_payment_not_found(self):
        """GET /api/payments/{id} with invalid ID returns 404."""
        response = self.client.get("/api/payments/00000000-0000-0000-0000-000000000000")
        self.assertEqual(response.status_code, 404)
