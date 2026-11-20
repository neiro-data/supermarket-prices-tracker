import sqlite3

from price_tracker.products import catalog
from price_tracker.products.matching import (
    apply_rules,
    assign,
    auto_match_all,
    link_by_ean,
    reject,
    suggest,
)


def chain_id(db: sqlite3.Connection, code: str) -> int:
    return int(db.execute("SELECT id FROM chain WHERE code = ?", (code,)).fetchone()["id"])


def add_receipt(db: sqlite3.Connection, code: str) -> int:
    n = db.execute("SELECT COUNT(*) AS n FROM source_file").fetchone()["n"]
    sf = db.execute(
        "INSERT INTO source_file (sha256, original_name, archive_path, media_type, "
        "extraction_method, raw_text, ingested_at) "
        "VALUES (?, 'x', 'x', 'pdf', 'text_layer', '', 't')",
        (f"sha{n}",),
    ).lastrowid
    cur = db.execute(
        "INSERT INTO receipt (source_file_id, chain_id, parser_profile, parse_confidence, status, "
        "created_at, updated_at) VALUES (?, ?, 'x', 1, 'parsed', 't', 't')",
        (sf, chain_id(db, code)),
    )
    return int(cur.lastrowid or 0)


def add_cp(
    db: sqlite3.Connection,
    code: str,
    norm: str,
    pack: tuple[int, str] | None = None,
    ean: str | None = None,
) -> int:
    cur = db.execute(
        "INSERT INTO chain_product (chain_id, raw_name, normalized_name, pack_size_milli, "
        "pack_unit, ean) VALUES (?, ?, ?, ?, ?, ?)",
        (chain_id(db, code), norm.upper(), norm, pack[0] if pack else None,
         pack[1] if pack else None, ean),
    )  # fmt: skip
    return int(cur.lastrowid or 0)


def test_rule_priority_and_chain_specific_wins(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "kg")
    b = catalog.add_canonical(db, "B", "kg")
    c = catalog.add_canonical(db, "C", "kg")
    cp = add_cp(db, "rewe", "tafeltrauben hell")
    catalog.add_alias(db, "tafeltrauben", a, None, "prefix", 10)
    catalog.add_alias(db, "tafeltrauben", b, "rewe", "prefix", 50)
    assert apply_rules(db, cp) == b  # chain-specific beats lower-priority-number global
    catalog.add_alias(db, "tafeltrauben hell", c, "rewe", "exact", 5)
    assert apply_rules(db, cp) == c  # priority within chain-specific


def test_regex_rule_and_no_match(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "pc")
    cp = add_cp(db, "lidl", "milbona joghurt natur")
    catalog.add_alias(db, r"milbona .*", a, None, "regex", 100)
    assert apply_rules(db, cp) == a
    other = add_cp(db, "lidl", "bananen")
    assert apply_rules(db, other) is None


def test_ean_link(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "pc")
    one = add_cp(db, "rewe", "x1", ean="4000")
    two = add_cp(db, "lidl", "x2", ean="4000")
    assert link_by_ean(db, two) is None
    assign(db, one, a, "manual", False)
    assert link_by_ean(db, two) == a
    stats = auto_match_all(db)
    assert stats.by_ean == 1


def test_suggest_excludes_rejections(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "Weintrauben hell kernlos", "kg")
    cp = add_cp(db, "rewe", "tafeltrauben hell kernlos")
    top = suggest(db, cp)
    assert top and top[0].canonical_id == a and top[0].via == "canonical"
    reject(db, cp, a)
    assert suggest(db, cp) == []


def test_suggest_via_mapped_chain_product(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "Zzz", "pc")
    mapped = add_cp(db, "lidl", "milbona joghurt natur")
    assign(db, mapped, a, "manual", False)
    cp = add_cp(db, "rewe", "joghurt natur milbona")
    res = suggest(db, cp)
    assert [(s.canonical_id, s.via) for s in res] == [(a, "chain_product")]


def test_assign_backfills_and_creates_rule(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "Trauben", "kg")
    cp = add_cp(db, "lidl", "trauben", pack=(500, "kg"))
    rid = add_receipt(db, "lidl")
    db.execute(
        "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, chain_product_id, "
        "quantity_milli, quantity_unit, line_total_cents) VALUES (?, 1, 'x', 'item', ?, 1000, "
        "'pc', 179)",
        (rid, cp),
    )
    assign(db, cp, a, "manual", True)
    row = db.execute("SELECT base_quantity_milli AS b, price_per_base_cents AS p "
                     "FROM v_line_effective").fetchone()  # fmt: skip
    assert (row["b"], row["p"]) == (500, 358)
    rule = db.execute("SELECT pattern, match_type FROM alias_rule").fetchone()
    assert (rule["pattern"], rule["match_type"]) == ("trauben", "exact")
    # a second product with the same normalized name on a new import is auto-mapped
    db.execute("UPDATE chain_product SET canonical_product_id = NULL WHERE id = ?", (cp,))
    assert auto_match_all(db).by_rule == 1
