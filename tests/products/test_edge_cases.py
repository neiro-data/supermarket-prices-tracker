import sqlite3

import pytest

from price_tracker.products import catalog
from price_tracker.products.matching import (
    apply_rules,
    assign,
    auto_match_all,
    reject,
    suggest,
)
from price_tracker.products.normalize import normalize_name
from price_tracker.products.units import parse_pack_size
from tests.products.test_matching import add_cp, add_receipt


def add_line(db: sqlite3.Connection, code: str, cp: int, qty: int, unit: str, total: int) -> int:
    rid = add_receipt(db, code)
    cur = db.execute(
        "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, chain_product_id, "
        "quantity_milli, quantity_unit, line_total_cents) VALUES (?, 1, 'x', 'item', ?, ?, ?, ?)",
        (rid, cp, qty, unit, total),
    )
    return int(cur.lastrowid or 0)


def base_qty(db: sqlite3.Connection, line_id: int) -> int | None:
    row = db.execute("SELECT base_quantity_milli AS b FROM receipt_line WHERE id = ?", (line_id,))
    return row.fetchone()["b"]  # type: ignore[no-any-return]


@pytest.mark.parametrize("raw", ["", "   ", "...", "500g", "-", "%"])
def test_normalize_odd_inputs_do_not_crash(raw: str) -> None:
    out = normalize_name(raw)
    assert out == out.strip()
    assert normalize_name(out) == out


def test_normalize_odd_inputs_empty() -> None:
    assert normalize_name("") == ""
    assert normalize_name("  500g ") == ""


@pytest.mark.parametrize("raw", ["", "abc", "0g", "x l", "1,5", "Milch 3,5% 1L"])
def test_parse_pack_size_odd(raw: str) -> None:
    res = parse_pack_size(raw)
    assert res is None or res.amount_milli >= 0


def test_units_mismatch_yields_no_base_quantity(db: sqlite3.Connection) -> None:
    c = catalog.add_canonical(db, "Wasser", "l")
    cp = add_cp(db, "lidl", "wasser", pack=(500, "kg"))
    line = add_line(db, "lidl", cp, 1000, "pc", 100)
    assign(db, cp, c, "manual", False)
    assert base_qty(db, line) is None


def test_pc_canonical_counts_plain_pieces(db: sqlite3.Connection) -> None:
    c = catalog.add_canonical(db, "Joghurt", "pc")
    cp = add_cp(db, "lidl", "joghurt")
    line = add_line(db, "lidl", cp, 3000, "pc", 100)
    assign(db, cp, c, "manual", False)
    assert base_qty(db, line) == 3000


def test_assign_unknown_canonical_raises(db: sqlite3.Connection) -> None:
    cp = add_cp(db, "lidl", "x")
    with pytest.raises(ValueError, match="canonical"):
        assign(db, cp, 999, "manual", False)
    with pytest.raises(ValueError, match="chain_product"):
        assign(db, 999, 1, "manual", False)


def test_auto_match_all_is_idempotent(db: sqlite3.Connection) -> None:
    c = catalog.add_canonical(db, "Bananen", "kg")
    catalog.add_alias(db, "bananen", c, None, "exact", 100)
    add_cp(db, "rewe", "bananen")
    add_cp(db, "rewe", "unbekannt")
    first = auto_match_all(db)
    second = auto_match_all(db)
    assert (first.by_rule, first.unmatched) == (1, 1)
    assert (second.by_rule, second.by_ean, second.unmatched) == (0, 0, 1)


def test_assign_rule_twice_upserts_single_rule(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "kg")
    b = catalog.add_canonical(db, "B", "kg")
    cp = add_cp(db, "rewe", "foo")
    assign(db, cp, a, "manual", True)
    assign(db, cp, b, "manual", True)
    rows = db.execute("SELECT canonical_product_id AS c FROM alias_rule").fetchall()
    assert [r["c"] for r in rows] == [b]


def test_assign_clears_matching_rejection(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "foo bar", "kg")
    cp = add_cp(db, "rewe", "foo bar")
    reject(db, cp, a)
    reject(db, cp, a)  # idempotent
    assign(db, cp, a, "manual", False)
    assert db.execute("SELECT COUNT(*) AS n FROM match_rejection").fetchone()["n"] == 0


def test_rejection_of_one_does_not_hide_others(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "Weintrauben hell", "kg")
    b = catalog.add_canonical(db, "Weintrauben hell kernlos", "kg")
    cp = add_cp(db, "rewe", "weintrauben hell kernlos")
    reject(db, cp, b)
    assert [s.canonical_id for s in suggest(db, cp)] == [a]


def test_suggest_empty_name_and_no_candidates(db: sqlite3.Connection) -> None:
    cp = add_cp(db, "rewe", "")
    assert suggest(db, cp) == []
    catalog.add_canonical(db, "Bananen", "kg")
    assert suggest(db, cp) == []


