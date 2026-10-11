import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0004_shipment_label"),
        ("printing", "0003_gh_contract"),
    ]

    operations = [
        migrations.AddField(
            model_name="printjob",
            name="shipment",
            field=models.ForeignKey(
                blank=True,
                help_text="Przesyłka, której etykieta poszła w zleceniu (`spec['label']`).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="print_jobs",
                to="orders.shipment",
            ),
        ),
    ]
