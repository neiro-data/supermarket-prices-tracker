"""CLI sub-apps: categories, canonical, products, alias, match."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from typing import Annotated

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from price_tracker.config import load_settings
from price_tracker.db.connection import connect
from price_tracker.db.seed import seed_categories
from price_tracker.domain.money import format_eur
from price_tracker.parsing.service import transaction
from price_tracker.products import catalog
from price_tracker.products.categorize import (
    category_id_for_slug,
    effective_category_slug,
    set_category,
)
from price_tracker.products.matching import (
    Suggestion,
    assign,
    auto_match_all,
    reject,
    suggest,
)
from price_tracker.products.normalize import key_name
from price_tracker.products.units import parse_pack_size

console = Console()
categories_app = typer.Typer(help="Manage the category taxonomy.", no_args_is_help=True)
canonical_app = typer.Typer(help="Manage canonical products.", no_args_is_help=True)
products_app = typer.Typer(help="Inspect and edit chain products.", no_args_is_help=True)
alias_app = typer.Typer(help="Manage alias rules.", no_args_is_help=True)
match_app = typer.Typer(help="Match chain products to canonical products.", no_args_is_help=True)


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    conn = connect(load_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _write() -> Iterator[sqlite3.Connection]:
    """Connection inside a transaction; domain errors become a clean CLI failure."""
    with _db() as conn:
        try:
            with transaction(conn):
                yield conn
        except (ValueError, sqlite3.IntegrityError) as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e


# --- categories ---------------------------------------------------------------------------


@categories_app.command("list")
def categories_list() -> None:
    """List categories as a tree."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT c.slug, c.name_de, c.name_en, p.slug AS parent FROM category AS c "
            "LEFT JOIN category AS p ON p.id = c.parent_id "
            "ORDER BY COALESCE(p.slug, c.slug), p.slug IS NOT NULL, c.slug"
        ).fetchall()
    table = Table("slug", "name_de", "name_en")
    for r in rows:
        indent = "  " if r["parent"] else ""
        table.add_row(indent + r["slug"], r["name_de"], r["name_en"])
    console.print(table)


@categories_app.command("add")
def categories_add(
    slug: str,
    name_de: Annotated[str, typer.Option("--name-de")],
    name_en: Annotated[str, typer.Option("--name-en")],
    parent: Annotated[str | None, typer.Option("--parent")] = None,
) -> None:
    """Add a category (max 2 levels)."""
    with _write() as conn:
        catalog.add_category(conn, slug, name_de, name_en, parent)
    console.print(f"Added category {slug}.")


@categories_app.command("reseed")
def categories_reseed() -> None:
    """Re-apply the seed taxonomy (idempotent upsert)."""
    with (
        _db() as conn,
        resources.as_file(
            resources.files("price_tracker").joinpath("data/categories.toml")
        ) as path,
    ):
        n = seed_categories(conn, path)
    console.print(f"Reseeded {n} categories.")


# --- canonical ----------------------------------------------------------------------------


