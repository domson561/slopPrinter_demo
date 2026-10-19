"""
Składa warianty plakatu w ramie na stronie głównej (tło 4, „Kosmiczny wieczór”).

Wejście: `assets/ramy/*.png` - pełne rendery tej samej sceny co oryginał tła, każdy z innym
plakatem w ramie. Z każdego renderu bierzemy tylko ramę (z miękką krawędzią) i wklejamy ją na
oryginał, żeby pokój był na wszystkich obrazach identyczny. Plik `pusta.png` to pusta rama
i zawsze ląduje na końcu (tekst „Tu Twój obraz” dokłada HTML).

Podpisy, czcionki, prompty i tagi galerii: `assets/ramy/podpisy.txt`
(wiersz `plik | podpis | czcionka | prompt | pasja | styl | okazja`);
kolejność wierszy to kolejność obrazów, pliki spoza listy idą na koniec alfabetycznie.

Wyjście: `static/img/tla/ramy/<nazwa>.webp` (pełny rozmiar) i `<nazwa>-720.webp` (telefon)
oraz `manifest.json` z kolejnością, podpisami, czworokątem ramy i rozmiarem tła.
Do galerii inspiracji: sam plakat wycięty z ramy i wyprostowany (`static/img/galeria/<nazwa>.webp`).

Gdy render dokłada coś do pokoju poza ramą (kot na dywanie, zdjęcie na ścianie), bierzemy
go w całości: pokój i tak jest ten sam, a wklejenie samej ramy zgubiłoby ten dodatek.
Render, którego pokój różni się od oryginału (inna kompozycja, inne światło), jest pomijany
z ostrzeżeniem: wklejona rama nie trafiłaby w ramę na oryginale.

Uruchomienie z katalogu projektu: `.venv\\Scripts\\python scripts\\ramy.py`
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "assets" / "Kosmiczny plakat w nastrojowym wnętrzu.png"
SRC = ROOT / "assets" / "ramy"
CAPTIONS = SRC / "podpisy.txt"
OUT = ROOT / "static" / "img" / "tla" / "ramy"
OUT_POSTERS = ROOT / "static" / "img" / "galeria"
POSTER_SIZE = (600, 900)
FRAME_BORDER = 22  # szerokość listwy ramy w px: o tyle wchodzimy do środka, wycinając sam plakat
EMPTY = "pusta.png"
TLO = 4

# Zewnętrzna krawędź ramy na oryginale (px), od lewego górnego rogu zgodnie z ruchem wskazówek.
FRAME = [(1151, 103), (1507, 68), (1494, 645), (1136, 662)]
MARGIN = 8  # o tyle px poszerzamy czworokąt poza krawędź ramy
FEATHER = 10  # promień rozmycia krawędzi maski
MAX_ROOM_DIFF = 12.0  # średnia różnica pikseli poza ramą, powyżej której render jest pomijany
MIN_EXTRA = 0.5  # % obrazu poza ramą z wyraźną zmianą, od którego bierzemy cały render
WEBP = {"quality": 82, "method": 6}
SMALL_WIDTH = 720


def slug(name):
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "rama"


def expanded(quad, margin):
    cx = sum(x for x, _ in quad) / len(quad)
    cy = sum(y for _, y in quad) / len(quad)
    out = []
    for x, y in quad:
        dx, dy = x - cx, y - cy
        length = (dx * dx + dy * dy) ** 0.5
        out.append((x + dx / length * margin, y + dy / length * margin))
    return out


def captions():
    """Wiersze podpisy.txt: {plik: (podpis, czcionka, prompt, pasja, styl, okazja)}."""
    result = {}
    if not CAPTIONS.exists():
        return result
    for line in CAPTIONS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        parts += [""] * (7 - len(parts))
        result[parts[0]] = tuple(parts[1:7])
    return result


def frame_mask(size):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(expanded(FRAME, MARGIN), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(FEATHER))


def room_diff(image, base, mask):
    """Średnia różnica pikseli poza ramą: czy render ma ten sam pokój co oryginał."""
    diff = ImageChops.difference(image, base).convert("L")
    outside = ImageChops.multiply(diff, ImageChops.invert(mask))
    return ImageStat.Stat(outside).mean[0]


def extra_outside(image, base, mask):
    """Procent obrazu poza ramą, gdzie render wyraźnie różni się od oryginału (zwarte plamy)."""
    diff = ImageChops.difference(image, base).convert("L")
    outside = ImageChops.multiply(diff, ImageChops.invert(mask))
    blobs = outside.filter(ImageFilter.GaussianBlur(4)).point(lambda v: 255 if v > 50 else 0)
    return ImageStat.Stat(blobs).mean[0] / 255 * 100


def tags(text):
    return [t.strip() for t in text.split(",") if t.strip()]


def poster_crop(image):
    """Sam plakat z ramy, wyprostowany do pionowego prostokąta (perspektywa z czworokąta ramy)."""
    tl, tr, br, bl = expanded(FRAME, -FRAME_BORDER)
    quad = tl + bl + br + tr  # PIL: lewy górny, lewy dolny, prawy dolny, prawy górny
    return image.transform(POSTER_SIZE, Image.QUAD, quad, resample=Image.BICUBIC)


def save(image, stem):
    image.save(OUT / f"{stem}.webp", **WEBP)
    small = image.resize(
        (SMALL_WIDTH, round(image.height * SMALL_WIDTH / image.width)), Image.LANCZOS
    )
    small.save(OUT / f"{stem}-{SMALL_WIDTH}.webp", **WEBP)


def main():
    base = Image.open(BASE).convert("RGB")
    mask = frame_mask(base.size)
    OUT.mkdir(parents=True, exist_ok=True)
    OUT_POSTERS.mkdir(parents=True, exist_ok=True)
    listed = captions()
    order = {name: i for i, name in enumerate(listed)}
    sources = sorted(
        SRC.glob("*.png"), key=lambda p: (order.get(p.name, len(order)), p.name == EMPTY, p.name)
    )
    if not sources:
        sys.exit(f"Brak plików PNG w {SRC}")
    for name in listed:
        if not (SRC / name).exists():
            print(f"UWAGA: {CAPTIONS.name} wymienia {name}, ale pliku nie ma")

    entries = []
    for path in sources:
        image = Image.open(path).convert("RGB")
        if image.size != base.size:
            if abs(image.width - base.width) > 4 or abs(image.height - base.height) > 4:
                print(f"POMIJAM {path.name}: rozmiar {image.size}, oryginał ma {base.size}")
                continue
            image = image.resize(base.size, Image.LANCZOS)  # generator czasem gubi piksel na brzegu
        diff = room_diff(image, base, mask)
        if diff > MAX_ROOM_DIFF:
            print(
                f"POMIJAM {path.name}: pokój różni się od oryginału (różnica {diff:.1f} > {MAX_ROOM_DIFF})"
            )
            continue
        stem = "pusta" if path.name == EMPTY else slug(path.stem)
        extra = extra_outside(image, base, mask)
        whole = extra >= MIN_EXTRA
        save(image if whole else Image.composite(image, base, mask), stem)
        caption, font, prompt, pasja, styl, okazja = listed.get(path.name, ("",) * 6)
        if prompt and path.name != EMPTY:
            poster_crop(image).save(OUT_POSTERS / f"{stem}.webp", **WEBP)
        entries.append(
            {
                "file": stem,
                "empty": path.name == EMPTY,
                "caption": caption,
                "font": font.split(":")[0],  # nazwa rodziny do CSS
                "font_spec": font,  # do adresu Google Fonts (z wagą)
                "prompt": prompt,
                "pasja": tags(pasja),
                "styl": tags(styl),
                "okazja": tags(okazja),
            }
        )
        how = f"cały render, {extra:.1f}% zmian poza ramą" if whole else "sama rama"
        print(f"{path.name} -> {stem}.webp ({how}, różnica pokoju {diff:.1f})")

    manifest = {
        "tlo": TLO,
        "size": list(base.size),
        "frame": [list(point) for point in FRAME],
        "ramy": entries,
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"manifest.json: {len(entries)} obrazów")


if __name__ == "__main__":
    main()
