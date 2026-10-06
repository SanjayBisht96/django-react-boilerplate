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
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

from .models import LedgerEntry, OutboxEvent, Payment
from .serializers import PaymentCreateSerializer, PaymentSerializer
from .state_machine import can_transition, is_terminal
from .throttles import PaymentCreateThrottle

logger = logging.getLogger(__name__)


def _compute_body_hash(body: dict) -> str:
    """Compute SHA256 hash of canonical JSON body."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _redis_client():
    """Return a Redis client for idempotency fast-path, or None if unavailable."""
    url = getattr(settings, "REDIS_URL", "")
    if not url:
        return None
    try:
        import redis

        return redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    except Exception:  # noqa: BLE001 - Redis is an optimization, never fatal
        return None


def _verify_signature(raw_body: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 signature using constant-time comparison."""
    secret = getattr(settings, "PROCESSOR_WEBHOOK_SECRET", "")
    if not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


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
@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def create_payment(request):
    """List payments, or create a payment with idempotency support."""
    if request.method == "GET":
        payments = Payment.objects.all().order_by("-created")
        return Response(PaymentSerializer(payments, many=True).data)

    # Aggressive rate limiting to shed retry storms before they hit the DB
    throttle = PaymentCreateThrottle()
    if not throttle.allow_request(request, create_payment):
        return Response(
            {"error": "Too many requests, retry later"},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

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

    # Redis fast-path: cached result + in-flight marker (idempotency)
    r = _redis_client()
    if r is not None:
        try:
            cached = r.get(f"idem:payment:{idempotency_key}")
            if cached:
                try:
                    payment = Payment.objects.get(id=cached.decode())
                    if payment.idempotency_body_hash == body_hash:
                        return Response(
                            PaymentSerializer(payment).data, status=status.HTTP_200_OK
                        )
                    return Response(
                        {"error": "Idempotency-Key already used with a different request body"},
                        status=status.HTTP_409_CONFLICT,
                    )
                except Payment.DoesNotExist:
                    pass
            if r.set(f"idem:inflight:{idempotency_key}", "1", nx=True, ex=30) is None:
                # Same key is still being processed by another worker
                return Response(
                    {"status": "processing"}, status=status.HTTP_202_ACCEPTED
                )
        except Exception:  # noqa: BLE001 - fall back to DB-backed idempotency
            r = None

    # Check for existing payment with same idempotency key
    existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if r is not None:
            try:
                r.delete(f"idem:inflight:{idempotency_key}")
                r.set(f"idem:payment:{idempotency_key}", str(existing.id), ex=86400)
            except Exception:  # noqa: BLE001
                pass
        if existing.idempotency_body_hash == body_hash:
            # Same key + same body → return original response
            return Response(PaymentSerializer(existing).data, status=status.HTTP_200_OK)
        # Same key + different body → reject
        return Response(
            {"error": "Idempotency-Key already used with a different request body"},
            status=status.HTTP_409_CONFLICT,
        )

    # Create payment in CREATED state; charging happens asynchronously
    with transaction.atomic():
        payment = Payment.objects.create(
            idempotency_key=idempotency_key,
            idempotency_body_hash=body_hash,
            amount=body["amount"],
            currency=body.get("currency", "USD"),
            payment_token=body["payment_token"],
            customer_email=body.get("customer_email") or None,
            status=Payment.Status.CREATED,
            processor_reference=None,
        )

    logger.info("Payment created: id=%s token=%s", payment.id, payment.payment_token)
    LedgerEntry.objects.create(
        payment=payment,
        event_id=f"evt_local_created_{payment.id.hex[:12]}",
        status=Payment.Status.CREATED,
        failure_code=None,
        occurred_at=timezone.now(),
    )

    if r is not None:
        try:
            r.delete(f"idem:inflight:{idempotency_key}")
            
            r.set(f"idem:payment:{idempotency_key}", str(payment.id), ex=86400)
        except Exception:  # noqa: BLE001
            pass

    # Call the mock processor's charge API to create a pending charge.
    # An empty MOCK_PROCESSOR_URL means "in-process" (e.g. tests).
    processor_url = getattr(settings, "MOCK_PROCESSOR_URL", None)
    if processor_url is None:
        processor_url = "http://localhost:8000/processor"

    if processor_url:
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
            # Processor unreachable → payment fails clearly (no silent fallthrough)
            logger.error("Mock processor charge failed for %s: %s", payment.id, e)
            payment.status = Payment.Status.FAILED
            payment.failure_code = "processor_unavailable"
            payment.save(update_fields=["status", "failure_code", "modified"])
            LedgerEntry.objects.create(
                payment=payment,
                event_id=f"evt_local_failed_{payment.id.hex[:12]}",
                status=Payment.Status.FAILED,
                failure_code="processor_unavailable",
                occurred_at=timezone.now(),
            )
            return Response(
                {"error": f"Failed to reach payment processor: {e}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        if response.status_code != 200:
            error = response.json().get("error", "charge_failed")
            payment.status = Payment.Status.FAILED
            payment.failure_code = error
            payment.save(update_fields=["status", "failure_code", "modified"])
            LedgerEntry.objects.create(
                payment=payment,
                event_id=f"evt_local_failed_{payment.id.hex[:12]}",
                status=Payment.Status.FAILED,
                failure_code=error,
                occurred_at=timezone.now(),
            )
            return Response(
                {"error": f"Invalid payment token: {payment.payment_token}"
                 if error == "invalid_token" else error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payment.processor_reference = response.json()["processor_reference"]
        payment.status = Payment.Status.PENDING
        payment.save(update_fields=["processor_reference", "status", "modified"])
        LedgerEntry.objects.create(
            payment=payment,
            event_id=f"evt_local_pending_{payment.id.hex[:12]}",
            status=Payment.Status.PENDING,
            failure_code=None,
            occurred_at=timezone.now(),
        )
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    # In-process charge (tests or mock processor not running)
    from .charging import charge_token

    try:
        payment.processor_reference = charge_token(
            payment.payment_token, payment.amount, payment.currency
        )
    except ValueError:
        payment.status = Payment.Status.FAILED
        payment.failure_code = "invalid_token"
        payment.save(update_fields=["status", "failure_code", "modified"])
        LedgerEntry.objects.create(
            payment=payment,
            event_id=f"evt_local_failed_{payment.id.hex[:12]}",
            status=Payment.Status.FAILED,
            failure_code="invalid_token",
            occurred_at=timezone.now(),
        )
        return Response(
            {"error": f"Invalid payment token: {payment.payment_token}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    payment.status = Payment.Status.PENDING
    payment.save(update_fields=["processor_reference", "status", "modified"])
    LedgerEntry.objects.create(
        payment=payment,
        event_id=f"evt_local_pending_{payment.id.hex[:12]}",
        status=Payment.Status.PENDING,
        failure_code=None,
        occurred_at=timezone.now(),
    )
    return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


@extend_schema(responses=PaymentSerializer)
@api_view(["GET"])
@authentication_classes([])
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


@extend_schema(request=None, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def tokenize(request):
    """Phase 1: forward raw card/bank details to the mock processor, return a token."""
    processor_url = getattr(
        settings, "MOCK_PROCESSOR_URL", "http://localhost:8000/processor"
    )
    try:
        response = requests.post(
            f"{processor_url}/tokenize", json=request.data, timeout=10
        )
    except requests.RequestException as e:
        return Response(
            {"error": f"Failed to reach payment processor: {e}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response(response.json(), status=response.status_code)


@extend_schema(request=None, responses={200: None})
@api_view(["POST"])
@authentication_classes([])
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

        # Transactional outbox: notify merchant asynchronously via CDC → Kafka
        OutboxEvent.objects.create(
            payment=payment,
            event_type=f"payment.{new_status}",
            payload={
                "payment_id": str(payment.id),
                "processor_reference": payment.processor_reference,
                "status": new_status,
                "failure_code": failure_code,
                "event_id": event_id,
                "occurred_at": occurred_dt.isoformat(),
                "customer_email": payment.customer_email,
                "amount": payment.amount,
                "currency": payment.currency,
                "payment_token": payment.payment_token,
            },
        )

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
