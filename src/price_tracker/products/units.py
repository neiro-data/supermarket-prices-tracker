"""Pack-size parsing and conversion of quantities to the canonical base unit (milli-units)."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from price_tracker.domain.models import ParsedLine, Unit

_NUM = r"\d+(?:[.,]\d+)?"
_UNIT = r"(?:kg|ml|cl|g|l)"
_START = r"(?<![\w.,])"
_END = r"(?![a-z0-9])"

_MULTI = re.compile(rf"{_START}(\d+)\s*[x×]\s*({_NUM})\s*({_UNIT}){_END}", re.IGNORECASE)
_SINGLE = re.compile(rf"{_START}({_NUM})\s*({_UNIT}){_END}", re.IGNORECASE)
_COUNT = re.compile(rf"{_START}(\d+)\s*(?:stk|st|stück|stueck){_END}", re.IGNORECASE)
_ER = re.compile(rf"{_START}(\d+)er{_END}", re.IGNORECASE)

# (milli factor, unit): g and ml are already milli-kg / milli-l.
_FACTORS: dict[str, tuple[int, Unit]] = {
    "kg": (1000, Unit.KG),
    "g": (1, Unit.KG),
    "l": (1000, Unit.L),
    "ml": (1, Unit.L),
    "cl": (10, Unit.L),
}


@dataclass(frozen=True, slots=True)
class PackSize:
    amount_milli: int
    unit: Unit


def _milli(amount: str, unit: str, count: int = 1) -> PackSize:
    factor, base = _FACTORS[unit.lower()]
    value = Decimal(amount.replace(",", ".")) * factor * count
    return PackSize(int(value.to_integral_value(ROUND_HALF_UP)), base)


def parse_pack_size(raw: str) -> PackSize | None:
    """Parse `500g`, `1,5l`, `6x0,5l`, `10 Stk`, `6er` ... into base-unit milli-amounts."""
    if m := _MULTI.search(raw):
        return _milli(m.group(2), m.group(3), int(m.group(1)))
    if m := _SINGLE.search(raw):
        return _milli(m.group(1), m.group(2))
    for pattern in (_COUNT, _ER):
        if m := pattern.search(raw):
            return PackSize(int(m.group(1)) * 1000, Unit.PC)
    return None


def strip_pack_tokens(text: str) -> str:
    """Remove pack-size tokens from a name (case-insensitive)."""
    for pattern in (_MULTI, _SINGLE, _COUNT, _ER):
        text = pattern.sub(" ", text)
    return text


def _base(quantity_milli: int, quantity_unit: str, pack: PackSize | None) -> PackSize | None:
    if quantity_unit in (Unit.KG, Unit.L):  # weighed / measured: the quantity is the amount
        return PackSize(quantity_milli, Unit(quantity_unit))
    if pack is not None:
        return PackSize((quantity_milli * pack.amount_milli + 500) // 1000, pack.unit)
    return None


def base_quantity_for(
    quantity_milli: int, quantity_unit: str, pack: PackSize | None, base_unit: str | None = None
) -> int | None:
    """Amount in the base unit; plain pieces count as `pc` when the base unit is `pc`."""
    res = _base(quantity_milli, quantity_unit, pack)
    if res is None:
        return quantity_milli if quantity_unit == Unit.PC and base_unit == Unit.PC else None
    if base_unit is not None and res.unit != base_unit:
        return None  # e.g. a kg/l pack under a `pc` canonical: not comparable
    return res.amount_milli


def base_quantity_milli(line: ParsedLine, pack: PackSize | None, is_weighed: bool) -> int | None:
    """Base-unit amount of a parsed line (weighed: quantity; packs: quantity x pack size)."""
    if is_weighed and line.quantity_unit is Unit.PC:
        return None
    return base_quantity_for(line.quantity_milli, line.quantity_unit.value, pack)
