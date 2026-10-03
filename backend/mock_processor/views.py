import logging
import random
from datetime import datetime, timezone

from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from .luhn import luhn_check
from .models import Charge, PaymentMethod
from .serializers import ChargeListSerializer, ChargeSerializer, TokenizeSerializer

logger = logging.getLogger(__name__)


def _detect_card_brand(number: str) -> str:
    if number.startswith("4"):
        return "visa"
    if number.startswith(("51", "52", "53", "54", "55")):
        return "mastercard"
    if number.startswith(("34", "37")):
        return "amex"
    if number.startswith("6"):
        return "discover"
    return "unknown"


def _detect_outcome(last4: str, method: str) -> tuple:
    """Determine the outcome based on the last 4 digits.

    Returns (status, failure_code, delay_seconds).
    """
    if last4 == "0002":
        if method == "card":
            return "failed", "card_declined", 0
        return "failed", "insufficient_funds", 0
    if last4 == "0119":
        return "failed", "processor_error", 0
    if last4 == "0341":
        return "succeeded", None, 15
    return "succeeded", None, 0


@extend_schema(request=TokenizeSerializer, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def tokenize(request):
    """Tokenize card or bank details.

    Accepts card details (number, expiry, CVV) or bank details
    (account_number, routing_number) and returns a token.
    """
    serializer = TokenizeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    method = serializer.validated_data["method"]

    if method == "card":
        return _tokenize_card(serializer.validated_data)
    return _tokenize_bank(serializer.validated_data)


def _tokenize_card(data: dict) -> Response:
    card_number = data.get("card_number", "").replace(" ", "")
    expiry = data.get("expiry", "")
    cvv = data.get("cvv", "")

    if not card_number or not luhn_check(card_number):
        return Response(
            {"error": "invalid_number"}, status=status.HTTP_400_BAD_REQUEST
        )

    try:
        exp_month, exp_year = expiry.split("/")
        exp_month = int(exp_month)
        exp_year = int(exp_year)
        if exp_year < 100:
            exp_year += 2000
        expiry_date = datetime(exp_year, exp_month, 1).replace(tzinfo=timezone.utc)
        # Last day of the expiry month
        if exp_month == 12:
            last_day = datetime(exp_year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            last_day = datetime(exp_year, exp_month + 1, 1, tzinfo=timezone.utc)
        from datetime import timedelta

        last_day = last_day - timedelta(days=1)
    except (ValueError, IndexError):
        return Response(
            {"error": "invalid_expiry"}, status=status.HTTP_400_BAD_REQUEST
        )

    if last_day < datetime.now(timezone.utc):
        return Response(
            {"error": "invalid_expiry"}, status=status.HTTP_400_BAD_REQUEST
        )

    if not cvv.isdigit() or len(cvv) not in (3, 4):
        return Response(
            {"error": "invalid_cvv"}, status=status.HTTP_400_BAD_REQUEST
        )

    last4 = card_number[-4:]
    brand = _detect_card_brand(card_number)
    token = f"tok_{random.getrandbits(64):016x}"

    payment_method = PaymentMethod.objects.create(
        token=token,
        method="card",
        last4=last4,
        brand_or_bank_type=brand,
    )

    logger.info("Card tokenized: token=%s last4=%s", token, last4)

    return Response(
        {
            "token": payment_method.token,
            "method": "card",
            "last4": payment_method.last4,
            "brand_or_bank_type": brand,
        }
    )


def _tokenize_bank(data: dict) -> Response:
    account_number = data.get("account_number", "").replace(" ", "")
    routing_number = data.get("routing_number", "").replace(" ", "")

    if not account_number.isdigit() or not 8 <= len(account_number) <= 17:
        return Response(
            {"error": "invalid_account_number"}, status=status.HTTP_400_BAD_REQUEST
        )

    if not routing_number.isdigit() or len(routing_number) != 9:
        return Response(
            {"error": "invalid_routing_number"}, status=status.HTTP_400_BAD_REQUEST
        )

    last4 = account_number[-4:]
    token = f"tok_{random.getrandbits(64):016x}"

    payment_method = PaymentMethod.objects.create(
        token=token,
        method="bank",
        last4=last4,
        brand_or_bank_type="checking",
    )

    logger.info("Bank account tokenized: token=%s last4=%s", token, last4)

    return Response(
        {
            "token": payment_method.token,
            "method": "bank",
            "last4": payment_method.last4,
            "brand_or_bank_type": "checking",
        }
    )


@extend_schema(responses=ChargeListSerializer(many=True))
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def charges(request):
    """List all charge requests received by the mock processor."""
    qs = Charge.objects.select_related("payment_method").order_by("-created")
    return Response(ChargeListSerializer(qs, many=True).data)


@extend_schema(request=ChargeSerializer, responses={200: OpenApiTypes.OBJECT})
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def charge(request):
    """Charge a tokenized payment method.

    Returns a processor_reference. The final result arrives via webhook.
    """
    serializer = ChargeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    token = serializer.validated_data["token"]
    amount = serializer.validated_data["amount"]
    currency = serializer.validated_data.get("currency", "USD")

    try:
        payment_method = PaymentMethod.objects.get(token=token)
    except PaymentMethod.DoesNotExist:
        return Response(
            {"error": "invalid_token"}, status=status.HTTP_400_BAD_REQUEST
        )

    processor_reference = f"pr_{random.getrandbits(64):016x}"

    charge_obj = Charge.objects.create(
        processor_reference=processor_reference,
        payment_method=payment_method,
        amount=amount,
        currency=currency,
        status="pending",
    )

    logger.info(
        "Charge created: reference=%s token=%s amount=%s",
        processor_reference,
        token,
        amount,
    )

    return Response(
        {
            "processor_reference": charge_obj.processor_reference,
            "status": "pending",
        }
    )
