"""Image preprocessing for OCR: grayscale, deskew, adaptive threshold, upscale."""

from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

_MAX_SKEW_DEG = 15.0

Gray = NDArray[np.uint8]  # cv2 stubs return wider dtypes; casts below keep mypy strict


def to_grayscale(img: Image.Image) -> Gray:
    """Convert a PIL image to a 2-D uint8 grayscale array."""
    return np.asarray(img.convert("L"), dtype=np.uint8)


def estimate_skew(gray: Gray) -> float:
    """Estimate skew angle in degrees (clamped to +-15) from dark foreground pixels."""
    _, fg = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(fg)
    if coords is None or len(coords) < 10:
        return 0.0
    angle = float(cv2.minAreaRect(coords)[-1])
    # Normalise OpenCV's angle convention to (-45, 45].
    if angle > 45:
        angle -= 90
    elif angle < -45:
        angle += 90
    return max(-_MAX_SKEW_DEG, min(_MAX_SKEW_DEG, angle))


def deskew(gray: Gray) -> Gray:
    """Rotate the image to remove skew (white fill)."""
    angle = estimate_skew(gray)
    if abs(angle) < 0.1:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(
        gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255
    )
    return cast(Gray, out)


def binarize(gray: Gray) -> Gray:
    """Adaptive Gaussian threshold; output contains only 0 and 255."""
    out = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return cast(Gray, out)


def upscale_if_small(gray: Gray, min_height: int = 2000) -> Gray:
    """Upscale (cubic) so the image height is at least `min_height`."""
    h, w = gray.shape
    if h >= min_height:
        return gray
    f = min_height / h
    out = cv2.resize(gray, (round(w * f), min_height), interpolation=cv2.INTER_CUBIC)
    return cast(Gray, out)


def preprocess(img: Image.Image) -> Image.Image:
    """Full pipeline: grayscale -> upscale -> deskew -> binarize."""
    gray = upscale_if_small(to_grayscale(img))
    return Image.fromarray(binarize(deskew(gray)))