@canonical_app.command("list")
def canonical_list(category: Annotated[str | None, typer.Option("--category")] = None) -> None:
    """List canonical products with mapped chain-product counts."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT c.id, c.name, c.base_unit, c.brand, cat.slug AS category, "
            "(SELECT COUNT(*) FROM chain_product AS cp WHERE cp.canonical_product_id = c.id) "
            "AS n_products FROM canonical_product AS c "
            "LEFT JOIN category AS cat ON cat.id = c.category_id "
            "WHERE (? IS NULL OR cat.slug = ?) ORDER BY c.name",
            (category, category),
        ).fetchall()
    table = Table("id", "name", "unit", "brand", "category", "chain products")
    for r in rows:
        table.add_row(
            str(r["id"]), r["name"], r["base_unit"], r["brand"] or "", r["category"] or "",
            str(r["n_products"]),
        )  # fmt: skip
    console.print(table)


@canonical_app.command("add")
def canonical_add(
    name: str,
    unit: Annotated[str, typer.Option("--unit", help="kg|l|pc")],
    category: Annotated[str | None, typer.Option("--category")] = None,
    brand: Annotated[str | None, typer.Option("--brand")] = None,
) -> None:
    """Create a canonical product."""
    with _write() as conn:
        cid = catalog.add_canonical(conn, name, unit, category, brand)
    console.print(f"Created canonical product {cid}: {name}")


@canonical_app.command("rename")
def canonical_rename(canonical_id: int, name: str) -> None:
    """Rename a canonical product."""
    with _write() as conn:
        catalog.rename_canonical(conn, canonical_id, name)
    console.print(f"Renamed {canonical_id} to {name}.")


@canonical_app.command("set-category")
def canonical_set_category(canonical_id: int, slug: str) -> None:
    """Set the category of a canonical product."""
    with _write() as conn:
        set_category(conn, slug, canonical_id=canonical_id)
    console.print(f"Set category of canonical {canonical_id} to {slug}.")


@canonical_app.command("merge")
def canonical_merge(src: int, tgt: int) -> None:
    """Merge canonical product SRC into TGT."""
    with _write() as conn:
        catalog.merge_canonical(conn, src, tgt)
    console.print(f"Merged {src} into {tgt}.")


# --- products -----------------------------------------------------------------------------


@products_app.command("list")
def products_list(
    unmatched: Annotated[bool, typer.Option("--unmatched")] = False,
    chain: Annotated[str | None, typer.Option("--chain")] = None,
) -> None:
    """List chain products."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT cp.id, ch.code AS chain, cp.raw_name, cp.pack_size_milli, cp.pack_unit, "
            "cp.ean, c.name AS canonical, cp.match_source, cp.id AS cpid FROM chain_product AS cp "
            "INNER JOIN chain AS ch ON ch.id = cp.chain_id "
            "LEFT JOIN canonical_product AS c ON c.id = cp.canonical_product_id "
            "WHERE (? = 0 OR cp.canonical_product_id IS NULL) AND (? IS NULL OR ch.code = ?) "
            "ORDER BY ch.code, cp.normalized_name",
            (int(unmatched), chain, chain),
        ).fetchall()
        cats = {r["cpid"]: effective_category_slug(conn, r["cpid"]) or "" for r in rows}
    table = Table("id", "chain", "raw name", "pack", "ean", "category", "canonical", "via")
    for r in rows:
        pack = f"{r['pack_size_milli']} m{r['pack_unit']}" if r["pack_size_milli"] else ""
        table.add_row(
            str(r["id"]), r["chain"], r["raw_name"], pack, r["ean"] or "",
            cats[r["cpid"]], r["canonical"] or "",
            r["match_source"] or "",
        )  # fmt: skip
    console.print(table)


@products_app.command("set-ean")
def products_set_ean(product_id: int, ean: str) -> None:
    """Set the EAN of a chain product."""
    with _write() as conn:
        catalog.set_ean(conn, product_id, ean)
    console.print(f"Set EAN of {product_id} to {ean}.")


@products_app.command("set-category")
def products_set_category(product_id: int, slug: str) -> None:
    """Set the category of a chain product (overrides the canonical one)."""
    with _write() as conn:
        set_category(conn, slug, chain_product_id=product_id)
    console.print(f"Set category of {product_id} to {slug}.")


@products_app.command("set-pack")
def products_set_pack(product_id: int, pack: str) -> None:
    """Set the pack size of a chain product, e.g. 500g, 1,5l, 6x0,5l."""
    parsed = parse_pack_size(pack)
    if parsed is None:
        console.print(f"[red]Cannot parse pack size '{pack}'[/red]")
        raise typer.Exit(2)
    with _write() as conn:
        catalog.set_pack(conn, product_id, parsed)
    console.print(f"Set pack of {product_id} to {parsed.amount_milli} milli-{parsed.unit.value}.")


# --- alias --------------------------------------------------------------------------------


