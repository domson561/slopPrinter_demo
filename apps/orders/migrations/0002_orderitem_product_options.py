"""
Pozycja zamówienia bez pól plakatu: `format`, `paper`, `white_margin`, `crop` i
`enhancer_enabled` przechodzą do `options` (JSON), a `product` wskazuje produkt w rejestrze.
Istniejące pozycje to plakaty z demo.
"""

from django.db import migrations, models


def poster_fields_to_options(apps, schema_editor):
    OrderItem = apps.get_model("orders", "OrderItem")
    for item in OrderItem.objects.select_related("format", "paper"):
        item.product = "poster"
        item.options = {
            "format": item.format.code,
            "paper": item.paper.code if item.paper_id else None,
            "white_margin": item.white_margin,
            "crop": item.crop or {},
            "enhancer": item.enhancer_enabled,
        }
        item.save(update_fields=["product", "options"])


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="product",
            field=models.CharField(
                default="poster", help_text="Kod produktu w rejestrze.", max_length=40
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="orderitem",
            name="options",
            field=models.JSONField(blank=True, default=dict, help_text="Opcje pozycji produktu."),
        ),
        migrations.RunPython(poster_fields_to_options, migrations.RunPython.noop),
        migrations.RemoveField(model_name="orderitem", name="format"),
        migrations.RemoveField(model_name="orderitem", name="paper"),
        migrations.RemoveField(model_name="orderitem", name="white_margin"),
        migrations.RemoveField(model_name="orderitem", name="crop"),
        migrations.RemoveField(model_name="orderitem", name="enhancer_enabled"),
        migrations.AlterField(
            model_name="orderitem",
            name="asset",
            field=models.ForeignKey(
                help_text="Plik od kupującego.",
                on_delete=models.deletion.PROTECT,
                related_name="order_items",
                to="assets.asset",
            ),
        ),
    ]
