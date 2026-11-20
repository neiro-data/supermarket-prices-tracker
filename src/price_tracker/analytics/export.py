"""CSV and self-contained HTML exports."""

from pathlib import Path

import plotly.graph_objects as go
import polars as pl


def write_csv(df: pl.DataFrame, path: Path) -> Path:
    """Write `df` as CSV (money columns stay in cents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(path)
    return path


def write_html(fig: go.Figure, path: Path) -> Path:
    """Write a figure as an offline HTML file with plotly.js embedded."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(path, include_plotlyjs=True)
    return path
