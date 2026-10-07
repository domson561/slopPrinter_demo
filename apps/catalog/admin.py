from django.contrib import admin

from .models import DeliveryMethod, Format, PaperOption, ShopSettings


@admin.register(Format)
class FormatAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "width_mm", "height_mm", "bleed_mm", "base_price", "is_active")
    list_editable = ("base_price", "is_active")


@admin.register(PaperOption)
class PaperOptionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price_delta", "is_active")


@admin.register(DeliveryMethod)
class DeliveryMethodAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price", "is_active")


@admin.register(ShopSettings)
class ShopSettingsAdmin(admin.ModelAdmin):
    pass
