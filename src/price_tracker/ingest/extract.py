"""Extract raw text from a receipt file (PDF or image)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pillow_heif
from PIL import Image, ImageOps

from price_tracker.ingest.ocr import OcrEngine
from price_tracker.ingest.pdf import extract_text_layer, has_usable_text, render_pages
from price_tracker.ingest.preprocess import preprocess

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic"})
PDF_EXTS = frozenset({".pdf"})
SUPPORTED_EXTS = IMAGE_EXTS | PDF_EXTS

pillow_heif.register_heif_opener()


@dataclass(frozen=True, slots=True)
class Extraction:
    """Raw text plus how it was obtained."""

    raw_text: str
    method: Literal["text_layer", "ocr"]
    confidence: float | None
    page_count: int
    media_type: Literal["pdf", "image"]


def _ocr_images(images: list[Image.Image], engine: OcrEngine) -> tuple[str, float]:
    texts: list[str] = []
    confs: list[float] = []
    for img in images:
        res = engine.image_to_text(preprocess(img))
        texts.append(res.text)
        confs.append(res.mean_confidence)
    return "\n".join(texts), (sum(confs) / len(confs) if confs else 0.0)


def extract(path: Path, engine: OcrEngine) -> Extraction:
    """Text layer first for PDFs, else render + preprocess + OCR; images go straight to OCR."""
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        pages = extract_text_layer(path)
        if has_usable_text(pages):
            return Extraction("\n".join(pages), "text_layer", None, len(pages), "pdf")
        images = render_pages(path)
        text, conf = _ocr_images(images, engine)
        return Extraction(text, "ocr", conf, len(images), "pdf")
    if ext in IMAGE_EXTS:
        with Image.open(path) as raw:
            img = ImageOps.exif_transpose(raw).convert("RGB")
        text, conf = _ocr_images([img], engine)
        return Extraction(text, "ocr", conf, 1, "image")
    raise ValueError(f"unsupported file type: {path.suffix!r}")
