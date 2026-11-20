"""Category handling: inheritance from canonical products and manual overrides."""

import sqlite3

from price_tracker.domain.models import LineType

PFAND_SLUG = "pfand"


def category_id_for_slug(conn: sqlite3.Connection, slug: str) -> int:
    row = conn.execute("SELECT id FROM category WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise ValueError(f"unknown category '{slug}'")
    return int(row["id"])


def line_type_category_slug(line_type: LineType) -> str | None:
    """Deposit is global: deposit lines always belong to `pfand`, independent of chain."""
    return PFAND_SLUG if line_type in (LineType.DEPOSIT, LineType.DEPOSIT_RETURN) else None


def line_category_slug(line_type: LineType, effective_slug: str | None) -> str | None:
    """Category of a receipt line: `pfand` by line_type, else the chain product's effective slug."""
    return line_type_category_slug(line_type) or effective_slug


def effective_category_slug(conn: sqlite3.Connection, chain_product_id: int) -> str | None:
    """Own category, else the one inherited from the canonical product."""
    row = conn.execute(
        "SELECT cat.slug AS slug FROM chain_product AS cp "
        "LEFT JOIN canonical_product AS c ON c.id = cp.canonical_product_id "
        "LEFT JOIN category AS cat ON cat.id = COALESCE(cp.category_id, c.category_id) "
        "WHERE cp.id = ?",
        (chain_product_id,),
    ).fetchone()
    return str(row["slug"]) if row and row["slug"] else None


def set_category(
    conn: sqlite3.Connection,
    slug: str,
    *,
    chain_product_id: int | None = None,
    canonical_id: int | None = None,
) -> None:
    """Set the category of exactly one of a chain product or a canonical product."""
    if (chain_product_id is None) == (canonical_id is None):
        raise ValueError("pass exactly one of chain_product_id / canonical_id")
    cat_id = category_id_for_slug(conn, slug)
    if chain_product_id is not None:
        cur = conn.execute(
            "UPDATE chain_product SET category_id = ? WHERE id = ?", (cat_id, chain_product_id)
        )
    else:
        cur = conn.execute(
            "UPDATE canonical_product SET category_id = ? WHERE id = ?", (cat_id, canonical_id)
        )
    if cur.rowcount == 0:
        raise ValueError("target product not found")
