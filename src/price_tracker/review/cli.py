"""`ptrack review ...` — inspect and correct parsed receipts."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, cast

import typer
from rich.console import Console
from rich.table import Table

from price_tracker.config import load_settings
from price_tracker.db.connection import connect
from price_tracker.domain.models import LineType, ParsedLine, ParsedReceipt, ReceiptStatus, Unit
from price_tracker.domain.money import format_eur, parse_eur
from price_tracker.parsing.service import (
    get_or_create_chain_product,
    now_utc,
    parse_source_file,
    transaction,
)
from price_tracker.parsing.validate import assess

review_app = typer.Typer(help="Review and correct parsed receipts.", no_args_is_help=True)
console = Console()


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    conn = connect(load_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()


def _receipt(conn: sqlite3.Connection, receipt_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM receipt WHERE id = ?", (receipt_id,)).fetchone()
    if row is None:
        console.print(f"[red]No receipt {receipt_id}[/red]")
        raise typer.Exit(1)
    return cast(sqlite3.Row, row)


def _touch(conn: sqlite3.Connection, receipt_id: int) -> None:
    """Recompute the lines sum and bump `updated_at` after an edit."""
    conn.execute(
        "UPDATE receipt SET lines_sum_cents = (SELECT COALESCE(SUM(line_total_cents), 0) "
        "FROM receipt_line WHERE receipt_id = ?), updated_at = ? WHERE id = ?",
        (receipt_id, now_utc(), receipt_id),
    )


def _parse_unit(unit: str) -> Unit:
    try:
        return Unit(unit)
    except ValueError:
        console.print("[red]--unit must be pc, kg or l[/red]")
        raise typer.Exit(2) from None


def _parse_type(line_type: str) -> LineType:
    try:
        return LineType(line_type)
    except ValueError:
        console.print(f"[red]--type must be one of {[t.value for t in LineType]}[/red]")
        raise typer.Exit(2) from None


def _amount(text: str) -> int:
    try:
        return parse_eur(text)
    except ValueError:
        console.print(f"[red]Not an amount: {text!r} (use e.g. 12,34)[/red]")
        raise typer.Exit(2) from None


@review_app.command("list")
def list_receipts(
    status: Annotated[str | None, typer.Option("--status", help="Filter by status.")] = None,
) -> None:
    """List receipts."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT r.id, r.status, r.purchased_at, c.code AS chain, r.total_cents, "
            "r.lines_sum_cents, r.review_reason FROM receipt r "
            "LEFT JOIN chain c ON c.id = r.chain_id "
            "WHERE (? IS NULL OR r.status = ?) ORDER BY r.id",
            (status, status),
        ).fetchall()
    table = Table("id", "status", "date", "chain", "total", "sum", "reason")
    for r in rows:
        table.add_row(
            str(r["id"]),
            r["status"],
            r["purchased_at"] or "-",
            r["chain"] or "-",
            format_eur(r["total_cents"]) if r["total_cents"] is not None else "-",
            format_eur(r["lines_sum_cents"]) if r["lines_sum_cents"] is not None else "-",
            r["review_reason"] or "",
        )
    console.print(table)


@review_app.command()
def show(receipt_id: int) -> None:
    """Show header, lines, sum vs total and the first 40 raw lines."""
    with _db() as conn:
        r = _receipt(conn, receipt_id)
        lines = conn.execute(
            "SELECT l.line_no, l.line_type, l.raw_text, cp.raw_name, l.quantity_milli, "
            "l.quantity_unit, l.unit_price_cents, l.line_total_cents, l.applies_to_line_no "
            "FROM receipt_line l LEFT JOIN chain_product cp ON cp.id = l.chain_product_id "
            "WHERE l.receipt_id = ? ORDER BY l.line_no",
            (receipt_id,),
        ).fetchall()
        raw = conn.execute(
            "SELECT raw_text FROM source_file WHERE id = ?", (r["source_file_id"],)
        ).fetchone()["raw_text"]
    console.print(
        f"Receipt {r['id']} [{r['status']}] chain_id={r['chain_id']} store_id={r['store_id']} "
        f"date={r['purchased_at']} profile={r['parser_profile']} "
        f"confidence={r['parse_confidence']:.2f}"
    )
    if r["review_reason"]:
        console.print(f"[yellow]Review: {r['review_reason']}[/yellow]")
    table = Table("#", "type", "name", "qty", "unit", "unit price", "total", "for")
    for ln in lines:
        table.add_row(
            str(ln["line_no"]),
            ln["line_type"],
            ln["raw_name"] or ln["raw_text"].strip(),
            f"{ln['quantity_milli'] / 1000:g}",
            ln["quantity_unit"],
            format_eur(ln["unit_price_cents"]) if ln["unit_price_cents"] is not None else "-",
            format_eur(ln["line_total_cents"]),
            str(ln["applies_to_line_no"] or ""),
        )
    console.print(table)
    total = r["total_cents"]
    console.print(
        f"Sum of lines: {format_eur(r['lines_sum_cents'] or 0)}  "
        f"Total: {format_eur(total) if total is not None else '-'}"
    )
    console.print("--- raw text (first 40 lines) ---")
    for text in raw.splitlines()[:40]:
        console.print(text, markup=False)