@alias_app.command("list")
def alias_list() -> None:
    """List alias rules."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT a.id, ch.code AS chain, a.pattern, a.match_type, a.priority, "
            "a.canonical_product_id AS cid, c.name FROM alias_rule AS a "
            "LEFT JOIN chain AS ch ON ch.id = a.chain_id "
            "LEFT JOIN canonical_product AS c ON c.id = a.canonical_product_id "
            "ORDER BY a.id"
        ).fetchall()
    table = Table("id", "chain", "pattern", "type", "prio", "canonical")
    for r in rows:
        table.add_row(
            str(r["id"]), r["chain"] or "*", r["pattern"], r["match_type"], str(r["priority"]),
            f"{r['cid']} {r['name'] or ''}".strip(),
        )  # fmt: skip
    console.print(table)


@alias_app.command("add")
def alias_add(
    pattern: str,
    canonical_id: int,
    chain: Annotated[str | None, typer.Option("--chain")] = None,
    match_type: Annotated[str, typer.Option("--type")] = "exact",
    priority: Annotated[int, typer.Option("--priority")] = 100,
) -> None:
    """Add an alias rule mapping a normalized name pattern to a canonical product."""
    with _write() as conn:
        rid = catalog.add_alias(conn, pattern, canonical_id, chain, match_type, priority)
    console.print(f"Added alias rule {rid}.")


@alias_app.command("delete")
def alias_delete(rule_id: int) -> None:
    """Delete an alias rule."""
    with _write() as conn:
        cur = conn.execute("DELETE FROM alias_rule WHERE id = ?", (rule_id,))
        if cur.rowcount == 0:
            raise ValueError(f"no alias rule {rule_id}")
    console.print(f"Deleted alias rule {rule_id}.")


# --- match --------------------------------------------------------------------------------


@match_app.command("run")
def match_run() -> None:
    """Auto-match unmapped chain products by alias rules and EAN."""
    with _write() as conn:
        stats = auto_match_all(conn)
    console.print(
        f"rule={stats.by_rule} ean={stats.by_ean} unmatched={stats.unmatched} "
        f"lines_backfilled={stats.lines_backfilled}"
    )


def _show_item(conn: sqlite3.Connection, cp: sqlite3.Row, sugg: list[Suggestion]) -> None:
    price = conn.execute(
        "SELECT line_total_cents FROM receipt_line WHERE chain_product_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (cp["id"],),
    ).fetchone()
    shown = format_eur(price["line_total_cents"]) if price else "-"
    console.print(
        f"\n[bold]{cp['raw_name']}[/bold] ({cp['chain_code']}) "
        f"normalized='{cp['normalized_name']}' last price {shown}"
    )
    for i, s in enumerate(sugg, 1):
        console.print(f"  {i}) {s.name} [{s.score}, {s.via}]")
    console.print("  n) new canonical  x) reject top  s) skip  q) quit")


def _new_canonical(conn: sqlite3.Connection, cp: sqlite3.Row) -> int:
    name = Prompt.ask(
        "Canonical name", default=key_name(str(cp["normalized_name"])).title(), console=console
    )
    pack_unit = cp["pack_unit"] or ("kg" if cp["is_weighed"] else "pc")
    unit = Prompt.ask("Base unit", choices=["kg", "l", "pc"], default=pack_unit, console=console)
    cat = Prompt.ask("Category slug (optional)", default="", console=console).strip()
    if cat:
        category_id_for_slug(conn, cat)  # fail before creating anything
    return catalog.add_canonical(conn, name, unit, cat or None)


@match_app.command("review")
def match_review(
    limit: Annotated[int, typer.Option("--limit")] = 50,
    threshold: Annotated[int, typer.Option("--threshold")] = 80,
) -> None:
    """Interactively map unmatched chain products to canonical products."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT cp.id, cp.raw_name, cp.normalized_name, cp.pack_unit, cp.is_weighed, "
            "ch.code AS chain_code FROM chain_product AS cp "
            "INNER JOIN chain AS ch ON ch.id = cp.chain_id "
            "WHERE cp.canonical_product_id IS NULL "
            "ORDER BY ch.code, cp.normalized_name LIMIT ?",
            (limit,),
        ).fetchall()
        for cp in rows:
            while True:
                sugg = suggest(conn, cp["id"], threshold=threshold)
                _show_item(conn, cp, sugg)
                keys = [*map(str, range(1, len(sugg) + 1)), "n", "x", "s", "q"]
                key = Prompt.ask("Choice", choices=keys, default="s", console=console)
                if key == "q":
                    return
                if key == "s":
                    break
                if key == "x":
                    if sugg:
                        with transaction(conn):
                            reject(conn, cp["id"], sugg[0].canonical_id)
                    continue
                if key == "n":
                    try:
                        with transaction(conn):
                            cid = _new_canonical(conn, cp)
                            remember = Confirm.ask(
                                "Remember as rule?", default=True, console=console
                            )
                            assign(conn, cp["id"], cid, "manual", remember)
                    except (ValueError, sqlite3.IntegrityError) as e:
                        console.print(f"[red]{e}[/red]")
                        continue
                    break
                chosen = sugg[int(key) - 1]
                remember = Confirm.ask("Remember as rule?", default=True, console=console)
                with transaction(conn):
                    assign(conn, cp["id"], chosen.canonical_id, "fuzzy", remember)
                break
    console.print("Review finished.")
