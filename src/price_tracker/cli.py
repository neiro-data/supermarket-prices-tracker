"""`ptrack` command line interface."""

import os
import subprocess  # noqa: S404  # fixed argv, no shell
import sys
from importlib import resources
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from price_tracker.analytics.cli import report_app
from price_tracker.config import load_settings
from price_tracker.db.connection import connect
from price_tracker.db.migrate import apply_migrations
from price_tracker.db.seed import seed_categories, seed_chains
from price_tracker.enrich.cli import enrich_app
from price_tracker.ingest.extract import SUPPORTED_EXTS
from price_tracker.ingest.ocr import TesseractEngine
from price_tracker.ingest.service import IngestOutcome, ingest_file
from price_tracker.products.cli import (
    alias_app,
    canonical_app,
    categories_app,
    match_app,
    products_app,
)
from price_tracker.review.cli import review_app

app = typer.Typer(help="Supermarket price tracker.", no_args_is_help=True)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(review_app, name="review")
app.add_typer(categories_app, name="categories")
app.add_typer(canonical_app, name="canonical")
app.add_typer(products_app, name="products")
app.add_typer(alias_app, name="alias")
app.add_typer(match_app, name="match")
app.add_typer(report_app, name="report")
app.add_typer(enrich_app, name="enrich")
console = Console()


@app.callback()
def main() -> None:
    """Supermarket price tracker."""


@app.command()
def init() -> None:
    """Create data dirs, apply migrations and seed categories and chains."""
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    pkg_data = resources.files("price_tracker").joinpath("data")
    conn = connect(settings.db_path)
    try:
        applied = apply_migrations(conn)
        with resources.as_file(pkg_data.joinpath("categories.toml")) as cat_path:
            n_cat = seed_categories(conn, cat_path)
        with resources.as_file(pkg_data.joinpath("chains.toml")) as chain_path:
            n_chain = seed_chains(conn, chain_path)
    finally:
        conn.close()
    console.print(
        f"DB {settings.db_path}: applied migrations {applied or 'none'}, "
        f"seeded {n_cat} categories, {n_chain} chains."
    )


@db_app.command("status")
def db_status() -> None:
    """List applied migrations."""
    settings = load_settings()
    conn = connect(settings.db_path)
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        rows = (
            conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
            if exists
            else []
        )
    finally:
        conn.close()
    table = Table("version", "name", "applied_at")
    for r in rows:
        table.add_row(str(r["version"]), r["name"], r["applied_at"])
    console.print(table)
    if not rows:
        console.print("No migrations applied. Run `ptrack init`.")


def _expand(paths: list[Path]) -> list[Path]:
    """Expand directories (non-recursively) to supported files, keeping explicit files."""
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(
                sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS)
            )
        else:
            out.append(p)
    return out


@app.command()
def ingest(
    paths: Annotated[
        list[Path] | None, typer.Argument(help="Files or directories (default: data/inbox).")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Re-extract known files.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List files only.")] = False,
) -> None:
    """Extract text from receipt PDFs/photos and store each new file once."""
    settings = load_settings()
    files = _expand(paths or [settings.data_dir / "inbox"])
    if dry_run:
        for f in files:
            console.print(str(f))
        console.print(f"{len(files)} file(s) would be ingested.")
        return
    engine = TesseractEngine(settings.tesseract_cmd)
    conn = connect(settings.db_path)
    counts = {o: 0 for o in IngestOutcome}
    try:
        for f in files:
            counts[ingest_file(conn, f, engine, settings.data_dir / "archive", force)] += 1
    finally:
        conn.close()
    console.print(" ".join(f"{o.value}={n}" for o, n in counts.items()))


@app.command()
def dashboard(
    port: Annotated[int, typer.Option(help="Port to serve on.")] = 8501,
) -> None:
    """Serve the Streamlit dashboard (read-only) on localhost."""
    settings = load_settings()
    os.environ["PTRACK_DB_PATH"] = str(settings.db_path)
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    # Fixed argv (no user-controlled strings besides the int port), no shell.
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port)],
        check=True,
    )
