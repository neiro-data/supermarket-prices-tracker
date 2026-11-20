from pathlib import Path

import pytest
from typer.testing import CliRunner

from price_tracker.cli import app


def test_init_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PTRACK_DB_PATH", str(tmp_path / "d" / "p.db"))
    monkeypatch.setenv("PTRACK_DATA_DIR", str(tmp_path / "d"))
    runner = CliRunner()
    res = runner.invoke(app, ["init"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["db", "status"])
    assert res.exit_code == 0, res.output
    assert "initial" in res.output
    assert "views" in res.output
