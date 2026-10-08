from django.contrib import admin

from .models import GhOrder, GhOrderFile, GhOrderItem, GhShop


class GhOrderItemInline(admin.TabularInline):
    model = GhOrderItem
    extra = 0


class GhOrderFileInline(admin.TabularInline):
    model = GhOrderFile
    extra = 0
    readonly_fields = ("state", "error", "downloaded_at", "changed_at")


@admin.register(GhShop)
class GhShopAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at")
    exclude = ("token_hash",)


@admin.register(GhOrder)
class GhOrderAdmin(admin.ModelAdmin):
    list_display = (
        "gh_number",
        "shop",
        "shop_order_number",
        "status",
        "needs_review",
        "changed_at",
    )
    list_filter = ("status", "shop")
    inlines = [GhOrderItemInline, GhOrderFileInline]
