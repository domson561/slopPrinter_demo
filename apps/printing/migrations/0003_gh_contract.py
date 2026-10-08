"""
Kontrakt v1 z GH: `PrintJob` dostaje numer zamówienia dla GH (klucz idempotencji), status
GH i stany plików; dochodzi kursor pollingu. Mock drukarni (`MockPrinterJob`, webhook)
zastępuje atrapa GH w `apps.gh_mock`.
"""

from django.db import migrations, models


def fill_shop_order_numbers(apps, schema_editor):
    PrintJob = apps.get_model("printing", "PrintJob")
    seen: dict[int, int] = {}
    for job in PrintJob.objects.order_by("created_at", "pk").select_related("order"):
        count = seen.get(job.order_id, 0)
        job.shop_order_number = job.order.number if count == 0 else f"{job.order.number}-D{count}"
        job.save(update_fields=["shop_order_number"])
        seen[job.order_id] = count + 1


class Migration(migrations.Migration):
    dependencies = [
        ("printing", "0002_printjob_order"),
    ]

    operations = [
        migrations.DeleteModel(name="MockPrinterJob"),
        migrations.AddField(
            model_name="printjob",
            name="shop_order_number",
            field=models.CharField(max_length=40, null=True),
        ),
        migrations.RunPython(fill_shop_order_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="printjob",
            name="shop_order_number",
            field=models.CharField(
                help_text="Klucz idempotencji w GH: numer zamówienia sklepu.",
                max_length=40,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="printjob",
            name="provider_status",
            field=models.CharField(blank=True, help_text="Status w GH.", max_length=20),
        ),
        migrations.AddField(
            model_name="printjob",
            name="files_state",
            field=models.JSONField(blank=True, default=list, help_text="Stany plików z GH."),
        ),
        migrations.AddField(
            model_name="printjob",
            name="last_polled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="PollCursor",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("since", models.DateTimeField(blank=True, null=True)),
                ("last_polled_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=300)),
                (
                    "auth_failed",
                    models.BooleanField(
                        default=False,
                        help_text="401 z GH: polling wstrzymany do interwencji operatora.",
                    ),
                ),
            ],
            options={"verbose_name": "Kursor pollingu GH"},
        ),
    ]
