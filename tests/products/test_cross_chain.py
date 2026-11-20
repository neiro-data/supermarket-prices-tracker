import sqlite3

from price_tracker.products import catalog
from price_tracker.products.matching import assign
from price_tracker.products.normalize import normalize_name
from price_tracker.products.units import parse_pack_size
from tests.products.test_matching import add_receipt


def test_rewe_vs_lidl_grapes_per_kg(db: sqlite3.Connection) -> None:
    cid = catalog.add_canonical(db, "Weintrauben hell kernlos", "kg", "obst")
    rewe = int(db.execute("SELECT id FROM chain WHERE code = 'rewe'").fetchone()["id"])
    lidl = int(db.execute("SELECT id FROM chain WHERE code = 'lidl'").fetchone()["id"])
    lines = (
        (rewe, "rewe", "TAFELTR.HELL KERNL", 566, "kg", 169),
        (lidl, "lidl", "Trauben hell kernlos 500g", 1000, "pc", 179),
    )
    for ch, code, raw, q, unit, total in lines:
        rid = add_receipt(db, code)
        pack = parse_pack_size(raw)
        cur = db.execute(
            "INSERT INTO chain_product (chain_id, raw_name, normalized_name, pack_size_milli, "
            "pack_unit) VALUES (?, ?, ?, ?, ?)",
            (ch, raw, normalize_name(raw), pack.amount_milli if pack else None,
             pack.unit.value if pack else None),
        )  # fmt: skip
        cp = int(cur.lastrowid or 0)
        db.execute(
            "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, "
            "chain_product_id, quantity_milli, quantity_unit, line_total_cents) "
            "VALUES (?, 1, ?, 'item', ?, ?, ?, ?)",
            (rid, raw, cp, q, unit, total),
        )
        assign(db, cp, cid, "manual", False)
    rows = db.execute(
        "SELECT chain_code, price_per_base_cents AS p, category_slug FROM v_line_effective "
        "ORDER BY chain_code"
    ).fetchall()
    assert [(r["chain_code"], r["p"], r["category_slug"]) for r in rows] == [
        ("lidl", 358, "obst"),
        ("rewe", 299, "obst"),
    ]
