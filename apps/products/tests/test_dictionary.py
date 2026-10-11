"""Słownik product_code: kompletność wpisów, stawki GH, pokrycie plakatu, produkt bierze stąd
kod, nazwę i stawkę."""

import json
from decimal import Decimal

import pytest

from apps.assets.services import create_asset_from_upload
from apps.orders import services as order_services
from apps.poster.models import Format, PaperOption
from apps.poster.options import poster_options
from apps.poster.product import dictionary_code
from apps.products import dictionary
from apps.products.dictionary import UnknownProductCode


def test_dictionary_has_version_and_complete_entries():
    assert dictionary.version() == "1.1"
    entries = dictionary.all_entries()
    assert entries
    for code, entry in entries.items():
        assert entry.code == code and code == code.lower()
        assert entry.name and entry.format and entry.paper and entry.finish
        assert isinstance(entry.print_rate, Decimal) and entry.print_rate > 0
        assert entry.files and all({"role", "description"} <= set(f) for f in entry.files)


@pytest.mark.parametrize("rate", [32, "0.00", "-5.00", "12.345", "abc", None])
def test_rate_must_be_positive_amount_as_text(tmp_path, monkeypatch, rate):
    data = json.loads(dictionary.PATH.read_text(encoding="utf-8"))
    data["products"]["poster-a2-matte"]["print_rate"] = rate
    path = tmp_path / "slownik.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(dictionary, "PATH", path)
    dictionary._load.cache_clear()
    try:
        with pytest.raises(ValueError, match="print_rate"):
            dictionary.all_entries()
    finally:
        dictionary._load.cache_clear()


def test_unknown_code_is_rejected_by_lookup():
    assert not dictionary.known("mug-xl")
    with pytest.raises(UnknownProductCode):
        dictionary.get("mug-xl")


@pytest.mark.django_db
def test_every_poster_format_and_paper_has_an_entry(catalog_data):
    for fmt in Format.objects.filter(is_active=True):
        for paper in PaperOption.objects.filter(is_active=True):
            code = dictionary_code(fmt, paper)
            assert dictionary.known(code), code


@pytest.mark.django_db
def test_poster_line_for_gh_comes_from_dictionary(catalog_data, image_upload):
    asset = create_asset_from_upload(image_upload(size=(1448, 2048)))
    fmt, paper = Format.objects.get(code="A2"), PaperOption.objects.get(code="satin")
    order = order_services.save_draft(
        "poster", asset, poster_options(fmt, paper, white_margin=True), quantity=1
    ).order
    item = order.item
    line = item.definition.gh_line(item)
    entry = dictionary.get("poster-a2-satin")
    assert (line.product_code, line.name, line.print_rate) == (
        entry.code,
        entry.name,
        entry.print_rate,
    )
    assert item.print_rate == entry.print_rate != item.unit_price
    assert item.summary == f"{entry.name}, biały margines"

    # Bez papieru w opcjach: pierwszy aktywny, żeby kod dla GH zawsze był w słowniku.
    order = order_services.save_draft("poster", asset, poster_options(fmt), quantity=1).order
    assert order.item.options["paper"] == "matte"
