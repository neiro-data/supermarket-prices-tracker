"""OCR engines."""

from dataclasses import dataclass
from typing import Protocol

import pytesseract
from PIL import Image


@dataclass(frozen=True, slots=True)
class OcrResult:
    """OCR text and mean word confidence (0-100)."""

    text: str
    mean_confidence: float


class OcrEngine(Protocol):
    """Anything that can turn an image into text."""

    def image_to_text(self, img: Image.Image) -> OcrResult: ...


class TesseractEngine:
    """Tesseract via pytesseract (German + English)."""

    def __init__(self, tesseract_cmd: str | None = None) -> None:
        self._checked = False
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def check_available(self) -> None:
        """Raise a clear error if the tesseract binary or language data is missing."""
        try:
            langs = set(pytesseract.get_languages(config=""))
        except pytesseract.TesseractNotFoundError as e:
            raise RuntimeError(
                "Tesseract not found. Install it: brew install tesseract tesseract-lang"
            ) from e
        missing = {"deu", "eng"} - langs
        if missing:
            raise RuntimeError(
                f"Tesseract language data missing: {sorted(missing)}. "
                "Install: brew install tesseract-lang"
            )

    def image_to_text(self, img: Image.Image) -> OcrResult:
        if not self._checked:
            self.check_available()
            self._checked = True
        cfg = "--oem 1 --psm 6"
        text: str = pytesseract.image_to_string(img, lang="deu+eng", config=cfg)
        data = pytesseract.image_to_data(
            img, lang="deu+eng", config=cfg, output_type=pytesseract.Output.DICT
        )
        confs = [
            float(c)
            for c, t in zip(data["conf"], data["text"], strict=True)
            if str(t).strip() and float(c) >= 0
        ]
        mean = sum(confs) / len(confs) if confs else 0.0
        return OcrResult(text=text, mean_confidence=mean)
