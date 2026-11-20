import sqlite3

import pytest
from typer.testing import CliRunner

from price_tracker.domain.models import LineType, Unit
from price_tracker.parsing.service import get_or_create_chain_product
from price_tracker.products import catalog
from price_tracker.products.categorize import (
    effective_category_slug,
    line_category_slug,
    set_category,
)
from price_tracker.products.matching import (
    apply_rules,
    assign,
    backfill_base_quantities,
    suggest,
)
from price_tracker.products.normalize import identity_key
from price_tracker.products.units import PackSize, base_quantity_for
from tests.products.test_matching import add_receipt


def _line(db: sqlite3.Connection, code: str, cp: int, qty: int, unit: str, total: int) -> None:
    db.execute(
        "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, chain_product_id, "
        "quantity_milli, quantity_unit, line_total_cents) VALUES (?, 1, 'x', 'item', ?, ?, ?, ?)",
        (add_receipt(db, code), cp, qty, unit, total),
    )


def _chain(db: sqlite3.Connection, code: str) -> int:
    return int(db.execute("SELECT id FROM chain WHERE code = ?", (code,)).fetchone()["id"])


def test_identity_key_canonicalises_pack() -> None:
    assert identity_key("Cola 0,33l") == identity_key("COLA 330 ml") == "cola #330ml"
    assert identity_key("Cola 1,5l") == "cola #1500ml"
    assert identity_key("Mehl 1kg") == identity_key("Mehl 1000g")
    assert identity_key("Bananen") == "bananen"


def test_cola_pack_sizes_are_two_chain_products(db: sqlite3.Connection) -> None:
    rewe = _chain(db, "rewe")
    small = get_or_create_chain_product(db, rewe, "Cola 0,33l", False)
    big = get_or_create_chain_product(db, rewe, "Cola 1,5l", False)
    assert small != big
    assert get_or_create_chain_product(db, rewe, "COLA 330ml", False) == small
    cid = catalog.add_canonical(db, "Cola", "l", "getraenke")
    _line(db, "rewe", small, 1000, "pc", 99)
    _line(db, "rewe", big, 1000, "pc", 149)
    assign(db, small, cid, "manual", False)
    assign(db, big, cid, "manual", False)
    rows = db.execute(
        "SELECT chain_product_id AS cp, price_per_base_cents AS p FROM v_line_effective"
    ).fetchall()
    assert {r["cp"]: r["p"] for r in rows} == {small: 300, big: 99}


def test_suggest_ignores_pack_and_rules_cover_packs(db: sqlite3.Connection) -> None:
    cid = catalog.add_canonical(db, "Cola", "l")
    rewe = _chain(db, "rewe")
    a = get_or_create_chain_product(db, rewe, "Cola 0,33l", False)
    b = get_or_create_chain_product(db, rewe, "Cola 1,5l", False)
    assign(db, a, cid, "manual", False)
    assert [s.canonical_id for s in suggest(db, b)] == [cid]
    catalog.add_alias(db, "cola 1,5l", cid, "rewe", "exact", 100)
    assert apply_rules(db, b) == cid
    assert apply_rules(db, get_or_create_chain_product(db, rewe, "Cola 2l", False)) is None
    catalog.add_alias(db, "cola", cid, "rewe", "exact", 100)
    assert apply_rules(db, get_or_create_chain_product(db, rewe, "Cola 2l", False)) == cid


def test_line_category_and_effective(db: sqlite3.Connection) -> None:
    rewe = _chain(db, "rewe")
    assert line_category_slug(LineType.DEPOSIT, None) == "pfand"
    assert line_category_slug(LineType.DEPOSIT_RETURN, "obst") == "pfand"
    assert line_category_slug(LineType.ITEM, "obst") == "obst"
    assert line_category_slug(LineType.ITEM, None) is None
    cid = catalog.add_canonical(db, "X", "l", "obst")
    other = get_or_create_chain_product(db, rewe, "Foo", False)
    assign(db, other, cid, "manual", False)
    assert effective_category_slug(db, other) == "obst"
    with pytest.raises(ValueError):
        set_category(db, "obst")


def test_base_quantity_pc_canonical_rejects_weight_pack() -> None:
    assert base_quantity_for(1000, "pc", PackSize(250, Unit.KG), "pc") is None
    assert base_quantity_for(2000, "pc", None, "pc") == 2000


def test_backfill_respects_weighed_guard(db: sqlite3.Connection) -> None:
    cid = catalog.add_canonical(db, "Trauben", "kg")
    cp = get_or_create_chain_product(db, _chain(db, "lidl"), "Trauben 500g", True)
    _line(db, "lidl", cp, 1000, "pc", 179)
    assign(db, cp, cid, "manual", False)
    backfill_base_quantities(db)
    assert db.execute("SELECT base_quantity_milli AS b FROM receipt_line").fetchone()["b"] is None


@pytest.mark.parametrize(
    ("ean", "ok"),
    [("4006381333931", True), ("96385074", True), ("4006381333932", False),
     ("12345", False), ("40063813339a1", False), ("", False)],
)  # fmt: skip
def test_valid_ean(ean: str, ok: bool) -> None:
    assert catalog.valid_ean(ean) is ok


def test_set_ean_rejects_invalid(db: sqlite3.Connection) -> None:
    cp = get_or_create_chain_product(db, _chain(db, "lidl"), "X", False)
    with pytest.raises(ValueError):
        catalog.set_ean(db, cp, "123")
    catalog.set_ean(db, cp, "4006381333931")


def test_set_category_cli_commands_exist() -> None:
    from price_tracker.products.cli import canonical_app, products_app

    runner = CliRunner()
    for app in (products_app, canonical_app):
        assert "set-category" in runner.invoke(app, ["--help"]).output
