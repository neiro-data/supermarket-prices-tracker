"""Plotly figures for each analytics query. Cents become euros here, and only here."""

import plotly.graph_objects as go
import polars as pl

# Okabe-Ito colorblind-safe palette, reused by every chart.
PALETTE: tuple[str, ...] = (
    "#0072B2",
    "#E69F00",
    "#009E73",
    "#D55E00",
    "#CC79A7",
    "#56B4E9",
    "#F0E442",
    "#000000",
)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _eur(values: list[int]) -> list[float]:
    return [v / 100 for v in values]


def _layout(fig: go.Figure, title: str, x: str, y: str) -> go.Figure:
    fig.update_layout(
        title=title,
        xaxis_title=x,
        yaxis_title=y,
        template="plotly_white",
        colorway=list(PALETTE),
    )
    return fig


def _bar(
    df: pl.DataFrame, x: str, title: str, x_label: str, y_label: str = "Spend (EUR)"
) -> go.Figure:
    fig = go.Figure(
        go.Bar(x=df[x].to_list(), y=_eur(df["spend_cents"].to_list()), marker_color=PALETTE[0])
    )
    return _layout(fig, title, x_label, y_label)


def spend_over_time_chart(df: pl.DataFrame, period: str = "month") -> go.Figure:
    """Spend per week/month."""
    return _bar(df, "period", f"Spend per {period}", period.capitalize())


def spend_by_category_chart(df: pl.DataFrame) -> go.Figure:
    """Spend per category."""
    return _bar(df, "category_name", "Spend by category", "Category")


def spend_by_chain_chart(df: pl.DataFrame) -> go.Figure:
    """Spend per chain."""
    return _bar(df, "chain_name", "Spend by chain", "Chain")


def shopping_frequency_chart(df: pl.DataFrame) -> go.Figure:
    """Trips per period."""
    fig = go.Figure(
        go.Bar(x=df["period"].to_list(), y=df["trips"].to_list(), marker_color=PALETTE[0])
    )
    return _layout(fig, "Shopping frequency", "Period", "Trips")


def weekday_profile_chart(df: pl.DataFrame) -> go.Figure:
    """Spend by weekday, stacked by hour bucket."""
    fig = go.Figure()
    for i, bucket in enumerate(sorted(df["hour_bucket"].unique().to_list())):
        sub = df.filter(pl.col("hour_bucket") == bucket)
        fig.add_bar(
            name=bucket,
            x=[_WEEKDAYS[d - 1] for d in sub["iso_weekday"].to_list()],
            y=_eur(sub["spend_cents"].to_list()),
            marker_color=PALETTE[i % len(PALETTE)],
        )
    fig.update_layout(barmode="stack")
    return _layout(fig, "Spend by weekday", "Weekday", "Spend (EUR)")


def basket_size_chart(df: pl.DataFrame) -> go.Figure:
    """Distribution of basket value per receipt."""
    fig = go.Figure(go.Histogram(x=_eur(df["spend_cents"].to_list()), marker_color=PALETTE[0]))
    return _layout(fig, "Basket size distribution", "Basket value (EUR)", "Receipts")


def top_products_chart(df: pl.DataFrame) -> go.Figure:
    """Top products by spend."""
    fig = go.Figure(
        go.Bar(
            x=_eur(df["spend_cents"].to_list()),
            y=df["product_name"].to_list(),
            orientation="h",
            marker_color=PALETTE[0],
        )
    )
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, "Top products", "Spend (EUR)", "Product")


def price_evolution_chart(df: pl.DataFrame) -> go.Figure:
    """Price per base unit over time, one line per chain."""
    fig = go.Figure()
    unit = df["base_unit"][0] if df.height else "unit"
    for i, chain in enumerate(sorted(df["chain_name"].unique().to_list())):
        sub = df.filter(pl.col("chain_name") == chain)
        fig.add_scatter(
            name=chain,
            x=sub["purchased_on"].to_list(),
            y=_eur(sub["price_per_base_cents"].to_list()),
            mode="lines+markers",
            line={"color": PALETTE[i % len(PALETTE)]},
        )
    return _layout(fig, "Price evolution", "Date", f"Price (EUR per {unit})")


def cheapest_chain_chart(df: pl.DataFrame) -> go.Figure:
    """Cheapest vs priciest median price per product."""
    names = df["product_name"].to_list()
    fig = go.Figure()
    fig.add_bar(
        name="Cheapest", x=names, y=_eur(df["cheapest_cents"].to_list()), marker_color=PALETTE[2]
    )
    fig.add_bar(
        name="Priciest", x=names, y=_eur(df["priciest_cents"].to_list()), marker_color=PALETTE[3]
    )
    fig.update_layout(barmode="group")
    return _layout(fig, "Cheapest chain per product", "Product", "Median price (EUR per base unit)")
