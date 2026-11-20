from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from price_tracker.analytics import charts, export, queries
from price_tracker.analytics.engine import connect_analytics
from price_tracker.cli import app


def test_write_csv(analytics_db: Path, tmp_path: Path) -> None:
    con = connect_analytics(analytics_db, prefer_attach=False)
    df = queries.spend_by_chain(con)
    path = export.write_csv(df, tmp_path / "out" / "chain.csv")
    lines = path.read_text().splitlines()
    assert lines[0] == "chain_code,chain_name,receipts,spend_cents"
    assert len(lines) == 1 + df.height == 4
    assert lines[1] == "rewe,REWE,4,1345"


def test_write_html_offline(analytics_db: Path, tmp_path: Path) -> None:
    con = connect_analytics(analytics_db, prefer_attach=False)
    fig = charts.spend_over_time_chart(queries.spend_over_time(con, "month"), "month")
    assert fig.data[0].y == (16.25, 12.81)  # cents -> euros only at render time
    html = export.write_html(fig, tmp_path / "m.html").read_text()
    assert "plotly" in html
    assert "cdn.plot.ly" not in html.split("<script", 2)[1][:200]


@pytest.fixture
def env(analytics_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PTRACK_DB_PATH", str(analytics_db))
    monkeypatch.setenv("PTRACK_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data" / "output"


CASES = [
    (["spend", "--by", "month"], "spend-month"),
    (["spend", "--by", "week", "--since", "2026-01-12"], "spend-week"),
    (["spend", "--by", "category", "--level", "2"], "spend-category"),
    (["spend", "--by", "chain"], "spend-chain"),
    (["top", "--n", "3", "--by", "count"], "top"),
    (["prices", "--canonical", "1"], "prices-1"),
    (["prices", "--canonical", "Aepfel"], "prices-2"),
    (["cheapest", "--since", "2026-01-01"], "cheapest"),
]


@pytest.mark.parametrize(("args", "_name"), CASES)
def test_cli_table(env: Path, args: list[str], _name: str) -> None:
    res = CliRunner().invoke(app, ["report", *args])
    assert res.exit_code == 0, res.output
    assert "No data" not in res.output


def test_cli_table_shows_euros(env: Path) -> None:
    res = CliRunner().invoke(app, ["report", "spend", "--by", "chain"])
    assert "13,45" in res.output or "13.45" in res.output


def test_cli_habits_table(env: Path) -> None:
    res = CliRunner().invoke(app, ["report", "habits"])
    assert res.exit_code == 0, res.output
    assert "avg_days_between" in res.output


@pytest.mark.parametrize(("args", "name"), CASES)
def test_cli_csv_and_html(env: Path, args: list[str], name: str) -> None:
    for ext in ("csv", "html"):
        res = CliRunner().invoke(app, ["report", *args, "--format", ext])
        assert res.exit_code == 0, res.output
        assert (env / f"{name}-{date.today():%Y-%m-%d}.{ext}").is_file()


def test_cli_habits_files(env: Path, tmp_path: Path) -> None:
    out = tmp_path / "h.html"
    res = CliRunner().invoke(app, ["report", "habits", "--format", "html", "--out", str(out)])
    assert res.exit_code == 0, res.output
    for part in ("frequency", "weekday", "basket"):
        assert "plotly" in (tmp_path / f"h-{part}.html").read_text()


def test_cli_missing_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PTRACK_DB_PATH", str(tmp_path / "nope.db"))
    res = CliRunner().invoke(app, ["report", "top"])
    assert res.exit_code == 1


def test_cli_unknown_canonical(env: Path) -> None:
    res = CliRunner().invoke(app, ["report", "prices", "--canonical", "Nope"])
    assert res.exit_code == 1
