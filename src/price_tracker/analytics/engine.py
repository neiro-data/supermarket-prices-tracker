"""DuckDB analytics connection: sqlite ATTACH with an offline-safe polars fallback."""

import sqlite3
from pathlib import Path

import duckdb
import polars as pl

RELATIONS: tuple[str, ...] = (
    "v_line_effective",
    "receipt",
    "receipt_line",
    "chain",
    "store",
    "category",
    "canonical_product",
)


def _attach(con: duckdb.DuckDBPyConnection, db_path: Path) -> None:
    """Attach the SQLite file read-only and alias the needed relations in `main`."""
    con.execute("INSTALL sqlite")
    con.execute("LOAD sqlite")
    resolved = str(db_path.resolve())
    try:
        con.execute("ATTACH ? AS src (TYPE sqlite, READ_ONLY)", [resolved])
    except duckdb.Error:
        quoted = resolved.replace("'", "''")
        con.execute(f"ATTACH '{quoted}' AS src (TYPE sqlite, READ_ONLY)")  # noqa: S608
    for name in RELATIONS:
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM src.{name}")  # noqa: S608
        con.execute(f"SELECT * FROM {name} LIMIT 0")  # noqa: S608


_PL_TYPES: dict[str, pl.DataType | type[pl.DataType]] = {
    "INTEGER": pl.Int64,
    "INT": pl.Int64,
    "BIGINT": pl.Int64,
    "SMALLINT": pl.Int64,
    "BOOLEAN": pl.Int64,
    "REAL": pl.Float64,
    "FLOAT": pl.Float64,
    "DOUBLE": pl.Float64,
    "NUMERIC": pl.Float64,
    "BLOB": pl.Binary,
}


def _pl_type(name: str, declared: str | None) -> pl.DataType | type[pl.DataType]:
    """Polars dtype for a column from its SQLite declared type.

    View expression columns have no declared type; money/quantity columns (`*_cents`,
    `*_milli`) are integers by project convention, anything else defaults to Utf8.
    """
    base = (declared or "").upper().split("(")[0].strip()
    if not base and name.endswith(("_cents", "_milli")):
        return pl.Int64
    return _PL_TYPES.get(base, pl.Utf8)


def _view_column_types(src: sqlite3.Connection, name: str) -> dict[str, str]:
    """Declared types of a view's columns, resolved through its base tables."""
    return {
        row[1]: row[2]
        for row in src.execute(f"PRAGMA table_info({name})")  # noqa: S608
    }


def _load_fallback(con: duckdb.DuckDBPyConnection, db_path: Path) -> None:
    """Copy the needed relations from SQLite into polars frames registered in DuckDB."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    src = sqlite3.connect(uri, uri=True)
    try:
        for name in RELATIONS:
            declared = _view_column_types(src, name)
            cur = src.execute(f"SELECT * FROM {name}")  # noqa: S608
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            schema = {c: _pl_type(c, declared.get(c)) for c in cols}
            df = pl.DataFrame(rows, schema=schema, orient="row", strict=False)
            con.register(name, df)
    finally:
        src.close()


def connect_analytics(db_path: Path, *, prefer_attach: bool = True) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB exposing the receipt relations of `db_path`.

    Tries the DuckDB sqlite extension first; on `duckdb.Error` (e.g. offline, extension
    missing) loads the relations via sqlite3 and polars instead. Both paths expose
    the names in `RELATIONS`.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")
    con = duckdb.connect(":memory:")
    if prefer_attach:
        try:
            _attach(con, db_path)
            return con
        except duckdb.Error:
            con.close()
            con = duckdb.connect(":memory:")
    _load_fallback(con, db_path)
    return con
