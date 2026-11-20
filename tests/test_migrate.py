import sqlite3
from pathlib import Path

from price_tracker.db.connection import connect
from price_tracker.db.migrate import apply_migrations


def test_apply_and_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    assert apply_migrations(conn) == [1, 2, 3]
    assert apply_migrations(conn) == []
    versions = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == [1, 2, 3]


def test_foreign_keys_on(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_view_folds_discount_and_price_per_base(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO source_file (id, sha256, original_name, archive_path, media_type, "
        "extraction_method, raw_text, ingested_at) VALUES (1, 'x', 'a', 'a', 'pdf', "
        "'text_layer', '', '2024-01-01T00:00:00')"
    )
    db.execute(
        "INSERT INTO receipt (id, source_file_id, parser_profile, parse_confidence, status, "
        "created_at, updated_at) VALUES (1, 1, 'generic', 1.0, 'parsed', 'n', 'n')"
    )
    db.execute(
        "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, line_total_cents, "
        "base_quantity_milli) VALUES (1, 1, 'A', 'item', 200, 500)"
    )
    db.execute(
        "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, line_total_cents, "
        "applies_to_line_no) VALUES (1, 2, 'D', 'discount', -50, 1)"
    )
    row = db.execute(
        "SELECT effective_total_cents, price_per_base_cents FROM v_line_effective"
    ).fetchall()
    assert len(row) == 1
    assert row[0]["effective_total_cents"] == 150
    assert row[0]["price_per_base_cents"] == 300
