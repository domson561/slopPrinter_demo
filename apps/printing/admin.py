from django.contrib import admin

from .models import MockPrinterJob, PrintJob, PrintJobEvent


class PrintJobEventInline(admin.TabularInline):
    model = PrintJobEvent
    extra = 0
    readonly_fields = ("status", "payload", "applied", "note", "received_at")


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order_item",
        "external_id",
        "status",
        "attempts",
        "sent_at",
        "reprint_of",
    )
    list_filter = ("status",)
    inlines = [PrintJobEventInline]


@admin.register(MockPrinterJob)
class MockPrinterJobAdmin(admin.ModelAdmin):
    list_display = ("external_id", "status", "tracking_number", "created_at")
