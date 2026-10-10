from django.contrib import admin

from .models import Format, PaperOption, PosterSettings


@admin.register(Format)
class FormatAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "width_mm", "height_mm", "bleed_mm", "base_price", "is_active")
    list_editable = ("base_price", "is_active")


@admin.register(PaperOption)
class PaperOptionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price_delta", "is_active")


@admin.register(PosterSettings)
class PosterSettingsAdmin(admin.ModelAdmin):
    pass
