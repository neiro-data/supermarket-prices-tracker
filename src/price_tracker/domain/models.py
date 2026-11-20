"""Parsed-receipt domain models (money in integer cents, quantities in milli-units)."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class LineType(StrEnum):
    ITEM = "item"
    DISCOUNT = "discount"
    DEPOSIT = "deposit"
    DEPOSIT_RETURN = "deposit_return"
    OTHER = "other"


class Unit(StrEnum):
    PC = "pc"
    KG = "kg"
    L = "l"


class ReceiptStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    PARSED = "parsed"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ParsedLine:
    line_no: int
    raw_text: str
    line_type: LineType
    line_total_cents: int
    quantity_milli: int = 1000
    quantity_unit: Unit = Unit.PC
    unit_price_cents: int | None = None
    vat_code: str | None = None
    applies_to_line_no: int | None = None
    article_no: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedStore:
    name: str | None = None
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country_code: str = "DE"


@dataclass(frozen=True, slots=True)
class ParsedReceipt:
    chain_code: str | None
    store: ParsedStore | None
    purchased_at: datetime | None
    total_cents: int | None
    lines: tuple[ParsedLine, ...]
    confidence: float
    warnings: tuple[str, ...] = ()
    currency: str = "EUR"
    payment_method: str | None = None
    parser_profile: str = "generic"
