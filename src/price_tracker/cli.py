"""`ptrack` command line interface."""

from importlib import resources

import typer
from rich.console import Console
from rich.table import Table

from price_tracker.config import load_settings
from price_tracker.db.connection import connect
from price_tracker.db.migrate import apply_migrations
from price_tracker.db.seed import seed_categories, seed_chains

app = typer.Typer(help="Supermarket price tracker.", no_args_is_help=True)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")
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
