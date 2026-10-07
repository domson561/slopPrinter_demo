from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.assets.models import Asset
from apps.catalog.models import ShopSettings
from apps.orders.models import Order, OrderStatus


class Command(BaseCommand):
    help = (
        "Retencja: usuwa pliki po upływie expires_at (30 dni po delivered/cancelled) "
        "oraz porzucone zamówienia draft starsze niż N dni."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        shop = ShopSettings.get()
        now = timezone.now()
        dry = options["dry_run"]

        stale_drafts = Order.objects.filter(
            status=OrderStatus.DRAFT, created_at__lt=now - timedelta(days=shop.draft_retention_days)
        )
        draft_count = stale_drafts.count()
        if not dry:
            stale_drafts.delete()

        expired = Asset.objects.filter(expires_at__lt=now)
        # Nie usuwaj plików zamówień, które wciąż są w toku (bezpiecznik).
        open_statuses = [
            s for s in OrderStatus.values if s not in (OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.DRAFT)
        ]
        expired = expired.exclude(order_items__order__status__in=open_statuses)
        asset_count = expired.count()
        if not dry:
            for asset in expired:
                for v in asset.versions.all():
                    v.file.delete(save=False)
                asset.original_file.delete(save=False)
                asset.delete()

        prefix = "[dry-run] " if dry else ""
        self.stdout.write(f"{prefix}Usunięto porzuconych draftów: {draft_count}, wygasłych plików: {asset_count}")
