import django.utils.timezone
from django.db import migrations, models

import apps.assets.storage
import apps.orders.models


def mark_existing_shipped(apps, schema_editor):
    """Dotychczasowe przesyłki powstawały dopiero przy statusie „wysłane” z GH."""
    Shipment = apps.get_model("orders", "Shipment")
    for shipment in Shipment.objects.all():
        shipment.status = "shipped"
        shipment.created_at = shipment.shipped_at or shipment.created_at
        shipment.save(update_fields=["status", "created_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0003_private_complaint_photos"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="shipment",
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.AddField(
            model_name="shipment",
            name="status",
            field=models.CharField(
                choices=[
                    ("created", "Etykieta gotowa"),
                    ("shipped", "Nadana"),
                    ("cancelled", "Anulowana"),
                ],
                default="created",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="shipment",
            name="provider",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="shipment",
            name="external_id",
            field=models.CharField(
                blank=True, help_text="Identyfikator przesyłki u przewoźnika.", max_length=100
            ),
        ),
        migrations.AddField(
            model_name="shipment",
            name="label",
            field=models.FileField(
                blank=True,
                storage=apps.assets.storage.private_storage,
                upload_to=apps.orders.models.label_upload_to,
            ),
        ),
        migrations.AddField(
            model_name="shipment",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="shipment",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="shipment",
            name="shipped_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_shipped, migrations.RunPython.noop),
    ]
