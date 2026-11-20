"""Shared regexes and helpers for receipt text parsing."""

import re
from dataclasses import dataclass
from datetime import datetime

from price_tracker.domain.models import LineType, ParsedStore
from price_tracker.domain.money import parse_eur

PRICE_RE = re.compile(r"(?<![\d,])(-?\d{1,4},\d{2})(-?)\s*([A-Z*]{0,2})$")
WEIGHT_RE = re.compile(r"(\d+,\d{3})\s*kg\s*x\s*(\d+,\d{2})\s*(?:EUR|€)?\s*/\s*kg", re.IGNORECASE)
QTY_RE = re.compile(r"(?<![\d,])(\d+)\s*(?:Stk|St\.?)?\s*x\s*(\d+,\d{2})", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4}|\d{2})(?!\d)(?:\D{0,4}(\d{2}):(\d{2})(?::(\d{2}))?)?"
)
_POSTAL_RE = re.compile(r"^\s*(\d{5})\s+(\S.*?)\s*$")
_TOTAL_RE = re.compile(
    r"^\s*(?:summe|zu zahlen|gesamtbetrag|total)\b.*?(\d{1,5},\d{2})\s*(?:eur|€)?\s*$",
    re.IGNORECASE,
)
_STOP_RE = re.compile(
    r"^\s*(?:geg\.|bar\b|ec\b|karte\b|r(?:ü|ue)ckgeld|mwst|steuer)", re.IGNORECASE
)
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def fold(text: str) -> str:
    """Lowercase and fold German umlauts (ä->ae, ß->ss)."""
    return text.lower().translate(_FOLD)


@dataclass(frozen=True, slots=True)
class PriceMatch:
    """A trailing price: signed cents, VAT code, and where the price text starts."""

    cents: int
    vat_code: str | None
    start: int


def trailing_price(text: str) -> PriceMatch | None:
    """Match `1,49 B`, `-0,30`, `0,30-` at the end of a line."""
    m = PRICE_RE.search(text.rstrip())
    if m is None:
        return None
    cents = parse_eur(m.group(1) + m.group(2))
    return PriceMatch(cents, m.group(3) or None, m.start())


def parse_date(text: str) -> datetime | None:
    """First valid `dd.mm.yyyy` / `dd.mm.yy` (+ optional `HH:MM[:SS]`) in the text."""
    for m in _DATE_RE.finditer(text):
        day, month, year = int(m[1]), int(m[2]), int(m[3])
        if year < 100:
            year += 2000
        hh, mm, ss = int(m[4] or 0), int(m[5] or 0), int(m[6] or 0)
        try:
            return datetime(year, month, day, hh, mm, ss)  # noqa: DTZ001 - local time by design
        except ValueError:
            continue
    return None


def parse_store_address(lines: list[str], head: int = 15) -> ParsedStore | None:
    """Street = line before the first `12345 City` line within the first `head` lines."""
    top = [ln.strip() for ln in lines[:head]]
    for i, ln in enumerate(top):
        m = _POSTAL_RE.match(ln)
        if m is None:
            continue
        street = next((c for c in reversed(top[:i]) if c), None)
        if street is not None and not any(ch.isdigit() for ch in street):
            street = None
        return ParsedStore(street=street, postal_code=m[1], city=m[2])
    return None


def find_total(lines: list[str]) -> tuple[int, int] | None:
    """Index and cents of the first total line (`SUMME`, `zu zahlen`, ...)."""
    for i, ln in enumerate(lines):
        m = _TOTAL_RE.match(ln)
        if m is not None:
            return i, parse_eur(m.group(1))
    return None


def is_stop_line(line: str) -> bool:
    """Lines that end the item section (payment, VAT summary)."""
    return _STOP_RE.match(line) is not None


def classify_line(name: str, cents: int) -> LineType:
    """Deposit / deposit return / discount / item from the label and sign."""
    f = fold(name)
    if "leergut" in f or "pfandrueckgabe" in f or ("pfand" in f and cents < 0):
        return LineType.DEPOSIT_RETURN
    if "pfand" in f:
        return LineType.DEPOSIT
    if cents < 0 or any(k in f for k in ("rabatt", "preisvorteil", "coupon", "aktion")):
        return LineType.DISCOUNT
    return LineType.ITEM


def payment_method(lines: list[str]) -> str | None:
    """`card` or `cash` from lines after the total."""
    for ln in lines:
        f = fold(ln)
        if re.search(r"\b(ec|karte|visa|mastercard|girocard|kartenzahlung)\b", f):
            return "card"
        if re.match(r"\s*(bar|geg\.)", f):
            return "cash"
    return None
