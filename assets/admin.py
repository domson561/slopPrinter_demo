from django.contrib import admin

from .models import Asset, AssetAnalysis, AssetVersion


class AssetVersionInline(admin.TabularInline):
    model = AssetVersion
    extra = 0
    readonly_fields = ("kind", "file", "width", "height", "provider", "params", "created_at")


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("id", "original_filename", "mime", "width", "height", "created_at", "expires_at")
    readonly_fields = ("sha256", "created_at")
    inlines = [AssetVersionInline]


@admin.register(AssetAnalysis)
class AssetAnalysisAdmin(admin.ModelAdmin):
    list_display = ("asset", "aspect_ratio", "content_flags", "analyzed_at")


@admin.register(AssetVersion)
class AssetVersionAdmin(admin.ModelAdmin):
    list_display = ("asset", "kind", "provider", "width", "height", "created_at")
    list_filter = ("kind", "provider")
