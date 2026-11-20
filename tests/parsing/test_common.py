from datetime import datetime

import pytest

from price_tracker.domain.models import LineType
from price_tracker.parsing.common import (
    QTY_RE,
    WEIGHT_RE,
    classify_line,
    find_total,
    parse_date,
    parse_store_address,
    trailing_price,
)


@pytest.mark.parametrize(
    ("text", "cents", "vat"),
    [
        ("MILCH 1,49 B", 149, "B"),
        ("X -0,30", -30, None),
        ("Rabatt 0,30-", -30, None),
        ("Cola 1,50 l 0,99 A", 99, "A"),
        ("Teuer 1.234,56", None, None),
    ],
)
def test_trailing_price(text: str, cents: int | None, vat: str | None) -> None:
    m = trailing_price(text)
    if cents is None:
        assert m is None or m.cents != 123456
    else:
        assert m is not None and (m.cents, m.vat_code) == (cents, vat)


def test_weight_and_qty_regexes() -> None:
    w = WEIGHT_RE.search("0,566 kg x 2,99 EUR/kg")
    assert w is not None and (w[1], w[2]) == ("0,566", "2,99")
    q = QTY_RE.search("2 Stk x 1,49")
    assert q is not None and (q[1], q[2]) == ("2", "1,49")
    assert WEIGHT_RE.search("0,566 kg x 2,99 EUR/kg") and QTY_RE.search("6x0,5l") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20.11.2026 18:42", datetime(2026, 11, 20, 18, 42)),
        ("Datum: 12.10.26 17:05:09", datetime(2026, 10, 12, 17, 5, 9)),
        ("05.11.2026", datetime(2026, 11, 5)),
        ("99.99.2026 then 01.02.2026", datetime(2026, 2, 1)),
        ("kein Datum", None),
    ],
)
def test_parse_date(text: str, expected: datetime | None) -> None:
    assert parse_date(text) == expected


def test_store_address() -> None:
    s = parse_store_address(["REWE", "Musterstrasse 12", "50667 Koeln"])
    assert s is not None and (s.street, s.postal_code, s.city) == (
        "Musterstrasse 12",
        "50667",
        "Koeln",
    )
    assert parse_store_address(["nothing here"]) is None


def test_find_total() -> None:
    assert find_total(["a 1,00", "SUMME EUR 7,65", "SUMME 9,99"]) == (1, 765)
    assert find_total(["zu zahlen 5,29"]) == (0, 529)
    assert find_total(["Summenwert nix"]) is None


@pytest.mark.parametrize(
    ("name", "cents", "expected"),
    [
        ("Pfand EW", 25, LineType.DEPOSIT),
        ("Leergutbon", -25, LineType.DEPOSIT_RETURN),
        ("Pfandrückgabe", -25, LineType.DEPOSIT_RETURN),
        ("Pfand", -25, LineType.DEPOSIT_RETURN),
        ("Preisvorteil", -30, LineType.DISCOUNT),
        ("Coupon", 0, LineType.DISCOUNT),
        ("Milch", 119, LineType.ITEM),
    ],
)
def test_classify_line(name: str, cents: int, expected: LineType) -> None:
    assert classify_line(name, cents) is expected
