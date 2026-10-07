"""
Generator obrazków demo (§14). Rysowane Pillow, bez pobierania z sieci.
Wzory z gradientem, kształtami, drobnym tekstem i szumem - żeby różnica przed/po upscale była widoczna.
"""

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

IMAGES_DIR = Path(__file__).resolve().parent / "images"

# nazwa -> (szerokość, wysokość, format zapisu)
DEMO_IMAGES = {
    "small_512.png": (362, 512, "PNG"),  # za słaby na A2 nawet po upscale
    "typical_1448.png": (1448, 2048, "PNG"),  # A3 ok, A2 wymaga poprawy
    "large_3000.jpg": (3000, 4243, "JPEG"),  # ostry bez enhancera
    "panorama_2048x768.jpg": (2048, 768, "JPEG"),  # złe proporcje, pokazuje kadrowanie
    "logo_test.png": (1448, 2048, "PNG"),  # wpada w moderację (nazwa zawiera 'logo')
}


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def draw_demo_image(width, height, seed=0, label=""):
    rnd = random.Random(seed)
    img = Image.new("RGB", (width, height))
    px = img.load()
    c1 = (rnd.randint(20, 90), rnd.randint(40, 120), rnd.randint(120, 220))
    c2 = (rnd.randint(200, 255), rnd.randint(120, 200), rnd.randint(40, 120))
    for y in range(height):
        t = y / max(height - 1, 1)
        row = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
        for x in range(width):
            px[x, y] = row
    d = ImageDraw.Draw(img)
    for _ in range(14):
        r = rnd.randint(min(width, height) // 12, min(width, height) // 4)
        x = rnd.randint(-r, width)
        y = rnd.randint(-r, height)
        color = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
        if rnd.random() < 0.5:
            d.ellipse([x, y, x + r, y + r], fill=color, outline=(20, 20, 20), width=max(1, r // 40))
        else:
            d.rectangle([x, y, x + r, y + int(r * 0.7)], fill=color, outline=(20, 20, 20), width=max(1, r // 40))
    # Drobne linie i tekst: dobrze pokazują pikselozę przy powiększeniu.
    step = max(width // 24, 8)
    for x in range(0, width, step):
        d.line([x, height - step * 3, x + step // 2, height], fill=(255, 255, 255), width=1)
    d.text((width // 20, height // 20), label or f"{width}x{height}", fill=(255, 255, 255), font=_font(max(width // 30, 12)))
    d.text((width // 20, height // 20 + max(width // 30, 12) + 4), "demo AI slop", fill=(0, 0, 0), font=_font(max(width // 60, 8)))
    return img


def ensure_demo_images(force=False):
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    created = []
    for i, (name, (w, h, fmt)) in enumerate(DEMO_IMAGES.items()):
        path = IMAGES_DIR / name
        if path.exists() and not force:
            continue
        label = "LOGO ACME (r)" if "logo" in name else name.rsplit(".", 1)[0]
        img = draw_demo_image(w, h, seed=i + 1, label=label)
        if fmt == "JPEG":
            img.save(path, "JPEG", quality=90)
        else:
            img.save(path, "PNG", optimize=True)
        created.append(path)
    return created
