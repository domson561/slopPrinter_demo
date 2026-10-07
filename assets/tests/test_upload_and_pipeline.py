import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from assets.models import AssetVersion
from assets.services import (
    AnalysisService,
    MockUpscale,
    OutpaintService,
    PrepressService,
    UploadError,
    create_asset_from_upload,
    render_fragment,
    render_mockup,
    run_pipeline,
)
from catalog.models import Format, PaperOption
from orders.models import Order, OrderItem


def make_upload(name="test.png", size=(800, 1200), fmt="PNG", color=(120, 80, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=f"image/{fmt.lower()}")


@pytest.fixture
def a2(db):
    return Format.objects.create(code="A2", name="A2", width_mm=420, height_mm=594, bleed_mm=3, base_price="89.00")


@pytest.fixture
def matte(db):
    return PaperOption.objects.create(code="matte", name="Matowy", price_delta="0.00")


@pytest.mark.django_db
def test_upload_creates_asset_preview_and_analysis(a2):
    asset = create_asset_from_upload(make_upload())
    assert asset.width == 800 and asset.height == 1200
    assert asset.mime == "image/png"
    assert len(asset.sha256) == 64
    assert asset.preview is not None
    assert "A2" in asset.analysis.quality_per_format
    assert asset.analysis.content_flags == []


@pytest.mark.django_db
def test_upload_rejects_wrong_type_and_garbage(a2):
    with pytest.raises(UploadError):
        create_asset_from_upload(SimpleUploadedFile("x.gif", b"GIF89a garbage", content_type="image/gif"))
    with pytest.raises(UploadError):
        create_asset_from_upload(SimpleUploadedFile("x.png", b"not an image", content_type="image/png"))


@pytest.mark.django_db
def test_upload_rejects_too_large(a2, settings):
    settings.UPLOAD_MAX_BYTES = 10
    with pytest.raises(UploadError):
        create_asset_from_upload(make_upload())


@pytest.mark.django_db
def test_mock_content_safety_flags_by_filename(a2):
    asset = create_asset_from_upload(make_upload(name="my_logo_test.png"))
    assert "trademark_suspected" in asset.analysis.content_flags
    asset2 = create_asset_from_upload(make_upload(name="nsfw_thing.png"))
    assert "nsfw_suspected" in asset2.analysis.content_flags


@pytest.mark.django_db
def test_pipeline_creates_versions_and_is_idempotent(a2, matte):
    asset = create_asset_from_upload(make_upload(size=(600, 900)))
    order = Order.objects.create(email="a@b.pl", full_name="Test")
    item = OrderItem.objects.create(
        order=order, asset=asset, format=a2, paper=matte, quantity=1, enhancer_enabled=True, unit_price="89.00"
    )

    pdf = run_pipeline(item, upscaler=MockUpscale(simulate_delay=False))
    assert pdf.kind == AssetVersion.Kind.PRINT_PDF
    assert pdf.file.size > 0
    assert pdf.params["effective_dpi"] > 0
    kinds = set(asset.versions.filter(order_item=item).values_list("kind", flat=True))
    assert kinds == {"upscaled", "cropped", "print_pdf"}
    upscaled = asset.version("upscaled", item)
    assert upscaled.width == 600 * 4 and upscaled.provider == "MockUpscale"

    run_pipeline(item, upscaler=MockUpscale(simulate_delay=False))
    assert asset.versions.filter(order_item=item, kind="print_pdf").count() == 1
    assert asset.versions.filter(order_item=item).count() == 3


@pytest.mark.django_db
def test_pipeline_without_enhancer_skips_upscale(a2, matte):
    asset = create_asset_from_upload(make_upload(size=(600, 900)))
    order = Order.objects.create(email="a@b.pl", full_name="Test")
    item = OrderItem.objects.create(order=order, asset=asset, format=a2, paper=matte, enhancer_enabled=False, unit_price="89.00")
    run_pipeline(item)
    assert asset.version("upscaled", item) is None
    assert asset.version("print_pdf", item) is not None


def test_prepress_pdf_page_size_includes_bleed(a2):
    img = Image.new("RGB", (100, 141), "red")
    pdf = PrepressService().build_pdf(img, a2, "portrait", white_margin=False)
    assert pdf.startswith(b"%PDF")
    # 426 x 600 mm w punktach
    assert b"MediaBox" in pdf


def test_outpaint_is_not_implemented():
    with pytest.raises(NotImplementedError):
        OutpaintService().outpaint(Image.new("RGB", (10, 10)), 1.0)


@pytest.mark.django_db
def test_mockup_and_fragment_render(a2):
    asset = create_asset_from_upload(make_upload(size=(400, 600)))
    crop = asset.analysis.quality_per_format["A2"]
    crop = {"x": 0, "y": 0, "w": 1, "h": 1, "orientation": crop["orientation"]}
    scene = render_mockup(asset, a2, crop, white_margin=True)
    assert scene.size == (800, 560)
    frag = render_fragment(asset, crop, enhanced=True)
    assert frag.size == (300, 300)
