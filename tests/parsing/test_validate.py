from datetime import datetime

from price_tracker.domain.models import LineType, ParsedLine, ParsedReceipt, ReceiptStatus
from price_tracker.parsing.validate import assess


def receipt(**kw: object) -> ParsedReceipt:
    base: dict[str, object] = {
        "chain_code": "rewe",
        "store": None,
        "purchased_at": datetime(2026, 11, 20),
        "total_cents": 149,
        "lines": (ParsedLine(1, "Milch 1,49", LineType.ITEM, 149),),
        "confidence": 1.0,
    }
    base.update(kw)
    return ParsedReceipt(**base)  # type: ignore[arg-type]


def test_ok() -> None:
    assert assess(receipt(), 90.0) == (ReceiptStatus.PARSED, None)


def test_each_review_reason() -> None:
    cases = {
        "no total": receipt(total_cents=None),
        "!= total": receipt(total_cents=300),
        "no date": receipt(purchased_at=None),
        "low parse confidence": receipt(confidence=0.7),
        "no items": receipt(lines=(), total_cents=0),
    }
    for reason, p in cases.items():
        status, why = assess(p, 90.0)
        assert status is ReceiptStatus.NEEDS_REVIEW and why is not None and reason in why


def test_low_ocr_confidence() -> None:
    status, why = assess(receipt(), 59.0)
    assert status is ReceiptStatus.NEEDS_REVIEW and why is not None and "OCR" in why


def test_one_cent_tolerance() -> None:
    assert assess(receipt(total_cents=150), None)[0] is ReceiptStatus.PARSED
