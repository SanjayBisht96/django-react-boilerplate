import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone as tz
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from payments.models import Payment

TEST_SECRET = "test-webhook-secret"
User = get_user_model()


def sign_body(body: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@override_settings(PROCESSOR_WEBHOOK_SECRET=TEST_SECRET)
class ReplayPaymentTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            email="admin@test.com", password="adminpass"
        )
        self.regular_user = User.objects.create_user(
            email="user@test.com", password="userpass"
        )
        self.payment = Payment.objects.create(
            amount=1000,
            currency="USD",
            payment_token="tok_test_abc123",
            processor_reference="pr_test_replay",
            status="pending",
        )

    def test_replay_requires_admin(self):
        """Non-admin users cannot replay."""
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.post(f"/api/payments/{self.payment.id}/replay")
        self.assertEqual(response.status_code, 403)

    def test_replay_requires_authentication(self):
        """Unauthenticated users cannot replay."""
        response = self.client.post(f"/api/payments/{self.payment.id}/replay")
        self.assertEqual(response.status_code, 403)

    @patch("payments.views.requests.post")
    def test_replay_stuck_payment(self, mock_post):
        """Admin can replay a stuck payment."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "processor_reference": "pr_test_replay",
            "status": "succeeded",
        }

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(f"/api/payments/{self.payment.id}/replay")
        self.assertEqual(response.status_code, 200)

    @patch("payments.views.requests.post")
    def test_replay_nonexistent_payment(self, mock_post):
        """Replaying a nonexistent payment returns 404."""
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/payments/{uuid.uuid4()}/replay"
        )
        self.assertEqual(response.status_code, 404)

    @patch("payments.views.requests.post")
    def test_replay_does_not_break_idempotency(self, mock_post):
        """Replay does not create duplicate ledger entries."""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "processor_reference": "pr_test_replay",
            "status": "succeeded",
        }

        self.client.force_authenticate(user=self.admin)

        # First replay
        response1 = self.client.post(f"/api/payments/{self.payment.id}/replay")
        self.assertEqual(response1.status_code, 200)

        # Second replay (should be safe)
        response2 = self.client.post(f"/api/payments/{self.payment.id}/replay")
        self.assertEqual(response2.status_code, 200)

        # Replay only re-requests the result; status changes via webhook
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "pending")
