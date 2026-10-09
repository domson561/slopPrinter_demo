"""Oryginały plików od kupujących z publicznego `/media/` do `PRIVATE_MEDIA_ROOT`."""

import shutil
from pathlib import Path

import apps.assets.models
import apps.assets.storage
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
    names = apps.get_model("assets", "Asset").objects.values_list("original_file", flat=True)
    _move([n for n in names if n], settings.MEDIA_ROOT, settings.PRIVATE_MEDIA_ROOT)


def to_public(apps, schema_editor):
    names = apps.get_model("assets", "Asset").objects.values_list("original_file", flat=True)
    _move([n for n in names if n], settings.PRIVATE_MEDIA_ROOT, settings.MEDIA_ROOT)


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0002_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='asset',
            name='original_file',
            field=models.FileField(storage=apps.assets.storage.PrivateStorage(), upload_to=apps.assets.models.original_upload_to),
        ),
        migrations.RunPython(to_private, to_public),
    ]
