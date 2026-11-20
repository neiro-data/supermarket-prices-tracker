import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import pytest

from price_tracker.analytics import queries as q
from price_tracker.analytics.engine import RELATIONS, connect_analytics


@pytest.fixture
def con(analytics_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Fallback (sqlite3 -> polars) engine path."""
    c = connect_analytics(analytics_db, prefer_attach=False)
    yield c
    c.close()


def test_spend_over_time_month(con: duckdb.DuckDBPyConnection) -> None:
    df = q.spend_over_time(con, "month")
    assert df.rows() == [(date(2026, 1, 1), 5, 1625), (date(2026, 2, 1), 5, 1281)]


def test_spend_over_time_week_and_bounds(con: duckdb.DuckDBPyConnection) -> None:
    df = q.spend_over_time(con, "week", date(2026, 1, 12), date(2026, 1, 26))
    assert df.rows() == [
        (date(2026, 1, 12), 1, 135),
        (date(2026, 1, 19), 1, 380),
        (date(2026, 1, 26), 1, 495),
    ]


def test_spend_total_equals_receipt_totals(con: duckdb.DuckDBPyConnection) -> None:
    assert q.spend_over_time(con, "month")["spend_cents"].sum() == 2906


def test_spend_by_category_levels(con: duckdb.DuckDBPyConnection) -> None:
    lvl1 = q.spend_by_category(con, 1)
    assert lvl1.select("category_slug", "line_count", "spend_cents").rows() == [
        ("obst-gemuese", 5, 1485),
        ("molkerei-eier", 10, 1071),
        ("uncategorized", 2, 300),
    ]
    lvl2 = q.spend_by_category(con, 2)
    assert lvl2.select("category_slug", "spend_cents").rows() == [
        ("obst", 1485),
        ("milch", 1071),
        ("uncategorized", 300),
    ]


def test_spend_by_category_deposits(con: duckdb.DuckDBPyConnection) -> None:
    df = q.spend_by_category(con, 1, include_deposits=True)
    assert df.filter(df["category_slug"] == "deposit").rows()[0][2:] == (2, 50)
    assert df["spend_cents"].sum() == 2906


def test_spend_by_chain(con: duckdb.DuckDBPyConnection) -> None:
    df = q.spend_by_chain(con)
    assert df.select("chain_code", "receipts", "spend_cents").rows() == [
        ("rewe", 4, 1345),
        ("lidl", 4, 921),
        ("aldi_sued", 2, 640),
    ]


def test_shopping_frequency(con: duckdb.DuckDBPyConnection) -> None:
    df = q.shopping_frequency(con)
    assert df.height == 8
    assert df["trips"].to_list() == [2, 1, 1, 1, 1, 2, 1, 1]
    assert set(df["avg_days_between"].to_list()) == {5.78}  # 52 days / 9 gaps
    assert q.shopping_frequency(con, "month")["trips"].to_list() == [5, 5]


def test_weekday_profile(con: duckdb.DuckDBPyConnection) -> None:
    assert q.weekday_profile(con).rows() == [
        (1, "afternoon", 1, 380),
        (1, "evening", 2, 347),
        (1, "morning", 5, 1845),
        (3, "evening", 1, 240),
        (4, "morning", 1, 94),
    ]


def test_basket_size(con: duckdb.DuckDBPyConnection) -> None:
    df = q.basket_size(con)
    assert df["items"].to_list() == [2, 2, 1, 2, 2, 2, 2, 1, 2, 1]
    assert df["spend_cents"].to_list() == [375, 240, 135, 380, 495, 255, 260, 92, 580, 94]


def test_top_products(con: duckdb.DuckDBPyConnection) -> None:
    by_spend = q.top_products(con, 2, "spend")
    assert by_spend.rows() == [("Aepfel", 5, 1485), ("Milch 1L", 10, 1071)]
    by_count = q.top_products(con, 3, "count")
    assert by_count["product_name"].to_list() == ["Milch 1L", "Aepfel", "SNACK"]


def test_price_evolution(con: duckdb.DuckDBPyConnection) -> None:
    df = q.price_evolution(con, 2)  # canonical ids: 1 = milk, 2 = apples
    assert df.select("purchased_on", "chain_name", "store_city", "price_per_base_cents").rows() == [
        (date(2026, 1, 5), "REWE", "Berlin", 500),
        (date(2026, 1, 19), "ALDI SÜD", "Muenchen", 300),
        (date(2026, 1, 26), "Lidl", "Berlin", 500),
        (date(2026, 2, 9), "ALDI SÜD", "Muenchen", 350),
        (date(2026, 2, 16), "REWE", "Berlin", 600),
    ]
    assert set(df["base_unit"].to_list()) == {"kg"}
    assert q.price_evolution(con, 1)["price_per_base_cents"].to_list() == [
        100, 90, 110, 80, 95, 105, 85, 92, 110, 94,
    ]  # fmt: skip


def test_cheapest_chain(con: duckdb.DuckDBPyConnection) -> None:
    df = q.cheapest_chain(con, date(2026, 1, 1))
    assert df.select(
        "product_name",
        "n_chains",
        "cheapest_chain",
        "cheapest_cents",
        "priciest_chain",
        "priciest_cents",
        "delta_pct",
    ).rows() == [  # fmt: skip
        ("Aepfel", 3, "ALDI SÜD", 325, "REWE", 550, 40.9),
        ("Milch 1L", 3, "ALDI SÜD", 83, "REWE", 108, 23.1),
    ]


def test_cheapest_chain_since_filters_and_needs_two_chains(con: duckdb.DuckDBPyConnection) -> None:
    # Only 2026-02-10 onwards: apples only at REWE, milk at REWE and LIDL.
    df = q.cheapest_chain(con, date(2026, 2, 10))
    assert df["product_name"].to_list() == ["Milch 1L"]
    assert df["cheapest_chain"].to_list() == ["Lidl"]


def test_excluded_status_never_counted(con: duckdb.DuckDBPyConnection) -> None:
    assert 999 not in q.basket_size(con)["spend_cents"].to_list()
    assert q.top_products(con, 5, "spend").filter(pl.col("product_name") == "Milch 1L")[
        "spend_cents"
    ].to_list() == [1071]


def test_attach_path_matches_fallback(analytics_db: Path) -> None:
    try:
        con = connect_analytics(analytics_db, prefer_attach=True)
        con.execute("SELECT * FROM src.receipt LIMIT 0")
    except duckdb.Error:
        pytest.skip("duckdb sqlite extension unavailable")
    try:
        assert q.spend_over_time(con, "month").rows() == [
            (date(2026, 1, 1), 5, 1625),
            (date(2026, 2, 1), 5, 1281),
        ]
        assert q.spend_by_category(con, 1)["spend_cents"].to_list() == [1485, 1071, 300]
    finally:
        con.close()


def test_since_until_filters_category_and_chain(con: duckdb.DuckDBPyConnection) -> None:
    # 2026-02-01..2026-02-09 inclusive: REWE 02-02 (255), ALDI 02-09 (260), LIDL 02-09 (92).
    since, until = date(2026, 2, 1), date(2026, 2, 9)
    chain = q.spend_by_chain(con, since, until)
    assert chain.select("chain_code", "receipts", "spend_cents").rows() == [
        ("aldi_sued", 1, 260),
        ("rewe", 1, 255),
        ("lidl", 1, 92),
    ]
    cat = q.spend_by_category(con, 1, since, until)
    assert cat["spend_cents"].sum() == 255 + 260 + 92
    assert q.spend_by_chain(con, date(2027, 1, 1)).height == 0
    assert q.spend_by_category(con, 1, until=date(2025, 12, 31)).height == 0


def test_spend_over_time_since_only_and_until_only(con: duckdb.DuckDBPyConnection) -> None:
    assert q.spend_over_time(con, "month", since=date(2026, 2, 1)).rows() == [
        (date(2026, 2, 1), 5, 1281)
    ]
    assert q.spend_over_time(con, "month", until=date(2026, 1, 31)).rows() == [
        (date(2026, 1, 1), 5, 1625)
    ]


def test_deposits_excluded_by_default(con: duckdb.DuckDBPyConnection) -> None:
    assert "deposit" not in q.spend_by_category(con, 1)["category_slug"].to_list()
    assert "deposit" not in q.spend_by_category(con, 2)["category_slug"].to_list()


def test_cheapest_chain_default_window_is_90_days(con: duckdb.DuckDBPyConnection) -> None:
    # Fixture data is from early 2026; the default window is relative to today.
    cutoff = date.today().toordinal() - 90
    expected_empty = date(2026, 2, 26).toordinal() < cutoff
    df = q.cheapest_chain(con)
    if expected_empty:
        assert df.height == 0
    assert set(df.columns) >= {"product_name", "n_chains", "cheapest_chain", "delta_pct"}


def test_cheapest_chain_single_chain_excluded(con: duckdb.DuckDBPyConnection) -> None:
    assert q.cheapest_chain(con, date(2026, 2, 27)).height == 0  # one receipt left (LIDL)


def test_price_evolution_unknown_canonical_empty(con: duckdb.DuckDBPyConnection) -> None:
    assert q.price_evolution(con, 9999).height == 0


@pytest.fixture
def empty_con(db: sqlite3.Connection) -> Iterator[duckdb.DuckDBPyConnection]:
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    path = Path(db.execute("PRAGMA database_list").fetchone()["file"])
    c = connect_analytics(path, prefer_attach=False)
    yield c
    c.close()


def test_empty_db_all_queries_return_empty_frames(empty_con: duckdb.DuckDBPyConnection) -> None:
    c = empty_con
    frames = [
        q.spend_over_time(c, "week"),
        q.spend_over_time(c, "month"),
        q.spend_by_category(c, 1),
        q.spend_by_category(c, 2, include_deposits=True),
        q.spend_by_chain(c),
        q.shopping_frequency(c),
        q.weekday_profile(c),
        q.basket_size(c),
        q.top_products(c, 5, "spend"),
        q.top_products(c, 5, "count"),
        q.price_evolution(c, 1),
        q.cheapest_chain(c, date(2026, 1, 1)),
    ]
    assert all(isinstance(f, pl.DataFrame) and f.height == 0 for f in frames)


def test_needs_review_excluded_everywhere(con: duckdb.DuckDBPyConnection) -> None:
    # The excluded receipt (999c, lidl, 2026-02-27) must not leak into any aggregate.
    assert q.spend_by_chain(con)["spend_cents"].sum() == 2906
    assert q.spend_by_category(con, 1, include_deposits=True)["spend_cents"].sum() == 2906
    assert q.weekday_profile(con)["spend_cents"].sum() == 2906
    assert 999 not in q.price_evolution(con, 1)["price_per_base_cents"].to_list()
    assert q.basket_size(con).height == 10


def test_attach_and_fallback_expose_same_relations(analytics_db: Path) -> None:
    fb = connect_analytics(analytics_db, prefer_attach=False)
    try:
        con = connect_analytics(analytics_db, prefer_attach=True)
        con.execute("SELECT * FROM src.receipt LIMIT 0")
    except duckdb.Error:
        fb.close()
        pytest.skip("duckdb sqlite extension unavailable")
    try:
        for name in RELATIONS:
            a = con.execute(f"SELECT * FROM {name} ORDER BY ALL").fetchall()  # noqa: S608
            b = fb.execute(f"SELECT * FROM {name} ORDER BY ALL").fetchall()  # noqa: S608
            assert len(a) == len(b), name
        for fn in (q.spend_by_chain, q.weekday_profile, q.basket_size, q.shopping_frequency):
            assert fn(con).rows() == fn(fb).rows(), fn.__name__
    finally:
        con.close()
        fb.close()


def test_attach_and_fallback_price_per_base_agree(analytics_db: Path) -> None:
    fb = connect_analytics(analytics_db, prefer_attach=False)
    try:
        con = connect_analytics(analytics_db, prefer_attach=True)
        con.execute("SELECT * FROM src.receipt LIMIT 0")
    except duckdb.Error:
        fb.close()
        pytest.skip("duckdb sqlite extension unavailable")
    try:
        since = date(2026, 1, 1)
        assert q.cheapest_chain(con, since).rows() == q.cheapest_chain(fb, since).rows()
    finally:
        con.close()
        fb.close()


def test_empty_db_keeps_integer_dtypes(empty_con: duckdb.DuckDBPyConnection) -> None:
    c = empty_con
    frames = [
        q.spend_over_time(c, "month"),
        q.spend_by_category(c, 1),
        q.spend_by_chain(c),
        q.top_products(c, 5, "spend"),
        q.price_evolution(c, 1),
        q.cheapest_chain(c, date(2026, 1, 1)),
    ]
    for f in frames:
        assert f.height == 0
        for name, dtype in f.schema.items():
            if name.endswith("_cents"):
                assert dtype == pl.Int64, (name, dtype)
            assert not isinstance(dtype, pl.Decimal) and dtype != pl.Null, (name, dtype)
