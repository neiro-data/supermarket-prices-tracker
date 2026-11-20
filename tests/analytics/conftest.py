"""Analytics fixture DB: 3 chains, 2 cities, 10 valid receipts over 8 weeks (+1 excluded)."""

import sqlite3
from pathlib import Path

import pytest

NOW = "2026-03-01T00:00:00"

# (purchased_at, chain code, store key, [lines]); a line is
# (type, text, product key | None, qty_milli, unit, total_cents, base_qty_milli | None, applies_to)
Line = tuple[str, str, str | None, int, str, int, int | None, int | None]
RECEIPTS: list[tuple[str, str, str, list[Line]]] = [
    (
        "2026-01-05T10:00:00",
        "rewe",
        "berlin",
        [
            ("item", "MILCH", "milk", 1000, "pc", 100, 1000, None),
            ("item", "AEPFEL", "apples", 500, "kg", 250, 500, None),
            ("deposit", "PFAND", None, 1000, "pc", 25, None, None),
        ],
    ),
    (
        "2026-01-07T18:30:00",
        "lidl",
        "berlin",
        [
            ("item", "MILCH", "milk", 1000, "pc", 90, 1000, None),
            ("item", "SNACK", "snack", 1000, "pc", 200, None, None),
            ("discount", "RABATT", None, 1000, "pc", -50, None, 2),
        ],
    ),
    (
        "2026-01-12T09:00:00",
        "rewe",
        "berlin",
        [
            ("item", "MILCH", "milk", 1000, "pc", 110, 1000, None),
            ("deposit", "PFAND", None, 1000, "pc", 25, None, None),
        ],
    ),
    (
        "2026-01-19T14:00:00",
        "aldi_sued",
        "munich",
        [
            ("item", "AEPFEL", "apples", 1000, "kg", 300, 1000, None),
            ("item", "MILCH", "milk", 1000, "pc", 80, 1000, None),
        ],
    ),
    (
        "2026-01-26T10:00:00",
        "lidl",
        "berlin",
        [
            ("item", "MILCH", "milk", 1000, "pc", 95, 1000, None),
            ("item", "AEPFEL", "apples", 800, "kg", 400, 800, None),
        ],
    ),
    (
        "2026-02-02T19:00:00",
        "rewe",
        "berlin",
        [
            ("item", "MILCH", "milk", 1000, "pc", 105, 1000, None),
            ("item", "SNACK", "snack", 1000, "pc", 150, None, None),
        ],
    ),
    (
        "2026-02-09T11:00:00",
        "aldi_sued",
        "munich",
        [
            ("item", "MILCH", "milk", 1000, "pc", 85, 1000, None),
            ("item", "AEPFEL", "apples", 500, "kg", 175, 500, None),
        ],
    ),
    (
        "2026-02-09T17:00:00",
        "lidl",
        "berlin",
        [("item", "MILCH", "milk", 1000, "pc", 92, 1000, None)],
    ),
    (
        "2026-02-16T10:00:00",
        "rewe",
        "berlin",
        [
            ("item", "AEPFEL", "apples", 600, "kg", 360, 600, None),
            ("item", "MILCH", "milk", 2000, "pc", 220, 2000, None),
        ],
    ),
    (
        "2026-02-26T10:00:00",
        "lidl",
        "berlin",
        [("item", "MILCH", "milk", 1000, "pc", 94, 1000, None)],
    ),
]


def _insert_receipt(
    conn: sqlite3.Connection,
    idx: int,
    when: str,
    chain_id: int,
    store_id: int,
    status: str,
    lines: list[Line],
    products: dict[tuple[int, str], int],
    chain_code: str,
) -> None:
    sf = conn.execute(
        "INSERT INTO source_file (sha256, original_name, archive_path, media_type, page_count,"
        " extraction_method, raw_text, ingested_at)"
        " VALUES (?, ?, ?, 'pdf', 1, 'text_layer', '', ?)",
        (f"sha{idx}", f"r{idx}.pdf", f"r{idx}.pdf", NOW),
    ).lastrowid
    total = sum(ln[5] for ln in lines)
    rid = conn.execute(
        "INSERT INTO receipt (source_file_id, chain_id, store_id, purchased_at, total_cents,"
        " lines_sum_cents, parser_profile, parse_confidence, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'generic', 1.0, ?, ?, ?)",
        (sf, chain_id, store_id, when, total, total, status, NOW, NOW),
    ).lastrowid
    for no, (ltype, text, key, qty, unit, cents, base, applies) in enumerate(lines, start=1):
        conn.execute(
            "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, chain_product_id,"
            " quantity_milli, quantity_unit, line_total_cents, base_quantity_milli,"
            " applies_to_line_no)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rid,
                no,
                text,
                ltype,
                products[(chain_id, key)] if key else None,
                qty,
                unit,
                cents,
                base,
                applies,
            ),
        )


@pytest.fixture
def analytics_db(db: sqlite3.Connection) -> Path:
    """Populated SQLite DB (built on the seeded `db` fixture); returns its path."""
    cat = {r["slug"]: r["id"] for r in db.execute("SELECT id, slug FROM category")}
    chains = {r["code"]: r["id"] for r in db.execute("SELECT id, code FROM chain")}
    stores: dict[tuple[str, str], int] = {}
    for chain_code in ("rewe", "lidl", "aldi_sued"):
        for key, city, street in (("berlin", "Berlin", "Str 1"), ("munich", "Muenchen", "Str 2")):
            stores[(chain_code, key)] = db.execute(
                "INSERT INTO store (chain_id, name, street, postal_code, city)"
                " VALUES (?, ?, ?, ?, ?)",
                (chains[chain_code], chain_code, street, key, city),
            ).lastrowid  # type: ignore[assignment]
    canon = {
        "milk": db.execute(
            "INSERT INTO canonical_product (name, category_id, base_unit, created_at)"
            " VALUES ('Milch 1L', ?, 'l', ?)",
            (cat["milch"], NOW),
        ).lastrowid,
        "apples": db.execute(
            "INSERT INTO canonical_product (name, category_id, base_unit, created_at)"
            " VALUES ('Aepfel', ?, 'kg', ?)",
            (cat["obst"], NOW),
        ).lastrowid,
    }
    products: dict[tuple[int, str], int] = {}
    for code, cid in chains.items():
        if code not in ("rewe", "lidl", "aldi_sued"):
            continue
        for key, canonical in (*canon.items(), ("snack", None)):
            products[(cid, key)] = db.execute(
                "INSERT INTO chain_product (chain_id, raw_name, normalized_name,"
                " canonical_product_id, match_source) VALUES (?, ?, ?, ?, ?)",
                (cid, key.upper(), key, canonical, "manual" if canonical else None),
            ).lastrowid  # type: ignore[assignment]
    for i, (when, code, store_key, lines) in enumerate(RECEIPTS, start=1):
        _insert_receipt(
            db,
            i,
            when,
            chains[code],
            stores[(code, store_key)],
            "confirmed" if i % 2 else "parsed",
            lines,
            products,
            code,
        )
    # Excluded receipt: must never show up in any report.
    _insert_receipt(
        db,
        99,
        "2026-02-27T10:00:00",
        chains["lidl"],
        stores[("lidl", "berlin")],
        "needs_review",
        [("item", "MILCH", "milk", 1000, "pc", 999, 1000, None)],
        products,
        "lidl",
    )
    path = db.execute("PRAGMA database_list").fetchone()["file"]
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return Path(path)
