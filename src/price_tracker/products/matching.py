"""Chain product -> canonical product matching: alias rules, EAN link, fuzzy suggestions, assign."""

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from rapidfuzz import fuzz, process

from price_tracker.products.normalize import PACK_SEP, key_name, normalize_name
from price_tracker.products.units import PackSize, base_quantity_for

MatchSource = Literal["rule", "fuzzy", "manual", "ean"]
_SAME_CATEGORY_BOOST = 5


@dataclass(frozen=True, slots=True)
class Suggestion:
    canonical_id: int
    name: str
    score: int
    via: str  # "canonical" or "chain_product"


@dataclass(frozen=True, slots=True)
class MatchStats:
    by_rule: int = 0
    by_ean: int = 0
    unmatched: int = 0
    lines_backfilled: int = 0


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _chain_product(conn: sqlite3.Connection, chain_product_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, chain_id, normalized_name, ean, category_id, canonical_product_id, "
        "pack_size_milli, pack_unit FROM chain_product WHERE id = ?",
        (chain_product_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no chain_product {chain_product_id}")
    return row  # type: ignore[no-any-return]


def _rule_matches(match_type: str, pattern: str, name: str) -> bool:
    if match_type == "exact":
        # a pack-less pattern covers every pack size; `x #330ml` only that one
        return name == pattern or (PACK_SEP not in pattern and key_name(name) == pattern)
    if match_type == "prefix":
        return name.startswith(pattern)
    try:
        return re.fullmatch(pattern, name) is not None
    except re.error:
        return False


def apply_rules(conn: sqlite3.Connection, chain_product_id: int) -> int | None:
    """First matching alias rule (chain-specific before global, then priority) -> canonical id.

    Rules match the identity key (`name #pack`): `prefix`/pack-less `exact` patterns cover all
    pack sizes, a pattern containing `#<pack>` only that pack. Category-only rules set the chain
    product category when it has none.
    """
    cp = _chain_product(conn, chain_product_id)
    rules = conn.execute(
        "SELECT pattern, match_type, canonical_product_id, category_id FROM alias_rule "
        "WHERE chain_id = ? OR chain_id IS NULL "
        "ORDER BY CASE WHEN chain_id IS NULL THEN 1 ELSE 0 END, priority, id",
        (cp["chain_id"],),
    ).fetchall()
    for rule in rules:
        if not _rule_matches(rule["match_type"], rule["pattern"], cp["normalized_name"]):
            continue
        if rule["category_id"] is not None and cp["category_id"] is None:
            conn.execute(
                "UPDATE chain_product SET category_id = ? WHERE id = ?",
                (rule["category_id"], chain_product_id),
            )
        if rule["canonical_product_id"] is not None:
            return int(rule["canonical_product_id"])
    return None


def link_by_ean(conn: sqlite3.Connection, chain_product_id: int) -> int | None:
    """Canonical id of another chain product carrying the same EAN."""
    cp = _chain_product(conn, chain_product_id)
    if not cp["ean"]:
        return None
    row = conn.execute(
        "SELECT canonical_product_id FROM chain_product "
        "WHERE ean = ? AND id <> ? AND canonical_product_id IS NOT NULL "
        "ORDER BY id LIMIT 1",
        (cp["ean"], chain_product_id),
    ).fetchone()
    return int(row["canonical_product_id"]) if row else None


def suggest(
    conn: sqlite3.Connection, chain_product_id: int, limit: int = 3, threshold: int = 80
) -> list[Suggestion]:
    """Fuzzy candidates from canonical names and already-mapped chain product names."""
    cp = _chain_product(conn, chain_product_id)
    rejected = {
        int(r["canonical_product_id"])
        for r in conn.execute(
            "SELECT canonical_product_id FROM match_rejection WHERE chain_product_id = ?",
            (chain_product_id,),
        )
    }
    # (text, canonical_id, display name, via, category_id)
    cands: list[tuple[str, int, str, str, int | None]] = []
    for r in conn.execute("SELECT id, name, category_id FROM canonical_product ORDER BY id"):
        cands.append((normalize_name(r["name"]), r["id"], r["name"], "canonical", r["category_id"]))
    for r in conn.execute(
        "SELECT cp.normalized_name, cp.canonical_product_id AS cid, c.name, "
        "COALESCE(cp.category_id, c.category_id) AS category_id "
        "FROM chain_product AS cp INNER JOIN canonical_product AS c "
        "ON c.id = cp.canonical_product_id WHERE cp.id <> ? ORDER BY cp.id",
        (chain_product_id,),
    ):
        cands.append(
            (key_name(r["normalized_name"]), r["cid"], r["name"], "chain_product", r["category_id"])
        )
    cands = [c for c in cands if c[1] not in rejected]
    if not cands:
        return []
    my_category = cp["category_id"]
    hits = process.extract(
        key_name(cp["normalized_name"]),
        [c[0] for c in cands],
        scorer=fuzz.token_set_ratio,
        limit=None,
        score_cutoff=max(threshold - _SAME_CATEGORY_BOOST, 0),
    )
    best: dict[int, Suggestion] = {}
    for _text, raw_score, idx in hits:
        _t, cid, name, via, cat = cands[idx]
        score = int(round(raw_score))
        if my_category is not None and cat == my_category:
            score = min(100, score + _SAME_CATEGORY_BOOST)
        if score < threshold:
            continue
        if cid not in best or score > best[cid].score:
            best[cid] = Suggestion(cid, name, score, via)
    ranked = sorted(best.values(), key=lambda s: (-s.score, s.via != "canonical", s.canonical_id))
    return ranked[:limit]


def backfill_base_quantities(conn: sqlite3.Connection, chain_product_id: int | None = None) -> int:
    """Fill `receipt_line.base_quantity_milli` for mapped chain products; return lines updated."""
    sql = (
        "SELECT l.id, l.quantity_milli, l.quantity_unit, cp.pack_size_milli, cp.pack_unit, "
        "cp.is_weighed, c.base_unit FROM receipt_line AS l "
        "INNER JOIN chain_product AS cp ON cp.id = l.chain_product_id "
        "INNER JOIN canonical_product AS c ON c.id = cp.canonical_product_id "
        "WHERE l.line_type = 'item'"
    )
    params: tuple[int, ...] = ()
    if chain_product_id is None:
        sql += " AND l.base_quantity_milli IS NULL"
    else:
        sql += " AND cp.id = ?"
        params = (chain_product_id,)
    updated = 0
    for r in conn.execute(sql, params).fetchall():
        pack = (
            PackSize(r["pack_size_milli"], r["pack_unit"])
            if r["pack_size_milli"] is not None and r["pack_unit"] is not None
            else None
        )
        if r["is_weighed"] and r["quantity_unit"] == "pc":
            qty = None
        else:
            qty = base_quantity_for(r["quantity_milli"], r["quantity_unit"], pack, r["base_unit"])
        conn.execute("UPDATE receipt_line SET base_quantity_milli = ? WHERE id = ?", (qty, r["id"]))
        updated += qty is not None
    return updated


def assign(
    conn: sqlite3.Connection,
    chain_product_id: int,
    canonical_id: int,
    source: MatchSource,
    create_rule: bool,
) -> None:
    """Map a chain product to a canonical product; backfill line base quantities.

    Does not open a transaction: callers wrap multi-step writes.
    """
    cp = _chain_product(conn, chain_product_id)
    if (
        conn.execute("SELECT 1 FROM canonical_product WHERE id = ?", (canonical_id,)).fetchone()
        is None
    ):
        raise ValueError(f"no canonical_product {canonical_id}")
    conn.execute(
        "UPDATE chain_product SET canonical_product_id = ?, match_source = ? WHERE id = ?",
        (canonical_id, source, chain_product_id),
    )
    conn.execute(
        "DELETE FROM match_rejection WHERE chain_product_id = ? AND canonical_product_id = ?",
        (chain_product_id, canonical_id),
    )
    backfill_base_quantities(conn, chain_product_id)
    if create_rule:
        conn.execute(
            "INSERT INTO alias_rule (chain_id, pattern, match_type, canonical_product_id, "
            "priority, created_at) VALUES (?, ?, 'exact', ?, 100, ?) "
            "ON CONFLICT (chain_id, pattern, match_type) "
            "DO UPDATE SET canonical_product_id = excluded.canonical_product_id",
            (cp["chain_id"], cp["normalized_name"], canonical_id, _now()),
        )


def reject(conn: sqlite3.Connection, chain_product_id: int, canonical_id: int) -> None:
    """Record 'not the same product' so it is never suggested again."""
    conn.execute(
        "INSERT OR IGNORE INTO match_rejection "
        "(chain_product_id, canonical_product_id, created_at) "
        "VALUES (?, ?, ?)",
        (chain_product_id, canonical_id, _now()),
    )


def auto_match_all(conn: sqlite3.Connection) -> MatchStats:
    """Map unmapped chain products by alias rules, then EAN; backfill missing base quantities."""
    by_rule = by_ean = unmatched = 0
    ids = [
        int(r["id"])
        for r in conn.execute(
            "SELECT id FROM chain_product WHERE canonical_product_id IS NULL ORDER BY id"
        )
    ]
    for cp_id in ids:
        if (cid := apply_rules(conn, cp_id)) is not None:
            assign(conn, cp_id, cid, "rule", create_rule=False)
            by_rule += 1
        elif (cid := link_by_ean(conn, cp_id)) is not None:
            assign(conn, cp_id, cid, "ean", create_rule=False)
            by_ean += 1
        else:
            unmatched += 1
    return MatchStats(by_rule, by_ean, unmatched, backfill_base_quantities(conn))
