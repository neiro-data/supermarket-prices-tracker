import pytest
from PIL import Image, ImageDraw, ImageFont

from price_tracker.ingest.ocr import TesseractEngine

pytestmark = pytest.mark.tesseract


def test_ocr_reads_rendered_text() -> None:
    img = Image.new("L", (600, 150), 255)
    font = ImageFont.load_default(size=60)
    ImageDraw.Draw(img).text((20, 30), "REWE 1,49", fill=0, font=font)
    res = TesseractEngine().image_to_text(img)
    assert "REWE" in res.text
    assert "1,49" in res.text
