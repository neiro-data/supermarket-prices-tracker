"""Line-by-line receipt parser shared by all chain profiles (differences are options)."""

from dataclasses import dataclass, replace
from typing import Literal

from price_tracker.domain.models import (
    LineType,
    ParsedLine,
    ParsedReceipt,
    Unit,
)
from price_tracker.domain.money import parse_eur
from price_tracker.parsing.common import (
    QTY_RE,
    WEIGHT_RE,
    classify_line,
    find_total,
    is_stop_line,
    parse_date,
    parse_store_address,
    payment_method,
    trailing_price,
)


@dataclass(frozen=True, slots=True)
class ProfileOptions:
    """Where a quantity/weight detail line sits relative to its item line."""

    detail: Literal["after", "before"] = "after"


@dataclass(frozen=True, slots=True)
class _Detail:
    qty_milli: int
    unit: Unit
    unit_price_cents: int
    rest: str  # line text without the matched detail


def _match_detail(text: str) -> _Detail | None:
    m = WEIGHT_RE.search(text)
    if m is not None:
        qty = int(m.group(1).replace(",", ""))
        return _Detail(qty, Unit.KG, parse_eur(m.group(2)), (text[: m.start()] + text[m.end() :]))
    m = QTY_RE.search(text)
    if m is not None:
        return _Detail(
            int(m.group(1)) * 1000,
            Unit.PC,
            parse_eur(m.group(2)),
            text[: m.start()] + text[m.end() :],
        )
    return None


def parse_receipt(
    text: str, chain_code: str | None, profile: str, options: ProfileOptions
) -> ParsedReceipt:
    """Parse receipt text into items, discounts and deposits plus header metadata."""
    lines = text.splitlines()
    total = find_total(lines)
    end = len(lines)
    for i, ln in enumerate(lines):
        if is_stop_line(ln):
            end = min(end, i)
            break
    if total is not None:
        end = min(end, total[0])

    parsed: list[ParsedLine] = []
    pending_name: str | None = None
    pending_detail: _Detail | None = None
    last_item: int | None = None  # index into `parsed`

    def add(raw: str, name: str, cents: int, vat: str | None, det: _Detail | None) -> None:
        nonlocal last_item
        line_no = len(parsed) + 1
        ltype = classify_line(name, cents)
        applies = (
            parsed[last_item].line_no
            if ltype is LineType.DISCOUNT and last_item is not None
            else None
        )
        if ltype is LineType.ITEM and det is not None:
            line = ParsedLine(
                line_no,
                raw,
                ltype,
                cents,
                det.qty_milli,
                det.unit,
                det.unit_price_cents,
                vat,
                name=name,
            )
        else:
            line = ParsedLine(
                line_no, raw, ltype, cents, vat_code=vat, applies_to_line_no=applies, name=name
            )
        parsed.append(line)
        if ltype is LineType.ITEM:
            last_item = len(parsed) - 1

    for raw in lines[:end]:
        s = raw.strip()
        if not s:
            continue
        det = _match_detail(s)
        if det is not None:
            price = trailing_price(det.rest)
            if price is not None:  # detail + total on one line (name on previous line)
                name = pending_name or det.rest[: price.start].strip() or "?"
                add(raw, name, price.cents, price.vat_code, det)
                pending_name = None
            elif options.detail == "before":
                pending_detail = det
            elif last_item is not None:
                prev = parsed[last_item]
                parsed[last_item] = replace(
                    prev,
                    quantity_milli=det.qty_milli,
                    quantity_unit=det.unit,
                    unit_price_cents=det.unit_price_cents,
                )
            continue
        price = trailing_price(s)
        if price is None:
            pending_name = s
            continue
        name = s[: price.start].strip() or (pending_name or "?")
        add(raw, name, price.cents, price.vat_code, pending_detail)
        pending_detail = None
        pending_name = None

    tail = lines[end:]
    purchased_at = parse_date(text)
    store = parse_store_address(lines)
    lines_sum = sum(p.line_total_cents for p in parsed)
    warnings: list[str] = []
    confidence = 1.0
    if total is None:
        warnings.append("no total line found")
        confidence -= 0.4
    elif abs(lines_sum - total[1]) > 1:
        warnings.append(f"line sum {lines_sum} differs from total {total[1]}")
        confidence -= 0.1
    if purchased_at is None:
        warnings.append("no purchase date found")
        confidence -= 0.2
    if not any(p.line_type is LineType.ITEM for p in parsed):
        warnings.append("no item lines found")
        confidence -= 0.5
    return ParsedReceipt(
        chain_code=chain_code,
        store=store,
        purchased_at=purchased_at,
        total_cents=total[1] if total else None,
        lines=tuple(parsed),
        confidence=max(0.0, round(confidence, 2)),
        warnings=tuple(warnings),
        payment_method=payment_method(tail),
        parser_profile=profile,
    )
