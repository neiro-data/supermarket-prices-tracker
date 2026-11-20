import sqlite3

import pytest
from typer.testing import CliRunner

from price_tracker.cli import app
from price_tracker.domain.models import LineType
from price_tracker.parsing.service import get_or_create_chain_product, parse_source_file
from price_tracker.products import catalog
from price_tracker.products.categorize import line_category_slug
from price_tracker.products.matching import apply_rules, auto_match_all
from price_tracker.products.normalize import identity_key
from tests.parsing.test_review_cli import add_source, cli_db  # noqa: F401
from tests.products.test_matching import chain_id

runner = CliRunner()


def _cp(db: sqlite3.Connection, name: str, weighed: bool = False) -> int:
    return get_or_create_chain_product(db, chain_id(db, "rewe"), name, weighed)


def _n(db: sqlite3.Connection, table: str) -> int:
    return int(db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])  # noqa: S608


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Cola 0,33l", "Cola 330ml", True),
        ("Cola 0,33l", "Cola 33cl", True),
        ("Mehl 1kg", "Mehl 1000g", True),
        ("Cola 6x0,5l", "Cola 3l", True),
        ("Cola 6x0,5l", "Cola 0,5l", False),
        ("Eier 6er", "Eier 10er", False),
        ("Cola", "Cola 1l", False),
    ],
)
def test_pack_identity(db: sqlite3.Connection, a: str, b: str, same: bool) -> None:
    assert (_cp(db, a) == _cp(db, b)) is same
    assert (identity_key(a) == identity_key(b)) is same


def test_no_pack_name_has_no_pack_columns(db: sqlite3.Connection) -> None:
    cp = _cp(db, "Bananen")
    row = db.execute("SELECT * FROM chain_product WHERE id = ?", (cp,)).fetchone()
    assert row["normalized_name"] == "bananen"
    assert row["pack_size_milli"] is None


def test_multipack_stored_as_total(db: sqlite3.Connection) -> None:
    row = db.execute(
        "SELECT pack_size_milli AS p, pack_unit AS u FROM chain_product WHERE id = ?",
        (_cp(db, "Cola 6x0,5l"),),
    ).fetchone()
    assert (row["p"], row["u"]) == (3000, "l")


def test_alias_rule_pack_scoping(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "l")
    b = catalog.add_canonical(db, "B", "l")
    small, big = _cp(db, "Cola 0,33l"), _cp(db, "Cola 1,5l")
    catalog.add_alias(db, "cola #330ml", a, None, "exact", 1)
    assert apply_rules(db, small) == a
    assert apply_rules(db, big) is None
    catalog.add_alias(db, "col", b, None, "prefix", 5)
    assert apply_rules(db, big) == b  # prefix covers all packs
    assert apply_rules(db, small) == a  # lower priority number wins


def test_packless_exact_does_not_match_other_names(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "A", "l")
    catalog.add_alias(db, "cola", a, None, "exact", 1)
    assert apply_rules(db, _cp(db, "Cola Zero 1l")) is None
    assert apply_rules(db, _cp(db, "Cola 1l")) == a


def test_auto_match_idempotent_with_packs(db: sqlite3.Connection) -> None:
    a = catalog.add_canonical(db, "Cola", "l")
    catalog.add_alias(db, "cola", a, None, "exact", 1)
    ids = [_cp(db, "Cola 0,33l"), _cp(db, "Cola 1,5l"), _cp(db, "Zzz")]
    first = auto_match_all(db)
    second = auto_match_all(db)
    assert (first.by_rule, first.unmatched) == (2, 1)
    assert (second.by_rule, second.unmatched) == (0, 1)
    assert _n(db, "chain_product") == len(ids)


def test_reparse_creates_no_duplicate_chain_products(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    parse_source_file(cli_db, add_source(cli_db, "rewe_ebon", 90.0))
    before = _n(cli_db, "chain_product")
    for _ in range(2):
        parse_source_file(cli_db, 1)
    assert _n(cli_db, "chain_product") == before
    dup = cli_db.execute(
        "SELECT COUNT(*) AS n FROM (SELECT 1 FROM chain_product "
        "GROUP BY chain_id, normalized_name HAVING COUNT(*) > 1)"
    ).fetchone()["n"]
    assert dup == 0


def test_deposit_lines_no_chain_product_pfand(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    parse_source_file(cli_db, add_source(cli_db, "rewe_ebon", 90.0))
    rows = cli_db.execute(
        "SELECT line_type, chain_product_id FROM receipt_line "
        "WHERE line_type IN ('deposit', 'deposit_return')"
    ).fetchall()
    assert rows
    assert all(r["chain_product_id"] is None for r in rows)
    assert {line_category_slug(LineType(r["line_type"]), None) for r in rows} == {"pfand"}


def test_set_category_cli_end_to_end(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    cp = _cp(cli_db, "Foo")
    cid = catalog.add_canonical(cli_db, "Foo", "kg")
    r = runner.invoke(app, ["products", "set-category", str(cp), "obst"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["canonical", "set-category", str(cid), "pfand"])
    assert r.exit_code == 0, r.output
    q = "SELECT cat.slug AS s FROM {t} AS x JOIN category AS cat ON cat.id = x.category_id"
    assert cli_db.execute(q.format(t="chain_product")).fetchone()["s"] == "obst"
    assert cli_db.execute(q.format(t="canonical_product")).fetchone()["s"] == "pfand"
    assert runner.invoke(app, ["products", "set-category", str(cp), "nope"]).exit_code != 0
    assert runner.invoke(app, ["products", "set-category", "999", "obst"]).exit_code != 0


@pytest.mark.parametrize(
    "ean", ["4006381333932", "1234", "abcdefgh", "40063813339 1", "-4006381333931"]
)
def test_set_ean_cli_rejects_invalid(cli_db: sqlite3.Connection, ean: str) -> None:  # noqa: F811
    cp = _cp(cli_db, "Foo")
    r = runner.invoke(app, ["products", "set-ean", str(cp), ean])
    assert r.exit_code != 0
    assert cli_db.execute("SELECT ean FROM chain_product").fetchone()["ean"] is None


def test_set_ean_cli_accepts_valid_ean8_and_13(cli_db: sqlite3.Connection) -> None:  # noqa: F811
    cp = _cp(cli_db, "Foo")
    for ean in ("96385074", "4006381333931"):
        r = runner.invoke(app, ["products", "set-ean", str(cp), ean])
        assert r.exit_code == 0, r.output
    assert cli_db.execute("SELECT ean FROM chain_product").fetchone()["ean"] == "4006381333931"
