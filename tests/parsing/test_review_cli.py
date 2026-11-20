import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from price_tracker.cli import app
from price_tracker.parsing.service import parse_source_file

FIXTURES = Path(__file__).parent.parent / "fixtures" / "receipts"
runner = CliRunner()


def add_source(db: sqlite3.Connection, name: str, ocr: float | None = None) -> int:
    cur = db.execute(
        "INSERT INTO source_file (sha256, original_name, archive_path, media_type, "
        "extraction_method, ocr_confidence, raw_text, ingested_at) "
        "VALUES (?, ?, 'x', 'image', 'ocr', ?, ?, '2026-01-01T00:00:00')",
        (name, name, ocr, (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")),
    )
    return int(cur.lastrowid or 0)


@pytest.fixture
def cli_db(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> sqlite3.Connection:
    path = db.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.setenv("PTRACK_DB_PATH", path)
    monkeypatch.chdir(tmp_path)
    return db


def test_parse_stores_receipt_lines_and_products(cli_db: sqlite3.Connection) -> None:
    rid = parse_source_file(cli_db, add_source(cli_db, "rewe_ebon", 90.0))
    r = cli_db.execute("SELECT * FROM receipt WHERE id = ?", (rid,)).fetchone()
    assert (r["status"], r["total_cents"], r["lines_sum_cents"]) == ("parsed", 765, 765)
    assert r["store_id"] is not None
    n_products = cli_db.execute("SELECT COUNT(*) AS n FROM chain_product").fetchone()["n"]
    assert n_products == 4  # items only
    deposits = cli_db.execute(
        "SELECT COUNT(*) AS n FROM receipt_line "
        "WHERE line_type LIKE 'deposit%' AND chain_product_id IS NULL"
    ).fetchone()["n"]
    assert deposits == 2


def test_set_line_accept_flow(cli_db: sqlite3.Connection) -> None:
    rid = parse_source_file(cli_db, add_source(cli_db, "ocr_noisy_lidl", 85.0))
    assert cli_db.execute("SELECT status FROM receipt").fetchone()["status"] == "needs_review"
    assert runner.invoke(app, ["review", "accept", str(rid)]).exit_code == 1
    res = runner.invoke(
        app, ["review", "add-line", str(rid), "--name", "Milbona Joghurt", "--total", "0,49"]
    )
    assert res.exit_code == 0, res.output
    assert runner.invoke(app, ["review", "set", str(rid), "--total", "5,20"]).exit_code == 0
    res = runner.invoke(app, ["review", "accept", str(rid)])
    assert res.exit_code == 0, res.output
    assert cli_db.execute("SELECT status FROM receipt").fetchone()["status"] == "confirmed"


def test_line_edit_and_delete(cli_db: sqlite3.Connection) -> None:
    rid = parse_source_file(cli_db, add_source(cli_db, "lidl", 90.0))
    res = runner.invoke(app, ["review", "line", str(rid), "2", "--total", "0,59", "--qty", "2"])
    assert res.exit_code == 0, res.output
    row = cli_db.execute(
        "SELECT line_total_cents, quantity_milli FROM receipt_line WHERE line_no = 2"
    ).fetchone()
    assert (row["line_total_cents"], row["quantity_milli"]) == (59, 2000)
    assert runner.invoke(app, ["review", "delete-line", str(rid), "4"]).exit_code == 0
    discount = cli_db.execute(
        "SELECT applies_to_line_no FROM receipt_line WHERE line_no = 7"
    ).fetchone()
    assert discount["applies_to_line_no"] is None
    sums = cli_db.execute("SELECT lines_sum_cents FROM receipt").fetchone()
    assert sums["lines_sum_cents"] == 529 + 10 - 358


def test_force_accept_and_invalid_input(cli_db: sqlite3.Connection) -> None:
    rid = parse_source_file(cli_db, add_source(cli_db, "ocr_noisy_lidl", 85.0))
    assert runner.invoke(app, ["review", "accept", str(rid), "--force"]).exit_code == 0
    assert runner.invoke(app, ["review", "set", str(rid), "--total", "abc"]).exit_code == 2
    assert runner.invoke(app, ["review", "show", "999"]).exit_code == 1


def test_list_and_show(cli_db: sqlite3.Connection) -> None:
    rid = parse_source_file(cli_db, add_source(cli_db, "aldi_sued", 90.0))
    assert "aldi_sued" in runner.invoke(app, ["review", "list"]).output
    out = runner.invoke(app, ["review", "show", str(rid)]).output
    assert "Mineralwasser" in out and "raw text" in out
