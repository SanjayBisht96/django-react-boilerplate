from rest_framework import serializers

from .models import LedgerEntry, Payment


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = ["id", "event_id", "status", "failure_code", "occurred_at", "created"]


class PaymentSerializer(serializers.ModelSerializer):
    ledger = LedgerEntrySerializer(many=True, read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "amount",
            "currency",
            "payment_token",
            "status",
            "failure_code",
            "processor_reference",
            "ledger",
            "created",
            "modified",
        ]


class PaymentCreateSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1)
    currency = serializers.CharField(default="USD", max_length=3)
    payment_token = serializers.CharField(max_length=255)
