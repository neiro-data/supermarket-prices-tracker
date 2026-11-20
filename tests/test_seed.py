import sqlite3
from importlib import resources
from pathlib import Path

import pytest

from price_tracker.db.seed import seed_categories, seed_chains


def test_counts(db: sqlite3.Connection) -> None:
    assert db.execute("SELECT COUNT(*) FROM category WHERE parent_id IS NULL").fetchone()[0] == 12
    assert db.execute("SELECT COUNT(*) FROM chain").fetchone()[0] == 21
    assert db.execute("SELECT COUNT(*) FROM category WHERE slug = 'pfand'").fetchone()[0] == 1


def test_two_levels_max(db: sqlite3.Connection) -> None:
    deep = db.execute(
        "SELECT COUNT(*) FROM category AS c "
        "JOIN category AS p ON p.id = c.parent_id WHERE p.parent_id IS NOT NULL"
    ).fetchone()[0]
    assert deep == 0


def test_rerun_no_dupes(db: sqlite3.Connection) -> None:
    before = db.execute("SELECT COUNT(*) FROM category").fetchone()[0]
    data = resources.files("price_tracker").joinpath("data")
    seed_categories(db, Path(str(data.joinpath("categories.toml"))))
    seed_chains(db, Path(str(data.joinpath("chains.toml"))))
    assert db.execute("SELECT COUNT(*) FROM category").fetchone()[0] == before
    assert db.execute("SELECT COUNT(*) FROM chain").fetchone()[0] == 21


def test_failed_seed_rolls_back(db: sqlite3.Connection, tmp_path: Path) -> None:
    before = db.execute("SELECT COUNT(*) FROM chain").fetchone()[0]
    bad = tmp_path / "chains.toml"
    bad.write_text(
        '[[chains]]\ncode = "zz_new"\nname = "N"\ncountry_code = "DE"\nparser_profile = "p"\n'
        '[[chains]]\ncode = "zz_bad"\n'
    )
    with pytest.raises(KeyError):
        seed_chains(db, bad)
    assert db.execute("SELECT COUNT(*) FROM chain").fetchone()[0] == before
    assert not db.in_transaction
