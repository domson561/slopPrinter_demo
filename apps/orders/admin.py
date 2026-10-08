from django.contrib import admin

from .models import Complaint, ComplaintPhoto, Order, OrderItem, OrderStatusEvent, Payment, Shipment


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


class OrderStatusEventInline(admin.TabularInline):
    model = OrderStatusEvent
    extra = 0
    readonly_fields = (
        "from_status",
        "to_status",
        "actor",
        "actor_user",
        "reason",
        "forced",
        "created_at",
    )
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("number", "status", "email", "total", "created_at")
    list_filter = ("status",)
    search_fields = ("number", "email", "full_name")
    readonly_fields = ("number", "status_token", "created_at", "updated_at")
    inlines = [OrderItemInline, OrderStatusEventInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("external_id", "order", "provider", "amount", "status", "created_at")


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("order", "carrier", "tracking_number", "shipped_at")


class ComplaintPhotoInline(admin.TabularInline):
    model = ComplaintPhoto
    extra = 0


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = ("order", "status", "created_at", "resolved_at")
    inlines = [ComplaintPhotoInline]
