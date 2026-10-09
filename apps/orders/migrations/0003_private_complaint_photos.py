"""Zdjęcia do reklamacji z publicznego `/media/` do `PRIVATE_MEDIA_ROOT`."""

import shutil
from pathlib import Path

import apps.assets.storage
import apps.orders.models
from django.conf import settings
from django.db import migrations, models


def _move(names, src_root, dst_root):
    """Przenosi istniejące pliki między katalogami; brakujące i już przeniesione pomija."""
    for name in names:
        src, dst = Path(src_root) / name, Path(dst_root) / name
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)


def to_private(apps, schema_editor):
    names = apps.get_model("orders", "ComplaintPhoto").objects.values_list("image", flat=True)
    _move([n for n in names if n], settings.MEDIA_ROOT, settings.PRIVATE_MEDIA_ROOT)


def to_public(apps, schema_editor):
    names = apps.get_model("orders", "ComplaintPhoto").objects.values_list("image", flat=True)
    _move([n for n in names if n], settings.PRIVATE_MEDIA_ROOT, settings.MEDIA_ROOT)


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_orderitem_product_options'),
    ]

    operations = [
        migrations.AlterField(
            model_name='complaintphoto',
            name='image',
            field=models.ImageField(storage=apps.assets.storage.PrivateStorage(), upload_to=apps.orders.models.complaint_photo_upload_to),
        ),
        migrations.RunPython(to_private, to_public),
    ]
