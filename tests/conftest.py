import sqlite3
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest

from price_tracker.db.connection import connect
from price_tracker.db.migrate import apply_migrations
from price_tracker.db.seed import seed_categories, seed_chains


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(tmp_path / "test.db")
    apply_migrations(conn)
    data = resources.files("price_tracker").joinpath("data")
    seed_categories(conn, Path(str(data.joinpath("categories.toml"))))
    seed_chains(conn, Path(str(data.joinpath("chains.toml"))))
    yield conn
    conn.close()
