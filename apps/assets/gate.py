"""
Bramka bezpieczeństwa plików od kupujących. Każdy plik z zewnątrz (wgranie do produktu,
zdjęcie do reklamacji) przechodzi tędy, zanim trafi na dysk. Bramka pilnuje bezpieczeństwa,
nie przydatności do druku: tę ocenia produkt.

- rozmiar w limicie, typ rozpoznany po zawartości, z listy sklepu (produkt może ją zawęzić);
- obraz: limit pikseli przed dekodowaniem, minimalny rozmiar, zapis na nowo (znikają metadane,
  np. GPS, i dane doklejone za obrazem); zostaje profil kolorów;
- PDF: daje się otworzyć, bez szyfrowania, JavaScriptu, uruchamiania programów, wysyłania
  formularzy, załączników, multimediów i XFA, z limitem stron;
- nazwę pliku na dysku nadaje serwer, nazwa od kupującego zostaje tylko jako opis.

Poza bramką, przy wdrożeniu: limit liczby wgrań i skan antywirusowy.
"""

import io
import os
from dataclasses import dataclass

from django.conf import settings
from PIL import Image, ImageOps

PDF_MIME = "application/pdf"

# Format Pillow -> (MIME, rozszerzenie na dysku).
_IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
IMAGE_MIME = tuple(mime for mime, _ in _IMAGE_FORMATS.values())
_LABELS = {"image/jpeg": "JPG", "image/png": "PNG", "image/webp": "WebP", PDF_MIME: "PDF"}
# Rozszerzenie od kupującego służy tylko do wyboru komunikatu, nigdy do rozpoznania typu.
_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": PDF_MIME,
}
_BROKEN_IMAGE = "Nie udało się otworzyć obrazu. Plik może być uszkodzony."
_BROKEN_PDF = "Nie udało się otworzyć PDF-a. Plik może być uszkodzony."
MIN_IMAGE_PX = 64


class UploadError(Exception):
    """Plik odrzucony przez bramkę. Komunikat po polsku, do pokazania kupującemu."""


@dataclass(frozen=True)
class CleanFile:
    """Plik po bramce: bajty do zapisania (obraz zapisany na nowo, PDF bez zmian)."""

    data: bytes
    mime: str
    ext: str
    width: int = 0
    height: int = 0
    pages: int = 0
    image: Image.Image | None = None


def check_upload(uploaded_file, allowed_mime=None, max_bytes=None) -> CleanFile:
    allowed = set(settings.UPLOAD_ALLOWED_MIME)
    if allowed_mime is not None:
        allowed &= set(allowed_mime)
    limit = min(max_bytes or settings.UPLOAD_MAX_BYTES, settings.UPLOAD_MAX_BYTES)
    too_large = UploadError(f"Plik jest za duży. Maksymalny rozmiar to {_mb(limit)} MB.")
    if uploaded_file.size > limit:
        raise too_large
    data = uploaded_file.read(limit + 1)
    if len(data) > limit:
        raise too_large

    mime = sniff_mime(data)
    if mime is None:
        claimed = _MIME_BY_EXT.get(os.path.splitext(getattr(uploaded_file, "name", ""))[1].lower())
        if claimed in allowed:
            raise UploadError(_BROKEN_PDF if claimed == PDF_MIME else _BROKEN_IMAGE)
    if mime not in allowed:
        raise UploadError(f"Obsługujemy tylko pliki {_names(allowed)}.")
    if mime == PDF_MIME:
        return _check_pdf(data)
    return _check_image(data, mime)


