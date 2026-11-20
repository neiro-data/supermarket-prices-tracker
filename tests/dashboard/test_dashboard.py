"""Dashboard tests via streamlit AppTest against the analytics fixture DB."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from typer.testing import CliRunner

from price_tracker import cli
from price_tracker.analytics.engine import connect_analytics
from price_tracker.analytics.queries import spend_by_chain

APP_PATH = Path(cli.__file__).parent / "dashboard" / "app.py"


@pytest.fixture
def at(analytics_db: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("PTRACK_DB_PATH", str(analytics_db))
    return AppTest.from_file(str(APP_PATH), default_timeout=30).run()


def test_overview_kpis_match_queries(at: AppTest, analytics_db: Path) -> None:
    assert not at.exception
    df = spend_by_chain(connect_analytics(analytics_db))
    trips = int(df["receipts"].sum())
    values = {m.label: m.value for m in at.metric}
    assert values["Trips"] == str(trips)
    assert "Total spend" in values


@pytest.mark.parametrize(
    "page", ["Categories", "Chains & stores", "Habits", "Price tracker", "Cheapest chain"]
)
def test_every_page_renders(at: AppTest, page: str) -> None:
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception


def test_price_tracker_renders_chart(at: AppTest) -> None:
    at.sidebar.radio[0].set_value("Price tracker").run()
    assert at.selectbox[0].options
    at.selectbox[0].select_index(0).run()
    assert not at.exception
    assert len(at.get("plotly_chart")) >= 1


def test_dashboard_cli_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("PTRACK_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: calls.append(argv))
    result = CliRunner().invoke(cli.app, ["dashboard", "--port", "9000"])
    assert result.exit_code == 0
    argv = calls[0]
    assert argv[1:4] == ["-m", "streamlit", "run"]
    assert argv[4] == str(APP_PATH)
    assert argv[-2:] == ["--server.port", "9000"]
