"""Minimal numbered-SQL migration runner."""

import re
import sqlite3
from importlib import resources

_NAME_RE = re.compile(r"^(\d{4})_(\w+)\.sql$")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


def _discover() -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for entry in resources.files("price_tracker.db").joinpath("migrations").iterdir():
        match = _NAME_RE.match(entry.name)
        if match:
            found.append((int(match[1]), match[2], entry.read_text(encoding="utf-8")))
    return sorted(found)


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply unapplied migrations in order (one transaction each); return applied versions."""
    conn.execute(_CREATE_TABLE)
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied: list[int] = []
    for version, name, sql in _discover():
        if version in done:
            continue
        # executescript() commits implicitly, so wrap it in an explicit transaction.
        script = (
            f"BEGIN;\n{sql}\n"
            "INSERT INTO schema_migrations (version, name, applied_at) "
            f"VALUES ({version}, '{name}', strftime('%Y-%m-%dT%H:%M:%S', 'now'));\nCOMMIT;"  # noqa: S608
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
        applied.append(version)
    return applied
