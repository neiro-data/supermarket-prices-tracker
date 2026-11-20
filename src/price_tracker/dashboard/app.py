"""Streamlit dashboard over the analytics layer. Read-only; no SQL here."""

import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Filters:
    """Sidebar selection. `chain_codes` is None when every chain is selected."""

    since: date | None
    until: date | None
    chain_codes: list[str] | None
    chain_names: list[str]

    def kw(self) -> dict[str, object]:
        """Keyword arguments shared by the range-aware queries."""
        return {"since": self.since, "until": self.until, "chains": self.chain_codes}


def _sidebar(db_path: str) -> Filters:
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
    chains = run_query(db_path, "spend_by_chain")
    by_name = dict(zip(chains["chain_name"].to_list(), chains["chain_code"].to_list(), strict=True))
    names = st.sidebar.multiselect("Chains", list(by_name), default=list(by_name))
    codes = None if len(names) == len(by_name) else [by_name[n] for n in names]
    return Filters(since, until, codes, names)


def _overview(db_path: str, f: Filters) -> None:
    by_chain = run_query(db_path, "spend_by_chain", **f.kw())
    spend = int(by_chain["spend_cents"].sum() or 0)
    trips = int(by_chain["receipts"].sum() or 0)
    c1, c2, c3 = st.columns(3)
    c1.metric("Total spend", format_eur(spend))
    c2.metric("Trips", trips)
    c3.metric("Avg basket", format_eur(spend // trips if trips else 0))
    period = st.radio("Period", ["month", "week"], horizontal=True)
    over_time = run_query(db_path, "spend_over_time", period=period, **f.kw())
    if over_time.height:
        st.plotly_chart(charts.spend_over_time_chart(over_time, period))


def _categories(db_path: str, f: Filters) -> None:
    deposits = st.checkbox("Include deposits")
    top = run_query(db_path, "spend_by_category", level=1, include_deposits=deposits, **f.kw())
    if top.height:
        st.plotly_chart(charts.spend_by_category_chart(top))
    sub = run_query(db_path, "spend_by_category", level=2, include_deposits=deposits, **f.kw())
    st.subheader("Level-2 drill-down")
    _show(sub)


def _chains(db_path: str, f: Filters) -> None:
    df = run_query(db_path, "spend_by_chain", **f.kw())
    if df.height:
        st.plotly_chart(charts.spend_by_chain_chart(df))
    _show(df)
    st.subheader("Per store")
    _show(run_query(db_path, "spend_by_store", **f.kw()))


def _habits(db_path: str, f: Filters) -> None:
    profile = run_query(db_path, "weekday_profile", **f.kw())
    if profile.height:
        st.plotly_chart(charts.weekday_heatmap_chart(profile))
        st.plotly_chart(charts.weekday_profile_chart(profile))
    freq = run_query(db_path, "shopping_frequency", period="week", **f.kw())
    if freq.height:
        st.metric("Avg days between trips", float(freq["avg_days_between"][0] or 0))
        st.plotly_chart(charts.shopping_frequency_chart(freq))
    baskets = run_query(db_path, "basket_size", **f.kw())
    if baskets.height:
        st.plotly_chart(charts.basket_size_chart(baskets))
    top = run_query(db_path, "top_products", n=15, by="spend", **f.kw())
    if top.height:
        st.plotly_chart(charts.top_products_chart(top))


def _price_tracker(db_path: str, f: Filters) -> None:
    products = run_query(db_path, "canonical_products")
    if not products.height:
        st.info("No canonical products yet.")
        return
    ids, labels = products["canonical_id"].to_list(), products["product_name"].to_list()
    names = dict(zip(ids, labels, strict=True))
    cid = st.selectbox("Canonical product", list(names), format_func=lambda i: names[i])
    df = run_query(db_path, "price_evolution", canonical_id=cid)
    countries = sorted(df["store_country"].unique().to_list())
    picked = st.multiselect("Countries", countries, default=countries)
    df = df.filter(
        pl.col("chain_name").is_in(f.chain_names) & pl.col("store_country").is_in(picked)
    )
    if df.height:
        st.plotly_chart(charts.price_evolution_chart(df))
    else:
        st.info("No price data for this selection.")
    _show(df)


def _cheapest(db_path: str, f: Filters) -> None:
    df = run_query(db_path, "cheapest_chain", since=f.since)
    names = f.chain_names
    df = df.filter(pl.col("cheapest_chain").is_in(names) | pl.col("priciest_chain").is_in(names))
    if df.height:
        st.plotly_chart(charts.cheapest_chain_chart(df))
    _show(df)


def main() -> None:
    """Render the dashboard."""
    st.set_page_config(page_title="Supermarket prices", layout="wide")
    db_path = os.getenv("PTRACK_DB_PATH", "data/prices.db")
    f = _sidebar(db_path)
    page = st.sidebar.radio("Page", PAGES)
    st.title(page)
    if page == "Overview":
        _overview(db_path, f)
    elif page == "Categories":
        _categories(db_path, f)
    elif page == "Chains & stores":
        _chains(db_path, f)
    elif page == "Habits":
        _habits(db_path, f)
    elif page == "Price tracker":
        _price_tracker(db_path, f)
    else:
        _cheapest(db_path, f)


main()
