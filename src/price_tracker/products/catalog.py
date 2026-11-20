"""CRUD helpers for categories, canonical products, aliases and chain-product attributes."""

import sqlite3
from datetime import UTC, datetime

from price_tracker.products.categorize import category_id_for_slug
from price_tracker.products.matching import backfill_base_quantities
from price_tracker.products.normalize import identity_key
from price_tracker.products.units import PackSize

BASE_UNITS = ("kg", "l", "pc")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def add_category(
    conn: sqlite3.Connection, slug: str, name_de: str, name_en: str, parent_slug: str | None
) -> int:
    parent_id: int | None = None
    if parent_slug is not None:
        parent_id = category_id_for_slug(conn, parent_slug)
        grand = conn.execute("SELECT parent_id FROM category WHERE id = ?", (parent_id,)).fetchone()
        if grand["parent_id"] is not None:
            raise ValueError("categories are limited to 2 levels")
    cur = conn.execute(
        "INSERT INTO category (parent_id, slug, name_de, name_en) VALUES (?, ?, ?, ?)",
        (parent_id, slug, name_de, name_en),
    )
    return int(cur.lastrowid or 0)


def add_canonical(
    conn: sqlite3.Connection,
    name: str,
    unit: str,
    category_slug: str | None = None,
    brand: str | None = None,
) -> int:
    if unit not in BASE_UNITS:
        raise ValueError(f"unit must be one of {BASE_UNITS}")
    cat_id = category_id_for_slug(conn, category_slug) if category_slug else None
    cur = conn.execute(
        "INSERT INTO canonical_product (name, category_id, base_unit, brand, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, cat_id, unit, brand, _now()),
    )
    return int(cur.lastrowid or 0)


def rename_canonical(conn: sqlite3.Connection, canonical_id: int, name: str) -> None:
    cur = conn.execute("UPDATE canonical_product SET name = ? WHERE id = ?", (name, canonical_id))
    if cur.rowcount == 0:
        raise ValueError(f"no canonical_product {canonical_id}")


def merge_canonical(conn: sqlite3.Connection, src: int, tgt: int) -> None:
    """Move all references from `src` to `tgt`, then delete `src`."""
    if src == tgt:
        raise ValueError("source and target are the same")
    for cid in (src, tgt):
        if conn.execute("SELECT 1 FROM canonical_product WHERE id = ?", (cid,)).fetchone() is None:
            raise ValueError(f"no canonical_product {cid}")
    conn.execute(
        "UPDATE chain_product SET canonical_product_id = ? WHERE canonical_product_id = ?",
        (tgt, src),
    )
    conn.execute(
        "UPDATE alias_rule SET canonical_product_id = ? WHERE canonical_product_id = ?", (tgt, src)
    )
    conn.execute(
        "UPDATE OR IGNORE match_rejection SET canonical_product_id = ? "
        "WHERE canonical_product_id = ?",
        (tgt, src),
    )
    conn.execute("DELETE FROM match_rejection WHERE canonical_product_id = ?", (src,))
    conn.execute("DELETE FROM canonical_product WHERE id = ?", (src,))
    conn.execute(
        "UPDATE receipt_line SET base_quantity_milli = NULL WHERE chain_product_id IN "
        "(SELECT id FROM chain_product WHERE canonical_product_id = ?)",
        (tgt,),
    )
    backfill_base_quantities(conn)


def add_alias(
    conn: sqlite3.Connection,
    pattern: str,
    canonical_id: int,
    chain_code: str | None,
    match_type: str,
    priority: int,
) -> int:
    if match_type not in ("exact", "prefix", "regex"):
        raise ValueError("type must be exact|prefix|regex")
    chain_id: int | None = None
    if chain_code is not None:
        row = conn.execute("SELECT id FROM chain WHERE code = ?", (chain_code,)).fetchone()
        if row is None:
            raise ValueError(f"unknown chain '{chain_code}'")
        chain_id = int(row["id"])
    # exact/prefix match the identity key: a pattern with a pack (`cola 0,33l`) becomes
    # `cola #330ml` and matches only that pack; without a pack it covers all pack sizes.
    stored = pattern if match_type == "regex" else identity_key(pattern)
    cur = conn.execute(
        "INSERT INTO alias_rule (chain_id, pattern, match_type, canonical_product_id, priority, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (chain_id, stored, match_type, canonical_id, priority, _now()),
    )
    return int(cur.lastrowid or 0)


def valid_ean(ean: str) -> bool:
    """EAN-8 / EAN-13: ASCII digits only and a correct GS1 check digit."""
    if len(ean) not in (8, 13) or not (ean.isascii() and ean.isdigit()):
        return False
    digits = [int(c) for c in ean]
    body = digits[:-1][::-1]  # weights 3,1,3,... from the right of the body
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
    return (10 - total % 10) % 10 == digits[-1]


def set_ean(conn: sqlite3.Connection, chain_product_id: int, ean: str) -> None:
    if not valid_ean(ean):
        raise ValueError(f"invalid EAN '{ean}' (EAN-8/EAN-13, digits only, valid check digit)")
    cur = conn.execute("UPDATE chain_product SET ean = ? WHERE id = ?", (ean, chain_product_id))
    if cur.rowcount == 0:
        raise ValueError(f"no chain_product {chain_product_id}")


def set_pack(conn: sqlite3.Connection, chain_product_id: int, pack: PackSize) -> None:
    cur = conn.execute(
        "UPDATE chain_product SET pack_size_milli = ?, pack_unit = ? WHERE id = ?",
        (pack.amount_milli, pack.unit.value, chain_product_id),
    )
    if cur.rowcount == 0:
        raise ValueError(f"no chain_product {chain_product_id}")
    conn.execute(
        "UPDATE receipt_line SET base_quantity_milli = NULL WHERE chain_product_id = ?",
        (chain_product_id,),
    )
    backfill_base_quantities(conn, chain_product_id)