def test_regex_is_fullmatch_not_search(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "pc")
    cp = add_cp(db, "lidl", "bio milbona joghurt")
    catalog.add_alias(db, r"milbona", a, None, "regex", 1)
    assert apply_rules(db, cp) is None
    catalog.add_alias(db, r".*milbona.*", a, None, "regex", 2)
    assert apply_rules(db, cp) == a


def test_invalid_regex_rule_is_skipped(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "pc")
    cp = add_cp(db, "lidl", "foo")
    catalog.add_alias(db, "(", a, None, "regex", 1)
    assert apply_rules(db, cp) is None


def test_rule_of_other_chain_does_not_apply(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "pc")
    cp = add_cp(db, "lidl", "foo")
    catalog.add_alias(db, "foo", a, "rewe", "exact", 1)
    assert apply_rules(db, cp) is None


def test_merge_moves_refs_and_recomputes_quantities(db: sqlite3.Connection) -> None:
    src = catalog.add_canonical(db, "Src", "kg")
    tgt = catalog.add_canonical(db, "Tgt", "kg")
    cp = add_cp(db, "lidl", "trauben", pack=(500, "kg"))
    other = add_cp(db, "rewe", "other")
    line = add_line(db, "lidl", cp, 2000, "pc", 358)
    assign(db, cp, src, "manual", True)
    reject(db, other, src)
    reject(db, other, tgt)  # would collide after the move
    assert base_qty(db, line) == 1000
    catalog.merge_canonical(db, src, tgt)
    assert db.execute("SELECT canonical_product_id AS c FROM chain_product WHERE id = ?",
                      (cp,)).fetchone()["c"] == tgt  # fmt: skip
    assert db.execute("SELECT canonical_product_id AS c FROM alias_rule").fetchone()["c"] == tgt
    assert db.execute("SELECT COUNT(*) AS n FROM canonical_product WHERE id = ?",
                      (src,)).fetchone()["n"] == 0  # fmt: skip
    assert db.execute("SELECT COUNT(*) AS n FROM match_rejection").fetchone()["n"] == 1
    assert base_qty(db, line) == 1000


def test_merge_with_unit_mismatch_nulls_quantity(db: sqlite3.Connection) -> None:
    src = catalog.add_canonical(db, "Src", "kg")
    tgt = catalog.add_canonical(db, "Tgt", "l")
    cp = add_cp(db, "lidl", "trauben", pack=(500, "kg"))
    line = add_line(db, "lidl", cp, 1000, "pc", 100)
    assign(db, cp, src, "manual", False)
    assert base_qty(db, line) == 500
    catalog.merge_canonical(db, src, tgt)
    assert base_qty(db, line) is None


def test_merge_invalid(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "kg")
    with pytest.raises(ValueError, match="same"):
        catalog.merge_canonical(db, a, a)
    with pytest.raises(ValueError, match="canonical"):
        catalog.merge_canonical(db, a, 999)


def test_alias_delete_stops_rule_and_keeps_existing_mapping(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "pc")
    rule = catalog.add_alias(db, "foo", a, None, "exact", 1)
    mapped = add_cp(db, "rewe", "foo")
    auto_match_all(db)
    db.execute("DELETE FROM alias_rule WHERE id = ?", (rule,))
    later = add_cp(db, "lidl", "foo")
    stats = auto_match_all(db)
    assert stats.by_rule == 0 and stats.unmatched == 1
    assert db.execute("SELECT canonical_product_id AS c FROM chain_product WHERE id = ?",
                      (mapped,)).fetchone()["c"] == a  # fmt: skip
    assert apply_rules(db, later) is None


def test_add_alias_validation(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "pc")
    with pytest.raises(ValueError, match="type"):
        catalog.add_alias(db, "x", a, None, "glob", 1)
    with pytest.raises(ValueError, match="chain"):
        catalog.add_alias(db, "x", a, "nochain", "exact", 1)
    with pytest.raises(ValueError, match="unit"):
        catalog.add_canonical(db, "B", "m")


def test_set_pack_recomputes_existing_lines(db: sqlite3.Connection) -> None:
    from price_tracker.domain.models import Unit
    from price_tracker.products.units import PackSize

    c = catalog.add_canonical(db, "A", "kg")
    cp = add_cp(db, "lidl", "foo", pack=(500, "kg"))
    line = add_line(db, "lidl", cp, 1000, "pc", 100)
    assign(db, cp, c, "manual", False)
    assert base_qty(db, line) == 500
    catalog.set_pack(db, cp, PackSize(250, Unit.KG))
    assert base_qty(db, line) == 250
    with pytest.raises(ValueError, match="chain_product"):
        catalog.set_pack(db, 999, PackSize(1, Unit.KG))
