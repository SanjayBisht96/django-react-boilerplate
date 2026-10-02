from django.contrib import admin

from .models import LedgerEntry, Payment


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    readonly_fields = ("id", "event_id", "status", "failure_code", "occurred_at", "created")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "amount", "currency", "status", "failure_code", "processor_reference", "created")
    list_filter = ("status", "currency")
    search_fields = ("id", "payment_token", "processor_reference", "idempotency_key")
    readonly_fields = ("id", "created", "modified")
    inlines = (LedgerEntryInline,)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "payment", "event_id", "status", "failure_code", "occurred_at", "created")
    list_filter = ("status",)
    search_fields = ("id", "event_id", "payment__id")
    readonly_fields = ("id", "created")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