@review_app.command("set")
def set_header(
    receipt_id: int,
    total: Annotated[str | None, typer.Option(help="e.g. 12,34")] = None,
    date: Annotated[str | None, typer.Option(help="ISO, e.g. 2026-11-20T18:42")] = None,
    chain: Annotated[str | None, typer.Option(help="Chain code, e.g. rewe")] = None,
    store_city: Annotated[str | None, typer.Option(help="Store city")] = None,
) -> None:
    """Correct receipt header fields."""
    with _db() as conn:
        _receipt(conn, receipt_id)
        with transaction(conn):
            if total is not None:
                conn.execute(
                    "UPDATE receipt SET total_cents = ? WHERE id = ?", (_amount(total), receipt_id)
                )
            if date is not None:
                try:
                    stamp = datetime.fromisoformat(date).strftime("%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    console.print("[red]--date must be ISO, e.g. 2026-11-20T18:42[/red]")
                    raise typer.Exit(2) from None
                conn.execute(
                    "UPDATE receipt SET purchased_at = ? WHERE id = ?", (stamp, receipt_id)
                )
            if chain is not None:
                row = conn.execute("SELECT id FROM chain WHERE code = ?", (chain,)).fetchone()
                if row is None:
                    console.print(f"[red]Unknown chain {chain!r}[/red]")
                    raise typer.Exit(2)
                conn.execute(
                    "UPDATE receipt SET chain_id = ? WHERE id = ?", (row["id"], receipt_id)
                )
            if store_city is not None:
                _set_store_city(conn, receipt_id, store_city)
            _touch(conn, receipt_id)
    console.print(f"Receipt {receipt_id} updated.")


def _set_store_city(conn: sqlite3.Connection, receipt_id: int, city: str) -> None:
    r = _receipt(conn, receipt_id)
    if r["chain_id"] is None:
        console.print("[red]Set --chain before --store-city[/red]")
        raise typer.Exit(2)
    if r["store_id"] is not None:
        conn.execute("UPDATE store SET city = ? WHERE id = ?", (city, r["store_id"]))
        return
    cur = conn.execute("INSERT INTO store (chain_id, city) VALUES (?, ?)", (r["chain_id"], city))
    conn.execute("UPDATE receipt SET store_id = ? WHERE id = ?", (cur.lastrowid, receipt_id))


@review_app.command()
def line(
    receipt_id: int,
    line_no: int,
    total: Annotated[str | None, typer.Option()] = None,
    qty: Annotated[float | None, typer.Option(help="Quantity, e.g. 0.566 or 2")] = None,
    unit: Annotated[str | None, typer.Option(help="pc|kg|l")] = None,
    type_: Annotated[str | None, typer.Option("--type", help="Line type")] = None,
    name: Annotated[str | None, typer.Option(help="Item name")] = None,
) -> None:
    """Correct one line."""
    with _db() as conn:
        _receipt(conn, receipt_id)
        row = conn.execute(
            "SELECT id, chain_product_id, line_type FROM receipt_line "
            "WHERE receipt_id = ? AND line_no = ?",
            (receipt_id, line_no),
        ).fetchone()
        if row is None:
            console.print(f"[red]No line {line_no} on receipt {receipt_id}[/red]")
            raise typer.Exit(1)
        with transaction(conn):
            if total is not None:
                conn.execute(
                    "UPDATE receipt_line SET line_total_cents = ? WHERE id = ?",
                    (_amount(total), row["id"]),
                )
            if qty is not None:
                conn.execute(
                    "UPDATE receipt_line SET quantity_milli = ? WHERE id = ?",
                    (round(qty * 1000), row["id"]),
                )
            if unit is not None:
                conn.execute(
                    "UPDATE receipt_line SET quantity_unit = ? WHERE id = ?",
                    (_parse_unit(unit).value, row["id"]),
                )
            if type_ is not None:
                conn.execute(
                    "UPDATE receipt_line SET line_type = ? WHERE id = ?",
                    (_parse_type(type_).value, row["id"]),
                )
            if name is not None:
                _rename(conn, receipt_id, row["id"], name)
            _touch(conn, receipt_id)
    console.print(f"Line {line_no} updated.")


def _rename(conn: sqlite3.Connection, receipt_id: int, line_id: int, name: str) -> None:
    r = _receipt(conn, receipt_id)
    if r["chain_id"] is None:
        console.print("[red]Set --chain before renaming items[/red]")
        raise typer.Exit(2)
    unit = conn.execute(
        "SELECT quantity_unit FROM receipt_line WHERE id = ?", (line_id,)
    ).fetchone()["quantity_unit"]
    cp_id = get_or_create_chain_product(conn, r["chain_id"], name, unit == "kg")
    conn.execute("UPDATE receipt_line SET chain_product_id = ? WHERE id = ?", (cp_id, line_id))


@review_app.command("add-line")
def add_line(
    receipt_id: int,
    name: Annotated[str, typer.Option()],
    total: Annotated[str, typer.Option()],
    qty: Annotated[float, typer.Option()] = 1.0,
    unit: Annotated[str, typer.Option()] = "pc",
) -> None:
    """Add a missing item line."""
    cents, u = _amount(total), _parse_unit(unit)
    with _db() as conn:
        r = _receipt(conn, receipt_id)
        with transaction(conn):
            next_no = conn.execute(
                "SELECT COALESCE(MAX(line_no), 0) + 1 AS n FROM receipt_line WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()["n"]
            cp_id = (
                get_or_create_chain_product(conn, r["chain_id"], name, u is Unit.KG)
                if r["chain_id"] is not None
                else None
            )
            conn.execute(
                "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, "
                "chain_product_id, quantity_milli, quantity_unit, line_total_cents) "
                "VALUES (?, ?, ?, 'item', ?, ?, ?, ?)",
                (receipt_id, next_no, f"{name} (manual)", cp_id, round(qty * 1000), u.value, cents),
            )
            _touch(conn, receipt_id)
    console.print(f"Added line {next_no}.")


@review_app.command("delete-line")
def delete_line(receipt_id: int, line_no: int) -> None:
    """Delete a line (discounts that pointed at it become unlinked)."""
    with _db() as conn:
        _receipt(conn, receipt_id)
        with transaction(conn):
            conn.execute(
                "UPDATE receipt_line SET applies_to_line_no = NULL "
                "WHERE receipt_id = ? AND applies_to_line_no = ?",
                (receipt_id, line_no),
            )
            cur = conn.execute(
                "DELETE FROM receipt_line WHERE receipt_id = ? AND line_no = ?",
                (receipt_id, line_no),
            )
            _touch(conn, receipt_id)
    if cur.rowcount == 0:
        console.print(f"[red]No line {line_no}[/red]")
        raise typer.Exit(1)
    console.print(f"Deleted line {line_no}.")


@review_app.command()
def accept(
    receipt_id: int,
    force: Annotated[bool, typer.Option("--force", help="Confirm even if sums differ.")] = False,
) -> None:
    """Confirm a receipt after re-checking it (sum must match unless --force)."""
    with _db() as conn:
        r = _receipt(conn, receipt_id)
        lines = conn.execute(
            "SELECT line_no, raw_text, line_type, line_total_cents FROM receipt_line "
            "WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchall()
        probe = ParsedReceipt(
            chain_code=None,
            store=None,
            purchased_at=datetime.fromisoformat(r["purchased_at"]) if r["purchased_at"] else None,
            total_cents=r["total_cents"],
            lines=tuple(
                ParsedLine(
                    ln["line_no"], ln["raw_text"], LineType(ln["line_type"]), ln["line_total_cents"]
                )
                for ln in lines
            ),
            confidence=1.0,
        )
        status, reason = assess(probe, None)
        if status is not ReceiptStatus.PARSED and not force:
            console.print(f"[red]Not confirmed: {reason}. Fix it or use --force.[/red]")
            raise typer.Exit(1)
        conn.execute(
            "UPDATE receipt SET status = 'confirmed', review_reason = NULL, updated_at = ? "
            "WHERE id = ?",
            (now_utc(), receipt_id),
        )
    console.print(f"Receipt {receipt_id} confirmed.")


@review_app.command()
def reparse(
    receipt_id: Annotated[int | None, typer.Argument()] = None,
    status: Annotated[str | None, typer.Option("--status")] = None,
    all_: Annotated[bool, typer.Option("--all")] = False,
    include_confirmed: Annotated[bool, typer.Option("--include-confirmed")] = False,
) -> None:
    """Re-run the parser on stored raw text (skips confirmed receipts by default)."""
    if receipt_id is None and status is None and not all_:
        console.print("[red]Give a receipt ID, --status or --all[/red]")
        raise typer.Exit(2)
    with _db() as conn:
        rows = conn.execute(
            "SELECT s.id AS source_id, r.id AS receipt_id, r.status FROM source_file s "
            "LEFT JOIN receipt r ON r.source_file_id = s.id "
            "WHERE (? IS NULL OR r.id = ?) AND (? IS NULL OR r.status = ?) ORDER BY s.id",
            (receipt_id, receipt_id, status, status),
        ).fetchall()
        done = skipped = 0
        for row in rows:
            if row["status"] == "confirmed" and not include_confirmed:
                skipped += 1
                continue
            parse_source_file(conn, row["source_id"])
            done += 1
    console.print(f"reparsed={done} skipped_confirmed={skipped}")
