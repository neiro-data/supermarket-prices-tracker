import pytest

from price_tracker.products.normalize import normalize_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TAFELTR.HELL KERNL", "tafeltrauben hell kernlos"),
        ("Trauben hell kernlos 500g", "trauben hell kernlos"),
        ("H-MILCH 3,5% 1L", "haltbare milch 3.5%"),
        ("Vollm. Käse", "vollmilch kaese"),
        ("Weißbrot  Bio", "weissbrot bio"),
        ("Müsli 6x0,5l", "muesli"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_idempotent() -> None:
    once = normalize_name("H-MILCH 3,5% 1L")
    assert normalize_name(once) == once
