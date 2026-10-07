import pytest

from apps.assets import quality
from apps.assets.quality import Verdict
from apps.catalog.models import Format, ShopSettings


@pytest.fixture
def a2():
    return Format(code="A2", name="A2", width_mm=420, height_mm=594, bleed_mm=3, base_price="89.00")


@pytest.fixture
def shop(db):
    return ShopSettings.get()  # 200 / 120 / x4


def test_default_crop_portrait_image_on_portrait_format(a2):
    crop = quality.default_crop(1000, 2000, a2)
    assert crop["orientation"] == "portrait"
    assert crop["w"] == 1.0
    assert crop["h"] == pytest.approx((1000 / 2000) / (420 / 594), abs=1e-4)
    assert crop["x"] == 0.0


def test_default_crop_landscape_image_rotates_format(a2):
    crop = quality.default_crop(3000, 1000, a2)
    assert crop["orientation"] == "landscape"
    assert crop["h"] == 1.0
    assert crop["w"] < 1.0


def test_effective_dpi_formula_full_frame(a2):
    # 1024 px szerokości na 420 mm -> 61.9 dpi (szerokość ogranicza)
    crop = {"x": 0, "y": 0, "w": 1.0, "h": 1.0, "orientation": "portrait"}
    dpi = quality.effective_dpi(1024, 1448, crop, a2)
    assert dpi == pytest.approx(1024 / (420 / 25.4), rel=1e-3)


def test_zooming_in_lowers_dpi(a2):
    full = quality.default_crop(2000, 2828, a2)
    zoomed = {**full, "w": full["w"] / 2, "h": full["h"] / 2}
    assert quality.effective_dpi(2000, 2828, zoomed, a2) < quality.effective_dpi(2000, 2828, full, a2)


@pytest.mark.parametrize(
    "dpi,expected",
    [
        (250, Verdict.SHARP),
        (200, Verdict.SHARP),
        (150, Verdict.OK),
        (120, Verdict.OK),
        (60, Verdict.NEEDS_ENHANCE),
        (30, Verdict.NEEDS_ENHANCE),
        (29.9, Verdict.TOO_WEAK),
        (5, Verdict.TOO_WEAK),
    ],
)
def test_verdict_thresholds(shop, dpi, expected):
    assert quality.verdict_for_dpi(dpi, shop) == expected


def test_quality_for_demo_images(db, shop):
    """
    Obrazki demo (§14): typical_1448 -> A3 ok, A2 wymaga poprawy;
    small_512 -> A2 za słaby nawet po upscale; large_3000 -> ostry.
    """
    a3 = Format(code="A3", name="A3", width_mm=297, height_mm=420, base_price="59")
    a2 = Format(code="A2", name="A2", width_mm=420, height_mm=594, base_price="89")

    assert quality.quality_for(1448, 2048, a3, shop).verdict == Verdict.OK
    assert quality.quality_for(1448, 2048, a2, shop).verdict == Verdict.NEEDS_ENHANCE
    assert quality.quality_for(362, 512, a2, shop).verdict == Verdict.TOO_WEAK
    assert quality.quality_for(3000, 4243, a3, shop).verdict == Verdict.SHARP
    assert quality.quality_for(3000, 4243, a2, shop).verdict == Verdict.OK


def test_sanitize_crop_rejects_garbage(a2):
    assert quality.sanitize_crop({"x": "abc"}, 1000, 1000, a2) == quality.default_crop(1000, 1000, a2)
    c = quality.sanitize_crop({"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}, 1000, 1000, a2)
    assert c["x"] + c["w"] <= 1.0 and c["y"] + c["h"] <= 1.0
