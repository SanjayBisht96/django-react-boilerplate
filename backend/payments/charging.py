"""Charge a token via mock_processor (in-process call).

Extracted from views so the celery worker can use it asynchronously.
"""

import random


def charge_token(token: str, amount: int, currency: str) -> str:
    """Create a pending Charge and return the processor_reference."""
    from mock_processor.models import Charge, PaymentMethod

    try:
        payment_method = PaymentMethod.objects.get(token=token)
    except PaymentMethod.DoesNotExist:
        raise ValueError(f"Invalid payment token: {token}")

    processor_reference = f"pr_{random.getrandbits(64):016x}"

    Charge.objects.create(
        processor_reference=processor_reference,
        payment_method=payment_method,
        amount=amount,
        currency=currency,
        status="pending",
    )

    return processor_reference
