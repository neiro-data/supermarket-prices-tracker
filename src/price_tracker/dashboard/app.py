"""Streamlit dashboard over the analytics layer. Read-only; no SQL here."""

import os
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import streamlit as st

from price_tracker.analytics import charts, queries
from price_tracker.analytics.engine import connect_analytics
from price_tracker.domain.money import format_eur

PAGES = ("Overview", "Categories", "Chains & stores", "Habits", "Price tracker", "Cheapest chain")


@st.cache_resource
def get_connection(db_path: str) -> duckdb.DuckDBPyConnection:
    """One read-only analytics connection per DB path."""
    return connect_analytics(Path(db_path))


@st.cache_data(ttl=60)
def run_query(db_path: str, name: str, **kwargs: object) -> pl.DataFrame:
    """Run `queries.<name>` against the cached connection; results cached for 60 s."""
    result: pl.DataFrame = getattr(queries, name)(get_connection(db_path), **kwargs)
    return result


def _show(df: pl.DataFrame) -> None:
    st.dataframe(df, use_container_width=True)


def _sidebar(db_path: str) -> tuple[date | None, date | None, list[str]]:
    baskets = run_query(db_path, "basket_size")
    st.sidebar.header("Filters")
    since: date | None = None
    until: date | None = None
    if baskets.height:
        lo: date = baskets["purchased_on"].min()  # type: ignore[assignment]
        hi: date = baskets["purchased_on"].max()  # type: ignore[assignment]
        picked = st.sidebar.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi)
        if isinstance(picked, tuple) and len(picked) == 2:
            since, until = picked
    all_chains = run_query(db_path, "spend_by_chain")["chain_name"].to_list()
    chains = st.sidebar.multiselect("Chains", all_chains, default=all_chains)
    return since, until, chains


def _overview(db_path: str, since: date | None, until: date | None, chains: list[str]) -> None:
    by_chain = run_query(db_path, "spend_by_chain", since=since, until=until).filter(
        pl.col("chain_name").is_in(chains)
    )
    spend = int(by_chain["spend_cents"].sum() or 0)
    trips = int(by_chain["receipts"].sum() or 0)
    c1, c2, c3 = st.columns(3)
    c1.metric("Total spend", format_eur(spend))
    c2.metric("Trips", trips)
    c3.metric("Avg basket", format_eur(spend // trips if trips else 0))
    period = st.radio("Period", ["month", "week"], horizontal=True)
    over_time = run_query(db_path, "spend_over_time", period=period, since=since, until=until)
    if over_time.height:
        st.plotly_chart(charts.spend_over_time_chart(over_time, period))
        st.caption("Spend over time covers all chains.")


def _categories(db_path: str, since: date | None, until: date | None) -> None:
    deposits = st.checkbox("Include deposits")
    top = run_query(
        db_path, "spend_by_category", level=1, since=since, until=until, include_deposits=deposits
    )
    if top.height:
        st.plotly_chart(charts.spend_by_category_chart(top))
    sub = run_query(
        db_path, "spend_by_category", level=2, since=since, until=until, include_deposits=deposits
    )
    st.subheader("Level-2 drill-down")
    _show(sub)


def _chains(db_path: str, since: date | None, until: date | None, chains: list[str]) -> None:
    df = run_query(db_path, "spend_by_chain", since=since, until=until).filter(
        pl.col("chain_name").is_in(chains)
    )
    if df.height:
        st.plotly_chart(charts.spend_by_chain_chart(df))
    _show(df)


def _habits(db_path: str) -> None:
    profile = run_query(db_path, "weekday_profile")
    if profile.height:
        st.plotly_chart(charts.weekday_heatmap_chart(profile))
        st.plotly_chart(charts.weekday_profile_chart(profile))
    freq = run_query(db_path, "shopping_frequency", period="week")
    if freq.height:
        st.metric("Avg days between trips", float(freq["avg_days_between"][0] or 0))
        st.plotly_chart(charts.shopping_frequency_chart(freq))
    baskets = run_query(db_path, "basket_size")
    if baskets.height:
        st.plotly_chart(charts.basket_size_chart(baskets))
    top = run_query(db_path, "top_products", n=15, by="spend")
    if top.height:
        st.plotly_chart(charts.top_products_chart(top))


def _price_tracker(db_path: str, chains: list[str]) -> None:
    products = run_query(db_path, "canonical_products")
    if not products.height:
        st.info("No canonical products yet.")
        return
    names = dict(
        zip(products["canonical_id"].to_list(), products["product_name"].to_list(), strict=True)
    )
    cid = st.selectbox("Canonical product", list(names), format_func=lambda i: names[i])
    df = run_query(db_path, "price_evolution", canonical_id=cid)
    cities = sorted(df["store_city"].drop_nulls().unique().to_list())
    picked = st.multiselect("Cities", cities, default=cities)
    df = df.filter(pl.col("chain_name").is_in(chains) & pl.col("store_city").is_in(picked))
    if df.height:
        st.plotly_chart(charts.price_evolution_chart(df))
    else:
        st.info("No price data for this selection.")
    _show(df)


def _cheapest(db_path: str, since: date | None, chains: list[str]) -> None:
    df = run_query(db_path, "cheapest_chain", since=since)
    df = df.filter(pl.col("cheapest_chain").is_in(chains) | pl.col("priciest_chain").is_in(chains))
    if df.height:
        st.plotly_chart(charts.cheapest_chain_chart(df))
    _show(df)


def main() -> None:
    """Render the dashboard."""
    st.set_page_config(page_title="Supermarket prices", layout="wide")
    db_path = os.getenv("PTRACK_DB_PATH", "data/prices.db")
    since, until, chains = _sidebar(db_path)
    page = st.sidebar.radio("Page", PAGES)
    st.title(page)
    if page == "Overview":
        _overview(db_path, since, until, chains)
    elif page == "Categories":
        _categories(db_path, since, until)
    elif page == "Chains & stores":
        _chains(db_path, since, until, chains)
    elif page == "Habits":
        _habits(db_path)
    elif page == "Price tracker":
        _price_tracker(db_path, chains)
    else:
        _cheapest(db_path, since, chains)


main()
