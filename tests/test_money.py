import pytest

from price_tracker.domain.money import format_eur, parse_eur


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("1,49", 149),
        ("-0,30", -30),
        ("0,30-", -30),
        ("1.234,56", 123456),
        ("€ 2,00", 200),
        ("2,00 EUR", 200),
        ("0,05", 5),
        ("3", 300),
    ],
)
def test_parse_eur(text: str, cents: int) -> None:
    result = parse_eur(text)
    assert result == cents
    assert type(result) is int


def test_parse_invalid() -> None:
    with pytest.raises(ValueError):
        parse_eur("abc")


@pytest.mark.parametrize(
    ("cents", "text"), [(149, "1,49 €"), (-30, "-0,30 €"), (123456, "1.234,56 €"), (0, "0,00 €")]
)
def test_format_eur(cents: int, text: str) -> None:
    assert format_eur(cents) == text


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("EUR 1.234,56", 123456),
        ("1.234,56 EUR", 123456),
        ("eur 0,99", 99),
        ("1.234.567,89", 123456789),
        ("-1.234,56", -123456),
        ("1.234,56-", -123456),
        ("  0,30-  ", -30),
        ("€0,30-", -30),
        ("0,00", 0),
        ("-0,00", 0),
        ("1,5", 150),
    ],
)
def test_parse_eur_edge_cases(text: str, cents: int) -> None:
    result = parse_eur(text)
    assert result == cents
    assert type(result) is int


@pytest.mark.parametrize("text", ["", "   ", "EUR", "€", "-", "1,2,3", "1.234", "1.49"])
def test_parse_eur_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_eur(text)


@pytest.mark.parametrize("cents", [0, 5, 99, 100, 99999, 100000, -5, -123456])
def test_format_parse_roundtrip(cents: int) -> None:
    assert parse_eur(format_eur(cents)) == cents


@pytest.mark.parametrize(
    "text", ["--1,00", "-0,30-", "1e3", "1_000,00", "Infinity", "1,499", "0,005", "1,00--"]
)
def test_parse_eur_rejects_double_sign(text: str) -> None:
    with pytest.raises(ValueError):
        parse_eur(text)
