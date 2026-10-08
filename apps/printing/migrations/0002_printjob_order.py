"""
PrintJob z pozycji (OrderItem) na zamówienie (Order): koszyk = jedno zlecenie w drukarni,
pozycje listą w `spec["items"]` (kontrakt v1). Istniejące zlecenia dostają zamówienie
swojej pozycji; stary kształt `spec` zostaje, demo przebudowuje go przez `seed_demo`.
"""

import django.db.models.deletion
from django.db import migrations, models


def copy_order_from_item(apps, schema_editor):
    PrintJob = apps.get_model("printing", "PrintJob")
    for job in PrintJob.objects.filter(order__isnull=True).select_related("order_item"):
        job.order_id = job.order_item.order_id
        job.save(update_fields=["order"])


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0001_initial"),
        ("printing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="printjob",
            name="order",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="print_jobs",
                to="orders.order",
            ),
        ),
        migrations.RunPython(copy_order_from_item, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="printjob",
            name="order",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="print_jobs",
                to="orders.order",
            ),
        ),
        migrations.RemoveField(model_name="printjob", name="order_item"),
        migrations.RemoveField(model_name="printjob", name="print_file"),
    ]
