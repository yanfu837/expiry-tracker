from django.contrib import admin
from .models import DocumentDeletion, ExpiryItem, ReminderDelivery

admin.site.register(ExpiryItem)
# Register your models here.


@admin.register(DocumentDeletion)
class DocumentDeletionAdmin(admin.ModelAdmin):
    list_display = ("id", "attempts", "retry_at", "last_error")
    readonly_fields = ("backend", "name", "delivery_type", "attempts", "retry_at", "lease_until", "lease_token", "last_error", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReminderDelivery)
class ReminderDeliveryAdmin(admin.ModelAdmin):
    list_display = ("id", "item_id", "expiry_date", "days_before", "sent_at", "attempts", "last_error")
    readonly_fields = ("item", "expiry_date", "days_before", "sent_at", "attempts", "retry_at", "lease_until", "lease_token", "last_error")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
