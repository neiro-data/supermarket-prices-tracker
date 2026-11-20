"""`ptrack report` sub-app."""

from collections.abc import Callable
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

import duckdb
import plotly.graph_objects as go
import polars as pl
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from price_tracker.analytics import charts, export, queries
from price_tracker.analytics.engine import connect_analytics
from price_tracker.config import load_settings
from price_tracker.domain.money import format_eur

console = Console()
report_app = typer.Typer(help="Spending and price reports.", no_args_is_help=True)


class Fmt(Enum):
    table = "table"
    csv = "csv"
    html = "html"


class SpendBy(Enum):
    week = "week"
    month = "month"
    category = "category"
    chain = "chain"


class TopBy(Enum):
    spend = "spend"
    count = "count"


Report = tuple[str, pl.DataFrame, Callable[[pl.DataFrame], go.Figure]]
_Since = Annotated[
    datetime | None, typer.Option(help="Start date (YYYY-MM-DD).", formats=["%Y-%m-%d"])
]
_Until = Annotated[
    datetime | None, typer.Option(help="End date (YYYY-MM-DD).", formats=["%Y-%m-%d"])
]
_Format = Annotated[Fmt, typer.Option("--format", help="Output format.")]
_Out = Annotated[Path | None, typer.Option("--out", help="Output file (csv/html).")]


def _d(value: datetime | None) -> date | None:
    return value.date() if value else None


def _connect() -> duckdb.DuckDBPyConnection:
    try:
        return connect_analytics(load_settings().db_path)
    except FileNotFoundError as e:
        console.print(f"[red]{e}. Run `ptrack init`.[/red]")
        raise typer.Exit(1) from e


def _show(name: str, df: pl.DataFrame) -> None:
    table = Table(*df.columns, title=name)
    money = [c.endswith("_cents") for c in df.columns]
    for row in df.iter_rows():
        table.add_row(
            *[
                format_eur(v) if m and isinstance(v, int) else "" if v is None else escape(str(v))
                for v, m in zip(row, money, strict=True)
            ]
        )
    console.print(table)
    if df.is_empty():
        console.print("No data.")


def _emit(reports: list[Report], title: str, fmt: Fmt, out: Path | None) -> None:
    """Render reports as tables, or write one csv/html file per report."""
    if fmt is Fmt.table:
        for name, df, _ in reports:
            _show(name, df)
        return
    base = (
        out or load_settings().data_dir / "output" / f"{title}-{date.today():%Y-%m-%d}.{fmt.value}"
    )
    for name, df, make_fig in reports:
        path = base if len(reports) == 1 else base.with_name(f"{base.stem}-{name}{base.suffix}")
        if fmt is Fmt.csv:
            export.write_csv(df, path)
        else:
            export.write_html(make_fig(df), path)
        console.print(f"Wrote {path}")


@report_app.command("spend")
def spend(
    by: Annotated[SpendBy, typer.Option("--by", help="Grouping.")] = SpendBy.month,
    level: Annotated[int, typer.Option(help="Category level (1 or 2).", min=1, max=2)] = 1,
    since: _Since = None,
    until: _Until = None,
    fmt: _Format = Fmt.table,
    out: _Out = None,
) -> None:
    """Spend over time, by category or by chain."""
    con = _connect()
    s, u = _d(since), _d(until)
    name = f"spend-{by.value}"
    if by is SpendBy.category:
        lvl: Literal[1, 2] = 2 if level == 2 else 1
        _emit(
            [(name, queries.spend_by_category(con, lvl, s, u), charts.spend_by_category_chart)],
            name,
            fmt,
            out,
        )
    elif by is SpendBy.chain:
        _emit(
            [(name, queries.spend_by_chain(con, s, u), charts.spend_by_chain_chart)], name, fmt, out
        )
    else:
        period: Literal["week", "month"] = "week" if by is SpendBy.week else "month"
        df = queries.spend_over_time(con, period, s, u)
        _emit([(name, df, lambda d: charts.spend_over_time_chart(d, period))], name, fmt, out)


@report_app.command("habits")
def habits(fmt: _Format = Fmt.table, out: _Out = None) -> None:
    """Shopping frequency, weekday profile and basket size."""
    con = _connect()
    reports: list[Report] = [
        ("frequency", queries.shopping_frequency(con), charts.shopping_frequency_chart),
        ("weekday", queries.weekday_profile(con), charts.weekday_profile_chart),
        ("basket", queries.basket_size(con), charts.basket_size_chart),
    ]
    _emit(reports, "habits", fmt, out)


@report_app.command("top")
def top(
    n: Annotated[int, typer.Option(help="Number of products.", min=1)] = 20,
    by: Annotated[TopBy, typer.Option("--by", help="Ranking.")] = TopBy.spend,
    fmt: _Format = Fmt.table,
    out: _Out = None,
) -> None:
    """Top products by spend or purchase count."""
    df = queries.top_products(_connect(), n, "spend" if by is TopBy.spend else "count")
    _emit([("top", df, charts.top_products_chart)], "top", fmt, out)


@report_app.command("prices")
def prices(
    canonical: Annotated[str, typer.Option(help="Canonical product ID or name.")],
    fmt: _Format = Fmt.table,
    out: _Out = None,
) -> None:
    """Price per base unit over time for one canonical product."""
    con = _connect()
    if canonical.isascii() and canonical.isdigit():
        cid = int(canonical)
    else:
        from price_tracker.db.connection import connect

        conn = connect(load_settings().db_path)
        try:
            row = conn.execute(
                "SELECT id FROM canonical_product WHERE name = ? COLLATE NOCASE", (canonical,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            console.print(f"[red]Unknown canonical product: {canonical}[/red]")
            raise typer.Exit(1)
        cid = int(row["id"])
    df = queries.price_evolution(con, cid)
    _emit([("prices", df, charts.price_evolution_chart)], f"prices-{cid}", fmt, out)


@report_app.command("cheapest")
def cheapest(since: _Since = None, fmt: _Format = Fmt.table, out: _Out = None) -> None:
    """Cheapest chain per product (default: last 90 days)."""
    df = queries.cheapest_chain(_connect(), _d(since))
    _emit([("cheapest", df, charts.cheapest_chain_chart)], "cheapest", fmt, out)
