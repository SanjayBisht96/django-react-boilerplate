import hashlib
import hmac
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def sign_payload(raw_body: bytes, secret: str) -> str:
    """Sign a webhook payload with HMAC-SHA256."""
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verify a webhook signature using constant-time comparison."""
    expected = sign_payload(raw_body, secret)
    return hmac.compare_digest(expected, signature)


def deliver_webhook(url: str, payload: dict, secret: str) -> bool:
    """Deliver a webhook to the given URL with HMAC signature."""
    raw_body = json.dumps(payload).encode()
    signature = sign_payload(raw_body, secret)

    try:
        response = requests.post(
            url,
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Processor-Signature": signature,
            },
            timeout=10,
        )
        return response.status_code == 200
    except requests.RequestException as e:
        logger.warning("Webhook delivery failed: %s", e)
        return False
