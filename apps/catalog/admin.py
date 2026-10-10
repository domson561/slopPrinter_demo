from django.contrib import admin

from .models import DeliveryMethod, ShopSettings


@admin.register(DeliveryMethod)
class DeliveryMethodAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price", "is_active")


@admin.register(ShopSettings)
class ShopSettingsAdmin(admin.ModelAdmin):
    pass
