"""Decide whether a parsed receipt can be trusted or needs manual review."""

from price_tracker.domain.models import LineType, ParsedReceipt, ReceiptStatus

MIN_OCR_CONFIDENCE = 60.0
MIN_PARSE_CONFIDENCE = 0.8


def assess(p: ParsedReceipt, ocr_confidence: float | None) -> tuple[ReceiptStatus, str | None]:
    """`needs_review` (with reasons) or `parsed`."""
    reasons: list[str] = []
    if p.total_cents is None:
        reasons.append("no total")
    else:
        lines_sum = sum(ln.line_total_cents for ln in p.lines)
        if abs(lines_sum - p.total_cents) > 1:
            reasons.append(f"lines sum {lines_sum} != total {p.total_cents}")
    if p.purchased_at is None:
        reasons.append("no date")
    if ocr_confidence is not None and ocr_confidence < MIN_OCR_CONFIDENCE:
        reasons.append(f"low OCR confidence {ocr_confidence:.0f}")
    if p.confidence < MIN_PARSE_CONFIDENCE:
        reasons.append(f"low parse confidence {p.confidence:.2f}")
    if not any(ln.line_type is LineType.ITEM for ln in p.lines):
        reasons.append("no items")
    if reasons:
        return ReceiptStatus.NEEDS_REVIEW, "; ".join(reasons)
    return ReceiptStatus.PARSED, None
