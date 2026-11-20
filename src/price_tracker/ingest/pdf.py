"""PDF text-layer extraction and page rendering via pypdfium2."""

import re
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

_PRICE_RE = re.compile(r"\d+,\d{2}")


def extract_text_layer(path: Path) -> list[str]:
    """Return the text layer of each page."""
    doc = pdfium.PdfDocument(path)
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    finally:
        doc.close()


def render_pages(path: Path, scale: float = 300 / 72) -> list[Image.Image]:
    """Render each page to a PIL image (default 300 dpi)."""
    doc = pdfium.PdfDocument(path)
    try:
        return [doc[i].render(scale=scale).to_pil() for i in range(len(doc))]
    finally:
        doc.close()


def has_usable_text(pages: list[str]) -> bool:
    """True if the text looks like a receipt: >= 1 price-like token and >= 5 lines."""
    text = "\n".join(pages)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines) >= 5 and _PRICE_RE.search(text) is not None
