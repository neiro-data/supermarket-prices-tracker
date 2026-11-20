import sqlite3

from typer.testing import CliRunner

from price_tracker.cli import app
from price_tracker.parsing.service import parse_source_file
from tests.parsing.test_review_cli import add_source, cli_db  # noqa: F401

runner = CliRunner()


def test_reparse_replaces_lines_and_skips_confirmed(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    a = parse_source_file(cli_db, add_source(cli_db, "lidl", 90.0))
    b = parse_source_file(cli_db, add_source(cli_db, "aldi_sued", 90.0))
    cli_db.execute("UPDATE receipt_line SET line_total_cents = 1 WHERE receipt_id = ?", (a,))
    cli_db.execute("UPDATE receipt SET status = 'confirmed' WHERE id = ?", (b,))
    cli_db.execute("UPDATE receipt_line SET line_total_cents = 1 WHERE receipt_id = ?", (b,))
    res = runner.invoke(app, ["review", "reparse", "--all"])
    assert res.exit_code == 0 and "reparsed=1 skipped_confirmed=1" in res.output
    total_a = cli_db.execute(
        "SELECT SUM(line_total_cents) AS s FROM receipt_line WHERE receipt_id = ?", (a,)
    ).fetchone()["s"]
    assert total_a == 529
    assert (
        cli_db.execute(
            "SELECT MAX(line_total_cents) AS m FROM receipt_line WHERE receipt_id = ?", (b,)
        ).fetchone()["m"]
        == 1
    )
    res = runner.invoke(app, ["review", "reparse", str(b), "--include-confirmed"])
    assert "reparsed=1" in res.output


def test_reparse_requires_selector(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    assert runner.invoke(app, ["review", "reparse"]).exit_code == 2


def test_reparse_is_idempotent(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    rid = parse_source_file(cli_db, add_source(cli_db, "rewe_ebon", 90.0))
    again = parse_source_file(cli_db, 1)
    assert again == rid
    assert cli_db.execute("SELECT COUNT(*) AS n FROM receipt_line").fetchone()["n"] == 7
    assert cli_db.execute("SELECT COUNT(*) AS n FROM chain_product").fetchone()["n"] == 4
