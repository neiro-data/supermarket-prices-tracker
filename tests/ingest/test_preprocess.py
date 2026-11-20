import numpy as np
from PIL import Image, ImageDraw

from price_tracker.ingest.preprocess import binarize, estimate_skew, preprocess, upscale_if_small


def _bar(angle: float) -> Image.Image:
    img = Image.new("L", (600, 600), 255)
    bar = Image.new("L", (400, 20), 0)
    rotated = bar.rotate(angle, expand=True, fillcolor=255)
    img.paste(rotated, (100, 250))
    return img


def test_estimate_skew_recovers_rotation() -> None:
    gray = np.asarray(_bar(7.0), dtype=np.uint8)
    # PIL rotates counter-clockwise; the estimate must be within 1 degree in magnitude.
    assert abs(abs(estimate_skew(gray)) - 7.0) < 1.0


def test_estimate_skew_blank_is_zero() -> None:
    assert estimate_skew(np.full((100, 100), 255, dtype=np.uint8)) == 0.0


def test_binarize_only_black_and_white() -> None:
    img = Image.new("L", (100, 100), 200)
    ImageDraw.Draw(img).rectangle((20, 20, 60, 60), fill=30)
    out = binarize(np.asarray(img, dtype=np.uint8))
    assert set(np.unique(out)) <= {0, 255}


def test_upscale_if_small() -> None:
    small = np.zeros((500, 300), dtype=np.uint8)
    assert upscale_if_small(small).shape[0] == 2000
    big = np.zeros((2500, 300), dtype=np.uint8)
    assert upscale_if_small(big).shape == big.shape


def test_preprocess_returns_binary_image() -> None:
    out = preprocess(_bar(3.0).convert("RGB"))
    assert set(np.unique(np.asarray(out))) <= {0, 255}
