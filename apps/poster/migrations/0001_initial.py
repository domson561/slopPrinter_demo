"""Formaty, papiery i progi DPI przechodzą z `catalog` do `poster`. Dane kopiowane 1:1 (te same pk)."""

from django.db import migrations, models


def copy_from_catalog(apps, schema_editor):
    pairs = [
        (apps.get_model("catalog", "Format"), apps.get_model("poster", "Format")),
        (apps.get_model("catalog", "PaperOption"), apps.get_model("poster", "PaperOption")),
    ]
    for old_model, new_model in pairs:
        fields = [f.name for f in old_model._meta.concrete_fields]
        for row in old_model.objects.all():
            new_model.objects.create(**{name: getattr(row, name) for name in fields})
    shop = apps.get_model("catalog", "ShopSettings").objects.filter(pk=1).first()
    if shop is not None:
        apps.get_model("poster", "PosterSettings").objects.create(
            pk=1,
            dpi_sharp=shop.dpi_sharp,
            dpi_ok=shop.dpi_ok,
            upscale_factor=shop.upscale_factor,
        )


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("catalog", "0002_remove_printer_auto_simulate"),
    ]

    operations = [
        migrations.CreateModel(
            name="Format",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("code", models.CharField(max_length=20, unique=True)),
                ("name", models.CharField(max_length=50)),
                ("width_mm", models.PositiveIntegerField()),
                ("height_mm", models.PositiveIntegerField()),
                ("bleed_mm", models.PositiveIntegerField(default=3)),
                ("base_price", models.DecimalField(decimal_places=2, max_digits=8)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
            ],
            options={"ordering": ["sort_order", "code"]},
        ),
        migrations.CreateModel(
            name="PaperOption",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("code", models.CharField(max_length=20, unique=True)),
                ("name", models.CharField(max_length=50)),
                ("price_delta", models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
            ],
            options={"ordering": ["sort_order", "code"]},
        ),
        migrations.CreateModel(
            name="PosterSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "dpi_sharp",
                    models.PositiveIntegerField(default=200, help_text="Próg 'ostry' (DPI)."),
                ),
                ("dpi_ok", models.PositiveIntegerField(default=120, help_text="Próg 'ok' (DPI).")),
                (
                    "upscale_factor",
                    models.PositiveIntegerField(
                        default=4,
                        help_text="Mnożnik enhancera; 'wymaga poprawy' gdy dpi * mnożnik >= próg 'ok'.",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ustawienia plakatu",
                "verbose_name_plural": "Ustawienia plakatu",
            },
        ),
        migrations.RunPython(copy_from_catalog, migrations.RunPython.noop),
    ]
