import sqlite3

from typer.testing import CliRunner

from price_tracker.cli import app
from price_tracker.parsing.service import parse_source_file
from tests.parsing.test_review_cli import add_source, cli_db  # noqa: F401
from tests.products.test_matching import add_cp

runner = CliRunner()


def test_review_accept_with_rule_then_reingest_auto_maps(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    parse_source_file(cli_db, add_source(cli_db, "lidl", 90.0))
    assert runner.invoke(app, ["canonical", "add", "Joghurt", "--unit", "pc"]).exit_code == 0
    keep = "(SELECT id FROM chain_product WHERE normalized_name <> 'milbona joghurt')"
    cli_db.execute(f"DELETE FROM receipt_line WHERE chain_product_id IN {keep}")  # noqa: S608
    cli_db.execute("DELETE FROM chain_product WHERE normalized_name <> 'milbona joghurt'")
    res = runner.invoke(app, ["match", "review"], input="1\ny\n")
    assert res.exit_code == 0, res.output
    cp = cli_db.execute("SELECT canonical_product_id AS c, match_source AS s FROM chain_product")
    row = cp.fetchone()
    assert (row["c"], row["s"]) == (1, "fuzzy")
    # same name on a newly ingested receipt is mapped by the remembered rule
    cli_db.execute("DELETE FROM receipt")
    cli_db.execute("DELETE FROM chain_product")
    parse_source_file(cli_db, 1)
    mapped = cli_db.execute(
        "SELECT match_source FROM chain_product WHERE normalized_name = 'milbona joghurt'"
    ).fetchone()
    assert mapped["match_source"] == "rule"


def test_review_new_canonical_and_reject(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    cp = add_cp(cli_db, "rewe", "bananen", pack=None)
    res = runner.invoke(app, ["match", "review"], input="n\nBananen\nkg\nobst\nn\n")
    assert res.exit_code == 0, res.output
    row = cli_db.execute("SELECT canonical_product_id AS c FROM chain_product WHERE id = ?", (cp,))
    assert row.fetchone()["c"] is not None


def test_crud_commands(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    assert runner.invoke(
        app, ["categories", "add", "x-top", "--name-de", "X", "--name-en", "X"]
    ).exit_code == 0  # fmt: skip
    bad = runner.invoke(
        app, ["categories", "add", "x-sub", "--name-de", "X", "--name-en", "X", "--parent", "obst"]
    )
    assert bad.exit_code == 1
    assert runner.invoke(app, ["canonical", "add", "A", "--unit", "kg"]).exit_code == 0
    assert runner.invoke(app, ["canonical", "add", "B", "--unit", "kg"]).exit_code == 0
    assert runner.invoke(app, ["canonical", "rename", "1", "A2"]).exit_code == 0
    assert runner.invoke(app, ["alias", "add", "foo", "1", "--chain", "rewe"]).exit_code == 0
    assert runner.invoke(app, ["canonical", "merge", "1", "2"]).exit_code == 0
    assert cli_db.execute("SELECT canonical_product_id AS c FROM alias_rule").fetchone()["c"] == 2
    assert runner.invoke(app, ["alias", "delete", "1"]).exit_code == 0
    cp = add_cp(cli_db, "rewe", "foo")
    assert runner.invoke(app, ["products", "set-pack", str(cp), "500g"]).exit_code == 0
    assert runner.invoke(app, ["products", "set-ean", str(cp), "4006381333931"]).exit_code == 0
    assert runner.invoke(app, ["products", "list", "--unmatched"]).exit_code == 0
    assert runner.invoke(app, ["match", "run"]).exit_code == 0
    assert runner.invoke(app, ["categories", "list"]).exit_code == 0
    assert runner.invoke(app, ["categories", "reseed"]).exit_code == 0
    assert runner.invoke(app, ["canonical", "list"]).exit_code == 0
    assert runner.invoke(app, ["alias", "list"]).exit_code == 0
