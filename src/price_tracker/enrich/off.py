"""Open Food Facts client + `product_ean` cache. Network is opt-in (`ptrack enrich run`)."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from price_tracker.products.catalog import set_pack, valid_ean
from price_tracker.products.units import parse_pack_size

OFF_BASE_URL = "https://world.openfoodfacts.org"
USER_AGENT = "price-tracker/0.1"  # generic on purpose: no personal data
CACHE_TTL = timedelta(days=30)
_FIELDS = "product_name,brands,quantity,categories"


class OffError(Exception):
    """Transport/HTTP failure talking to Open Food Facts (not a 404)."""


@dataclass(frozen=True, slots=True)
class OffProduct:
    ean: str
    product_name: str | None
    brand: str | None
    quantity: str | None
    categories: str | None


class OffClient:
    def __init__(
        self,
        base_url: str = OFF_BASE_URL,
        timeout: float = 10,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )

    def fetch(self, ean: str) -> OffProduct | None:
        """Return the product, or None if OFF does not know the EAN. Raises OffError on failure."""
        if not valid_ean(ean):
            raise ValueError(f"invalid EAN '{ean}'")
        url = f"{self._base_url}/api/v2/product/{ean}.json"
        try:
            resp = self._client.get(url, params={"fields": _FIELDS})
        except httpx.HTTPError as e:
            raise OffError(str(e) or type(e).__name__) from e
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise OffError(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise OffError("invalid JSON") from e
        product = data.get("product") if isinstance(data, dict) else None
        if data.get("status") == 0 or not isinstance(product, dict):
            return None

        def text(key: str) -> str | None:
            value = product.get(key)
            return value.strip() if isinstance(value, str) and value.strip() else None

        return OffProduct(
            ean, text("product_name"), text("brands"), text("quantity"), text("categories")
        )

    def close(self) -> None:
        self._client.close()


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def get_cached(conn: sqlite3.Connection, ean: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM product_ean WHERE ean = ?", (ean,)
    ).fetchone()
    return row


def is_stale(row: sqlite3.Row, now: datetime | None = None) -> bool:
    fetched = datetime.fromisoformat(row["fetched_at"]).replace(tzinfo=UTC)
    return (now or _now()) - fetched > CACHE_TTL


def refresh(conn: sqlite3.Connection, client: OffClient, ean: str) -> str:
    """Fetch one EAN and upsert the cache row; return the status written."""
    product: OffProduct | None = None
    try:
        product = client.fetch(ean)
        status = "found" if product else "not_found"
    except OffError:
        status = "error"
        cached = get_cached(conn, ean)
        if cached is not None and cached["status"] == "found":
            return status  # keep the last good record; it is retried once stale
    conn.execute(
        "INSERT INTO product_ean (ean, product_name, brand, quantity, categories, fetched_at, "
        "status) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (ean) DO UPDATE SET product_name = excluded.product_name, "
        "brand = excluded.brand, quantity = excluded.quantity, categories = excluded.categories, "
        "fetched_at = excluded.fetched_at, status = excluded.status",
        (
            ean,
            product.product_name if product else None,
            product.brand if product else None,
            product.quantity if product else None,
            product.categories if product else None,
            _iso(_now()),
            status,
        ),
    )
    return status


def pending_eans(conn: sqlite3.Connection, limit: int | None = None) -> list[str]:
    """Distinct chain-product EANs that are valid and uncached, stale, or previously errored."""
    cutoff = _iso(_now() - CACHE_TTL)
    rows = conn.execute(
        "SELECT DISTINCT cp.ean FROM chain_product cp "
        "LEFT JOIN product_ean pe ON pe.ean = cp.ean "
        "WHERE cp.ean IS NOT NULL AND (pe.ean IS NULL OR pe.fetched_at < ? OR pe.status = 'error') "
        "ORDER BY cp.ean",
        (cutoff,),
    ).fetchall()
    eans = [r[0] for r in rows if valid_ean(r[0])]
    return eans[:limit] if limit is not None else eans


def fill_pack_sizes(conn: sqlite3.Connection) -> int:
    """Set `pack_size` from the cached OFF quantity where a chain product has none."""
    rows = conn.execute(
        "SELECT cp.id, pe.quantity FROM chain_product cp "
        "JOIN product_ean pe ON pe.ean = cp.ean "
        "WHERE pe.status = 'found' AND pe.quantity IS NOT NULL AND cp.pack_size_milli IS NULL"
    ).fetchall()
    filled = 0
    for row in rows:
        if pack := parse_pack_size(row["quantity"]):
            set_pack(conn, row["id"], pack)
            filled += 1
    return filled


def suggest_name(row: sqlite3.Row) -> str | None:
    """Canonical-name suggestion (never applied automatically): 'Brand Name'."""
    name = row["product_name"]
    if not name:
        return None
    brand = row["brand"]
    first_brand = brand.split(",")[0].strip() if brand else ""
    if first_brand and first_brand.lower() not in name.lower():
        return f"{first_brand} {name}"
    return str(name)
