from rest_framework import serializers


class TokenizeSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=["card", "bank"])

    # Card fields
    card_number = serializers.CharField(required=False, allow_blank=True)
    expiry = serializers.CharField(required=False, allow_blank=True)
    cvv = serializers.CharField(required=False, allow_blank=True)

    # Bank fields
    account_number = serializers.CharField(required=False, allow_blank=True)
    routing_number = serializers.CharField(required=False, allow_blank=True)


class ChargeSerializer(serializers.Serializer):
    token = serializers.CharField()
    amount = serializers.IntegerField(min_value=1)
    currency = serializers.CharField(default="USD")