def sniff_mime(data: bytes) -> str | None:
    """Typ z pierwszych bajtów pliku. Rozszerzenie i nagłówek od przeglądarki nie mają znaczenia."""
    if data.startswith(b"%PDF-"):
        return PDF_MIME
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _mb(n: int) -> int:
    return max(n // (1024 * 1024), 1)


def _names(mimes) -> str:
    names = [label for mime, label in _LABELS.items() if mime in mimes]
    return " i ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " i " + names[-1]


# ---------------------------------------------------------------------------
# Obrazy
# ---------------------------------------------------------------------------


def _check_image(data: bytes, mime: str) -> CleanFile:
    broken = UploadError(_BROKEN_IMAGE)
    bomb = UploadError("Obraz ma za dużo pikseli, żebyśmy mogli go bezpiecznie otworzyć.")
    fmt = _format_of(mime)
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise broken from exc
    if img.format != fmt:
        raise broken
    if img.width * img.height > settings.PIL_MAX_IMAGE_PIXELS:
        raise bomb  # przed dekodowaniem: rozmiar z nagłówka
    try:
        img.load()
    except Image.DecompressionBombError as exc:
        raise bomb from exc
    except Exception as exc:
        raise broken from exc

    icc_profile = img.info.get("icc_profile")
    img = ImageOps.exif_transpose(img)
    if img.width < MIN_IMAGE_PX or img.height < MIN_IMAGE_PX:
        raise UploadError(f"Obraz jest za mały (minimum {MIN_IMAGE_PX} x {MIN_IMAGE_PX} px).")
    return CleanFile(
        data=_reencode(img, mime, icc_profile),
        mime=mime,
        ext=_IMAGE_FORMATS[fmt][1],
        width=img.width,
        height=img.height,
        image=img,
    )


def _format_of(mime: str) -> str:
    return next(fmt for fmt, (m, _) in _IMAGE_FORMATS.items() if m == mime)


def _reencode(img: Image.Image, mime: str, icc_profile) -> bytes:
    """Zapis od nowa z samych pikseli: bez EXIF/XMP i bez danych doklejonych za obrazem."""
    buf = io.BytesIO()
    extra = {"icc_profile": icc_profile} if icc_profile else {}
    if mime == "image/jpeg":
        if img.mode not in ("RGB", "L", "CMYK"):
            img = img.convert("RGB")
        img.save(buf, "JPEG", quality=95, subsampling=0, **extra)
    elif mime == "image/png":
        img.save(buf, "PNG", **extra)
    else:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.save(buf, "WEBP", quality=95, **extra)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

# Klucze i typy obiektów, które w pliku do druku oznaczają treść aktywną.
_PDF_ACTIVE_KEYS = {
    "/JS": "JavaScript",
    "/JavaScript": "JavaScript",
    "/EmbeddedFiles": "załączniki",
    "/XFA": "formularz XFA",
    "/RichMediaContent": "multimedia",
}
_PDF_ACTIVE_TYPES = {
    "/JavaScript": "JavaScript",
    "/Launch": "uruchamianie programów",
    "/SubmitForm": "wysyłanie formularza",
    "/ImportData": "import danych",
    "/FileAttachment": "załączniki",
    "/EmbeddedFile": "załączniki",
    "/RichMedia": "multimedia",
    "/Movie": "multimedia",
    "/Sound": "multimedia",
}
_PDF_MAX_OBJECTS = 200_000


def _check_pdf(data: bytes) -> CleanFile:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise UploadError(
                "PDF jest zaszyfrowany albo chroniony hasłem. Wgraj plik bez zabezpieczeń."
            )
        pages = len(reader.pages)
        active = _pdf_active_content(reader)
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError(_BROKEN_PDF) from exc
    if pages < 1:
        raise UploadError("PDF nie ma żadnej strony.")
    if pages > settings.UPLOAD_MAX_PDF_PAGES:
        raise UploadError(f"PDF ma za dużo stron (maksymalnie {settings.UPLOAD_MAX_PDF_PAGES}).")
    if active:
        raise UploadError(
            f"PDF zawiera elementy aktywne ({', '.join(active)}). "
            "Wgraj spłaszczony plik do druku, np. PDF/X."
        )
    return CleanFile(data=data, mime=PDF_MIME, ext=".pdf", pages=pages)


def _pdf_active_content(reader) -> list[str]:
    """Przejście po wszystkich obiektach osiągalnych z katalogu dokumentu, bez dekodowania strumieni."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    found: set[str] = set()
    stack = [reader.trailer]
    seen: set[tuple[int, int]] = set()
    visited = 0
    while stack:
        obj = stack.pop()
        if isinstance(obj, IndirectObject):
            ref = (obj.idnum, obj.generation)
            if ref in seen:
                continue
            seen.add(ref)
            obj = obj.get_object()
        visited += 1
        if visited > _PDF_MAX_OBJECTS:
            raise UploadError("PDF jest zbyt złożony, żebyśmy mogli go bezpiecznie sprawdzić.")
        if isinstance(obj, DictionaryObject):
            for key, value in obj.items():
                if key in _PDF_ACTIVE_KEYS:
                    found.add(_PDF_ACTIVE_KEYS[key])
                if key in ("/S", "/Subtype", "/Type"):
                    name = str(value.get_object() if isinstance(value, IndirectObject) else value)
                    if name in _PDF_ACTIVE_TYPES:
                        found.add(_PDF_ACTIVE_TYPES[name])
                if isinstance(value, IndirectObject | DictionaryObject | ArrayObject):
                    stack.append(value)
        elif isinstance(obj, ArrayObject):
            stack.extend(
                v for v in obj if isinstance(v, IndirectObject | DictionaryObject | ArrayObject)
            )
    return sorted(found)
