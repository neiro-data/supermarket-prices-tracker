"""`ptrack enrich` sub-app."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from price_tracker.config import load_settings
from price_tracker.db.connection import connect
from price_tracker.enrich import off
from price_tracker.parsing.service import transaction
from price_tracker.products.catalog import valid_ean

console = Console()
enrich_app = typer.Typer(help="Optional Open Food Facts enrichment.", no_args_is_help=True)


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    conn = connect(load_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()


@enrich_app.command("run")
def enrich_run(limit: Annotated[int | None, typer.Option("--limit", min=1)] = None) -> None:
    """Look up chain-product EANs on Open Food Facts (network) and cache the results."""
    counts = {"found": 0, "not_found": 0, "error": 0}
    client = off.OffClient()
    try:
        with _db() as conn:
            eans = off.pending_eans(conn, limit)
            for ean in eans:
                with transaction(conn):
                    counts[off.refresh(conn, client, ean)] += 1
            with transaction(conn):
                filled = off.fill_pack_sizes(conn)
    finally:
        client.close()
    console.print(
        f"{len(eans)} EAN(s): {counts['found']} found, {counts['not_found']} not found, "
        f"{counts['error']} error(s); pack size filled for {filled} product(s)."
    )


@enrich_app.command("show")
def enrich_show(ean: str) -> None:
    """Show the cached Open Food Facts record for an EAN."""
    if not valid_ean(ean):
        console.print(f"[red]invalid EAN '{escape(ean)}'[/red]")
        raise typer.Exit(1)
    with _db() as conn:
        row = off.get_cached(conn, ean)
    if row is None:
        console.print(f"[yellow]{ean} not cached — run `ptrack enrich run`.[/yellow]")
        raise typer.Exit(1)
    console.print(f"EAN:        {ean}  [{row['status']}, fetched {row['fetched_at']}]")
    for label, key in (
        ("Name", "product_name"),
        ("Brand", "brand"),
        ("Quantity", "quantity"),
        ("Categories", "categories"),
    ):
        console.print(f"{label + ':':<11} {escape(row[key] or '-')}")
    if suggestion := off.suggest_name(row):
        console.print(f"Suggested canonical name (confirm manually): {escape(suggestion)}")
