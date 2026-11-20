import sqlite3

import pytest


def _receipt(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO source_file (id, sha256, original_name, archive_path, media_type, "
        "extraction_method, raw_text, ingested_at) VALUES (1, 'x', 'a', 'a', 'pdf', "
        "'text_layer', '', '2024-01-01T00:00:00')"
    )
    db.execute(
        "INSERT INTO receipt (id, source_file_id, parser_profile, parse_confidence, status, "
        "created_at, updated_at) VALUES (1, 1, 'generic', 1.0, 'parsed', 'n', 'n')"
    )


def _line(
    db: sqlite3.Connection,
    no: int,
    kind: str,
    total: int,
    base: int | None = None,
    applies: int | None = None,
) -> None:
    db.execute(
        "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, line_total_cents, "
        "base_quantity_milli, applies_to_line_no) VALUES (1, ?, 'x', ?, ?, ?, ?)",
        (no, kind, total, base, applies),
    )


def _rows(db: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    cur = db.execute(
        "SELECT line_no, effective_total_cents, price_per_base_cents FROM v_line_effective"
    )
    return {r["line_no"]: r for r in cur}


def test_multiple_discounts_fold_and_unlinked_ignored(db: sqlite3.Connection) -> None:
    _receipt(db)
    _line(db, 1, "item", 500)
    _line(db, 2, "item", 300)
    _line(db, 3, "discount", -50, applies=1)
    _line(db, 4, "discount", -25, applies=1)
    _line(db, 5, "discount", -99)  # unlinked: must not affect anything
    rows = _rows(db)
    assert set(rows) == {1, 2}  # only items are listed
    assert rows[1]["effective_total_cents"] == 425
    assert rows[2]["effective_total_cents"] == 300


def test_discount_not_leaked_across_receipts(db: sqlite3.Connection) -> None:
    _receipt(db)
    db.execute(
        "INSERT INTO source_file (id, sha256, original_name, archive_path, media_type, "
        "extraction_method, raw_text, ingested_at) VALUES (2, 'y', 'b', 'b', 'pdf', "
        "'text_layer', '', '2024-01-01T00:00:00')"
    )
    db.execute(
        "INSERT INTO receipt (id, source_file_id, parser_profile, parse_confidence, status, "
        "created_at, updated_at) VALUES (2, 2, 'generic', 1.0, 'parsed', 'n', 'n')"
    )
    _line(db, 1, "item", 500)
    db.execute(
        "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, line_total_cents, "
        "applies_to_line_no) VALUES (2, 2, 'D', 'discount', -50, 1)"
    )
    assert _rows(db)[1]["effective_total_cents"] == 500


@pytest.mark.parametrize(
    ("total", "base", "expected"),
    [
        (100, 300, 333),  # 333.33 rounds down
        (200, 300, 667),  # 666.67 rounds up
        (1, 2000, 1),  # 0.5 rounds half up
        (299, 1000, 299),
    ],
)
def test_price_per_base_integer_rounding(
    db: sqlite3.Connection, total: int, base: int, expected: int
) -> None:
    _receipt(db)
    _line(db, 1, "item", total, base=base)
    value = _rows(db)[1]["price_per_base_cents"]
    assert type(value) is int
    assert value == expected


@pytest.mark.parametrize("base", [None, 0])
def test_price_per_base_null_without_base_quantity(
    db: sqlite3.Connection, base: int | None
) -> None:
    _receipt(db)
    _line(db, 1, "item", 200, base=base)
    assert _rows(db)[1]["price_per_base_cents"] is None
