from django.contrib import admin

from .models import PollCursor, PrintJob, PrintJobEvent


class PrintJobEventInline(admin.TabularInline):
    model = PrintJobEvent
    extra = 0
    readonly_fields = ("status", "payload", "applied", "note", "received_at")


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = (
        "shop_order_number",
        "order",
        "external_id",
        "status",
        "provider_status",
        "attempts",
        "sent_at",
        "reprint_of",
    )
    list_filter = ("status", "provider_status")
    inlines = [PrintJobEventInline]


@admin.register(PollCursor)
class PollCursorAdmin(admin.ModelAdmin):
    list_display = ("since", "last_polled_at", "auth_failed", "last_error")
