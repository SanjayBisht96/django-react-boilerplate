import hashlib
import hmac
import json
import logging
from datetime import datetime

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

from .models import LedgerEntry, Payment
from .serializers import PaymentCreateSerializer, PaymentSerializer
from .state_machine import can_transition, is_terminal

logger = logging.getLogger(__name__)


def _compute_body_hash(body: dict) -> str:
    """Compute SHA256 hash of canonical JSON body."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _verify_signature(raw_body: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 signature using constant-time comparison."""
    secret = getattr(settings, "PROCESSOR_WEBHOOK_SECRET", "")
    if not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _charge_token(token: str, amount: int, currency: str) -> str:
    """Charge a token via mock_processor (in-process call).

    Returns the processor_reference. Raises ValueError on failure.
    """
    from mock_processor.models import Charge, PaymentMethod

    try:
        payment_method = PaymentMethod.objects.get(token=token)
    except PaymentMethod.DoesNotExist:
        raise ValueError(f"Invalid payment token: {token}")

    import random

    processor_reference = f"pr_{random.getrandbits(64):016x}"

    Charge.objects.create(
        processor_reference=processor_reference,
        payment_method=payment_method,
        amount=amount,
        currency=currency,
        status="pending",
    )

    return processor_reference


@extend_schema(
    request=PaymentCreateSerializer,
    responses=PaymentSerializer,
    parameters=[
        OpenApiParameter(
            name="Idempotency-Key",
            location=OpenApiParameter.HEADER,
            type=OpenApiTypes.STR,
            required=True,
            description="Unique key to safely retry this request without creating duplicate payments.",
        )
    ],
)
@api_view(["POST"])
@permission_classes([AllowAny])
def create_payment(request):
    """Create a payment with idempotency support."""
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return Response(
            {"error": "Idempotency-Key header is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = PaymentCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    body = serializer.validated_data
    body_hash = _compute_body_hash(body)

    # Check for existing payment with same idempotency key
    existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if existing.idempotency_body_hash == body_hash:
            # Same key + same body → return original response
            return Response(PaymentSerializer(existing).data, status=status.HTTP_200_OK)
        # Same key + different body → reject
        return Response(
            {"error": "Idempotency-Key already used with a different request body"},
            status=status.HTTP_409_CONFLICT,
        )

    # Charge the token via mock_processor (in-process call)
    processor_reference = _charge_token(
        token=body["payment_token"],
        amount=body["amount"],
        currency=body.get("currency", "USD"),
    )

    # Create payment
    with transaction.atomic():
        payment = Payment.objects.create(
            idempotency_key=idempotency_key,
            idempotency_body_hash=body_hash,
            amount=body["amount"],
            currency=body.get("currency", "USD"),
            payment_token=body["payment_token"],
            status=Payment.Status.PENDING,
            processor_reference=processor_reference,
        )

    logger.info(
        "Payment created: id=%s token=%s reference=%s",
        payment.id,
        payment.payment_token,
        processor_reference,
    )

    return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


@extend_schema(responses=PaymentSerializer)
@api_view(["GET"])
@permission_classes([AllowAny])
def get_payment(request, payment_id):
    """Get payment status and ledger history."""
    try:
        payment = Payment.objects.get(id=payment_id)
    except Payment.DoesNotExist:
        return Response(
            {"error": "Payment not found"}, status=status.HTTP_404_NOT_FOUND
        )

    return Response(PaymentSerializer(payment).data)


@extend_schema(request=None, responses={200: None})
@api_view(["POST"])
@permission_classes([AllowAny])
def processor_webhook(request):
    """Receive and process processor webhooks."""
    # Verify signature
    signature = request.headers.get("X-Processor-Signature")
    if not signature:
        return Response(
            {"error": "Missing X-Processor-Signature header"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    raw_body = request.body
    if not _verify_signature(raw_body, signature):
        return Response(
            {"error": "Invalid signature"}, status=status.HTTP_400_BAD_REQUEST
        )

    # Parse payload
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return Response(
            {"error": "Invalid JSON body"}, status=status.HTTP_400_BAD_REQUEST
        )

    event_id = payload.get("event_id")
    processor_reference = payload.get("processor_reference")
    new_status = payload.get("status")
    failure_code = payload.get("failure_code")
    occurred_at = payload.get("occurred_at")

    if not all([event_id, processor_reference, new_status]):
        return Response(
            {"error": "Missing required fields"}, status=status.HTTP_400_BAD_REQUEST
        )

    # Find payment by processor_reference
    try:
        payment = Payment.objects.get(processor_reference=processor_reference)
    except Payment.DoesNotExist:
        return Response(
            {"error": "Payment not found"}, status=status.HTTP_404_NOT_FOUND
        )

    # Process webhook with row-level locking
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(id=payment.id)

        # Check for duplicate event
        if LedgerEntry.objects.filter(payment=payment, event_id=event_id).exists():
            return Response({"status": "already_processed"}, status=status.HTTP_200_OK)

        # Validate state transition
        if is_terminal(payment.status):
            # Terminal state: ignore late events
            return Response({"status": "ignored_terminal"}, status=status.HTTP_200_OK)

        if not can_transition(payment.status, new_status):
            return Response(
                {"error": f"Invalid transition from {payment.status} to {new_status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Write ledger entry
        try:
            occurred_dt = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            occurred_dt = timezone.now()

        LedgerEntry.objects.create(
            payment=payment,
            event_id=event_id,
            status=new_status,
            failure_code=failure_code,
            occurred_at=occurred_dt,
        )

        # Update payment status
        payment.status = new_status
        payment.failure_code = failure_code
        payment.save()

    logger.info(
        "Webhook processed: event_id=%s payment=%s status=%s",
        event_id,
        payment.id,
        new_status,
    )

    return Response({"status": "processed"}, status=status.HTTP_200_OK)


@extend_schema(request=None, responses=PaymentSerializer)
@api_view(["POST"])
@permission_classes([IsAdminUser])
def replay_payment(request, payment_id):
    """Admin-only: re-request a stuck payment's result from the processor."""
    try:
        payment = Payment.objects.get(id=payment_id)
    except Payment.DoesNotExist:
        return Response(
            {"error": "Payment not found"}, status=status.HTTP_404_NOT_FOUND
        )

    if not payment.processor_reference:
        return Response(
            {"error": "Payment has no processor reference"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Call mock_processor to re-request the result
    processor_url = getattr(
        settings, "MOCK_PROCESSOR_URL", "http://localhost:8000/processor"
    )
    try:
        response = requests.post(
            f"{processor_url}/charge",
            json={
                "token": payment.payment_token,
                "amount": payment.amount,
                "currency": payment.currency,
            },
            timeout=10,
        )
    except requests.RequestException as e:
        logger.warning("Replay failed for payment %s: %s", payment.id, e)
        return Response(
            {"error": "Failed to contact processor"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if response.status_code != 200:
        return Response(
            {"error": "Processor returned an error"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    result = response.json()
    new_reference = result.get("processor_reference")

    if new_reference and new_reference != payment.processor_reference:
        payment.processor_reference = new_reference
        payment.save()

    logger.info(
        "Payment replayed: id=%s new_reference=%s",
        payment.id,
        new_reference,
    )

    return Response(PaymentSerializer(payment).data)
