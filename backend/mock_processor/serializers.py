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


class ChargeListSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    processor_reference = serializers.CharField()
    amount = serializers.IntegerField()
    currency = serializers.CharField()
    status = serializers.CharField()
    failure_code = serializers.CharField(allow_null=True)
    method = serializers.SerializerMethodField()
    last4 = serializers.SerializerMethodField()
    brand_or_bank_type = serializers.SerializerMethodField()
    created = serializers.DateTimeField()
    modified = serializers.DateTimeField()

    def get_method(self, obj):
        return obj.payment_method.method

    def get_last4(self, obj):
        return obj.payment_method.last4

    def get_brand_or_bank_type(self, obj):
        return obj.payment_method.brand_or_bank_type
