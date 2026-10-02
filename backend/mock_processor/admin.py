from django.contrib import admin

from .models import Charge, PaymentMethod


class ChargeInline(admin.TabularInline):
    model = Charge
    extra = 0
    readonly_fields = ("id", "processor_reference", "amount", "currency", "status", "failure_code", "created")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("id", "method", "last4", "brand_or_bank_type", "token", "created")
    list_filter = ("method",)
    search_fields = ("token", "last4", "brand_or_bank_type")
    readonly_fields = ("id", "created", "modified")
    inlines = (ChargeInline,)


@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ("id", "processor_reference", "payment_method", "amount", "currency", "status", "created")
    list_filter = ("status", "currency")
    search_fields = ("id", "processor_reference", "payment_method__token")
    readonly_fields = ("id", "created", "modified")
