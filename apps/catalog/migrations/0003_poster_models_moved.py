"""Formaty, papiery i progi DPI są już w `poster` (skopiowane w poster.0001); tu znikają z rdzenia."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_remove_printer_auto_simulate"),
        ("poster", "0001_initial"),
        ("orders", "0002_orderitem_product_options"),
    ]

    operations = [
        migrations.RemoveField(model_name="shopsettings", name="dpi_sharp"),
        migrations.RemoveField(model_name="shopsettings", name="dpi_ok"),
        migrations.RemoveField(model_name="shopsettings", name="upscale_factor"),
        migrations.DeleteModel(name="Format"),
        migrations.DeleteModel(name="PaperOption"),
    ]
