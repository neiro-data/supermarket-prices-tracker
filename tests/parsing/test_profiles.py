import json
from pathlib import Path
from typing import Any

import pytest

from price_tracker.domain.models import ParsedReceipt
from price_tracker.parsing import profiles  # noqa: F401
from price_tracker.parsing.base import PARSERS
from price_tracker.parsing.detect import detect_chain, load_chain_infos

FIXTURES = Path(__file__).parent.parent / "fixtures" / "receipts"
NAMES = sorted(p.stem for p in FIXTURES.glob("*.txt"))


def parse_fixture(name: str) -> ParsedReceipt:
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    chain = detect_chain(text, load_chain_infos())
    profile = chain.parser_profile if chain else "generic"
    return PARSERS[profile].parse(text, chain.code if chain else None)


def as_dict(p: ParsedReceipt) -> dict[str, Any]:
    return {
        "chain": p.chain_code,
        "profile": p.parser_profile,
        "purchased_at": p.purchased_at.isoformat() if p.purchased_at else None,
        "total_cents": p.total_cents,
        "city": p.store.city if p.store else None,
        "lines": [
            {
                "type": ln.line_type.value,
                "name": ln.name,
                "qty_milli": ln.quantity_milli,
                "unit": ln.quantity_unit.value,
                "unit_price_cents": ln.unit_price_cents,
                "total_cents": ln.line_total_cents,
                "applies_to": ln.applies_to_line_no,
            }
            for ln in p.lines
        ],
    }


@pytest.mark.parametrize("name", NAMES)
def test_fixture_matches_expected(name: str) -> None:
    expected = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    assert as_dict(parse_fixture(name)) == expected


@pytest.mark.parametrize("name", [n for n in NAMES if n != "ocr_noisy_lidl"])
def test_clean_fixtures_sum_to_total(name: str) -> None:
    p = parse_fixture(name)
    assert sum(ln.line_total_cents for ln in p.lines) == p.total_cents


def test_weighed_item_and_discount_link() -> None:
    p = parse_fixture("rewe_ebon")
    grapes = next(ln for ln in p.lines if ln.name.startswith("TAFELTR"))
    assert (grapes.quantity_milli, grapes.quantity_unit.value) == (566, "kg")
    discount = next(ln for ln in p.lines if ln.line_type.value == "discount")
    assert discount.applies_to_line_no == 4


def test_aldi_quantity_prefix_applies_to_next_item() -> None:
    water = parse_fixture("aldi_sued").lines[0]
    assert (water.name, water.quantity_milli, water.unit_price_cents) == ("Mineralwasser", 2000, 99)
