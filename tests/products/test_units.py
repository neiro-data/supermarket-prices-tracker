import pytest

from price_tracker.domain.models import LineType, ParsedLine, Unit
from price_tracker.products.units import PackSize, base_quantity_milli, parse_pack_size


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Trauben 500g", PackSize(500, Unit.KG)),
        ("Milch 1,5l", PackSize(1500, Unit.L)),
        ("Wein 0,75 L", PackSize(750, Unit.L)),
        ("Cola 330ml", PackSize(330, Unit.L)),
        ("Saft 50cl", PackSize(500, Unit.L)),
        ("Wasser 6x0,5l", PackSize(3000, Unit.L)),
        ("Joghurt 4x125g", PackSize(500, Unit.KG)),
        ("Eier 10 Stk", PackSize(10000, Unit.PC)),
        ("Eier 6er", PackSize(6000, Unit.PC)),
        ("Bananen", None),
        ("Milch 3,5%", None),
    ],
)
def test_parse_pack_size(raw: str, expected: PackSize | None) -> None:
    assert parse_pack_size(raw) == expected


def _line(q: int, unit: Unit) -> ParsedLine:
    return ParsedLine(1, "x", LineType.ITEM, 100, quantity_milli=q, quantity_unit=unit)


def test_base_quantity_weighed() -> None:
    assert base_quantity_milli(_line(566, Unit.KG), None, True) == 566


def test_base_quantity_pack_times_quantity() -> None:
    assert base_quantity_milli(_line(2000, Unit.PC), PackSize(500, Unit.KG), False) == 1000


def test_base_quantity_unknown() -> None:
    assert base_quantity_milli(_line(1000, Unit.PC), None, False) is None
