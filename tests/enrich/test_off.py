import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from price_tracker.enrich import off
from price_tracker.enrich.off import OffClient, OffError

EAN = "4006381333931"  # valid EAN-13
EAN2 = "96385074"  # valid EAN-8


def _client(handler: httpx.MockTransport | None = None, **kw: object) -> OffClient:
    transport = handler or httpx.MockTransport(lambda r: httpx.Response(404))
    return OffClient(client=httpx.Client(transport=transport), **kw)  # type: ignore[arg-type]


def _found(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": 1,
            "product": {
                "product_name": " Vollmilch ",
                "brands": "Alpro,Other",
                "quantity": "1 l",
                "categories": "Milks",
            },
        },
    )


def test_fetch_found() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return _found(r)

    p = _client(httpx.MockTransport(handler)).fetch(EAN)
    assert p == off.OffProduct(EAN, "Vollmilch", "Alpro,Other", "1 l", "Milks")
    assert seen[0].url.path.endswith(f"/{EAN}.json")


def test_fetch_404_and_status0() -> None:
    assert _client().fetch(EAN) is None
    c = _client(httpx.MockTransport(lambda r: httpx.Response(200, json={"status": 0})))
    assert c.fetch(EAN) is None


def test_fetch_timeout_and_server_error() -> None:
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(OffError):
        _client(httpx.MockTransport(boom)).fetch(EAN)
    with pytest.raises(OffError):
        _client(httpx.MockTransport(lambda r: httpx.Response(500))).fetch(EAN)


@pytest.mark.parametrize("bad", ["4006381333932", "123", "abcdefgh", "４００６３８１３３３９３１"])
def test_fetch_rejects_invalid_ean_without_calling(bad: str) -> None:
    def fail(r: httpx.Request) -> httpx.Response:
        raise AssertionError("network called")

    with pytest.raises(ValueError):
        _client(httpx.MockTransport(fail)).fetch(bad)


def test_default_user_agent() -> None:
    c = OffClient()
    assert c._client.headers["User-Agent"] == "price-tracker/0.1"
    c.close()


def _chain_product(db: sqlite3.Connection, name: str, ean: str | None, pack: bool = False) -> int:
    cur = db.execute(
        "INSERT INTO chain_product (chain_id, raw_name, normalized_name, ean, pack_size_milli, "
        "pack_unit) VALUES ((SELECT MIN(id) FROM chain), ?, ?, ?, ?, ?)",
        (name, name, ean, 500 if pack else None, "kg" if pack else None),
    )
    return int(cur.lastrowid or 0)


def test_refresh_upserts_and_statuses(db: sqlite3.Connection) -> None:
    assert off.refresh(db, _client(httpx.MockTransport(_found)), EAN) == "found"
    assert off.refresh(db, _client(), EAN2) == "not_found"
    err = _client(httpx.MockTransport(lambda r: httpx.Response(503)))
    assert off.refresh(db, err, EAN) == "error"
    row = off.get_cached(db, EAN)  # a failed refresh must not clobber the good record
    assert row is not None and row["status"] == "found" and row["product_name"] == "Vollmilch"
    assert off.refresh(db, err, EAN2) == "error"
    row = off.get_cached(db, EAN2)
    assert row is not None and row["status"] == "error"


def test_pending_eans(db: sqlite3.Connection) -> None:
    _chain_product(db, "a", EAN)
    _chain_product(db, "b", EAN2)
    _chain_product(db, "c", None)
    _chain_product(db, "d", "1234567890123")  # invalid checksum
    assert off.pending_eans(db) == sorted([EAN, EAN2])
    assert off.pending_eans(db, limit=1) == [sorted([EAN, EAN2])[0]]
    off.refresh(db, _client(httpx.MockTransport(_found)), EAN)
    assert off.pending_eans(db) == [EAN2]
    old = (datetime.now(UTC) - timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("UPDATE product_ean SET fetched_at = ? WHERE ean = ?", (old, EAN))
    assert EAN in off.pending_eans(db)
    row = off.get_cached(db, EAN)
    assert row is not None and off.is_stale(row)


def test_error_rows_are_retried(db: sqlite3.Connection) -> None:
    _chain_product(db, "a", EAN)
    off.refresh(db, _client(httpx.MockTransport(lambda r: httpx.Response(500))), EAN)
    assert off.pending_eans(db) == [EAN]


def test_fill_pack_sizes_only_when_missing(db: sqlite3.Connection) -> None:
    missing = _chain_product(db, "milk", EAN)
    has = _chain_product(db, "other", EAN2, pack=True)
    off.refresh(db, _client(httpx.MockTransport(_found)), EAN)
    off.refresh(db, _client(httpx.MockTransport(_found)), EAN2)
    assert off.fill_pack_sizes(db) == 1
    r = db.execute("SELECT pack_size_milli, pack_unit FROM chain_product WHERE id = ?", (missing,))
    assert tuple(r.fetchone()) == (1000, "l")
    r = db.execute("SELECT pack_size_milli FROM chain_product WHERE id = ?", (has,))
    assert r.fetchone()[0] == 500


def test_suggest_name(db: sqlite3.Connection) -> None:
    off.refresh(db, _client(httpx.MockTransport(_found)), EAN)
    row = off.get_cached(db, EAN)
    assert row is not None and off.suggest_name(row) == "Alpro Vollmilch"


def test_cli_run_and_show(
    db: sqlite3.Connection, tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from price_tracker.enrich import cli as enrich_cli

    _chain_product(db, "milk", EAN)
    monkeypatch.setattr(enrich_cli, "_db", lambda: _Ctx(db))
    monkeypatch.setattr(enrich_cli.off, "OffClient", lambda: _client(httpx.MockTransport(_found)))
    runner = CliRunner()
    res = runner.invoke(enrich_cli.enrich_app, ["run", "--limit", "5"])
    assert res.exit_code == 0 and "1 found" in res.output and "filled for 1" in res.output
    res = runner.invoke(enrich_cli.enrich_app, ["show", EAN])
    assert res.exit_code == 0 and "Alpro Vollmilch" in res.output
    assert runner.invoke(enrich_cli.enrich_app, ["show", "123"]).exit_code == 1
    assert runner.invoke(enrich_cli.enrich_app, ["show", EAN2]).exit_code == 1


class _Ctx:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, *a: object) -> None:
        return None


@pytest.mark.network
def test_live_lookup() -> None:
    c = OffClient()
    p = c.fetch("3017620422003")  # Nutella
    c.close()
    assert p is not None and p.product_name
