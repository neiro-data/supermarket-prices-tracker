"""Idempotent seeding of categories and chains from TOML files."""

import sqlite3
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Explicit BEGIN/COMMIT, ROLLBACK on error (connect() uses autocommit mode)."""
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


_UPSERT_CATEGORY = """
INSERT INTO category (parent_id, slug, name_de, name_en)
VALUES (?, ?, ?, ?)
ON CONFLICT (slug) DO UPDATE SET name_de = excluded.name_de, name_en = excluded.name_en
"""


def _upsert_category(
    conn: sqlite3.Connection, parent_id: int | None, slug: str, node: dict[str, Any]
) -> int:
    conn.execute(_UPSERT_CATEGORY, (parent_id, slug, node["name_de"], node["name_en"]))
    row = conn.execute("SELECT id FROM category WHERE slug = ?", (slug,)).fetchone()
    return int(row["id"])


def seed_categories(conn: sqlite3.Connection, path: Path) -> int:
    """Upsert the two-level taxonomy; return the number of categories processed."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    count = 0
    with _transaction(conn):
        for slug, node in data["categories"].items():
            parent_id = _upsert_category(conn, None, slug, node)
            count += 1
            for child_slug, child in node.get("children", {}).items():
                _upsert_category(conn, parent_id, child_slug, child)
                count += 1
    return count


def seed_chains(conn: sqlite3.Connection, path: Path) -> int:
    """Upsert chains (detect_keywords are parser-side only); return rows processed."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    chains: list[dict[str, Any]] = data["chains"]
    with _transaction(conn):
        for chain in chains:
            conn.execute(
                "INSERT INTO chain (code, name, country_code, parser_profile) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (code) DO UPDATE SET name = excluded.name, "
                "country_code = excluded.country_code, "
                "parser_profile = excluded.parser_profile",
                (chain["code"], chain["name"], chain["country_code"], chain["parser_profile"]),
            )
    return len(chains)
