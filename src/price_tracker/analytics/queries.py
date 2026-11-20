"""Analytics queries over confirmed/parsed receipts. Money stays in integer cents."""

from datetime import date, timedelta
from typing import Literal

import duckdb
import polars as pl

# Shared CTE: valid receipts with a parsed local date, filtered by optional bounds.
_RECEIPTS_CTE = """
rcpt AS (
    SELECT
        id AS receipt_id,
        chain_id,
        CAST(SUBSTRING(CAST(purchased_at AS VARCHAR) FROM 1 FOR 10) AS DATE) AS purchased_on,
        CAST(SUBSTRING(CAST(purchased_at AS VARCHAR) FROM 12 FOR 2) AS INTEGER) AS purchase_hour
    FROM receipt
    WHERE status IN ('confirmed', 'parsed')
        AND purchased_at IS NOT NULL
),
rr AS (
    SELECT receipt_id, chain_id, purchased_on, purchase_hour
    FROM rcpt
    WHERE (CAST(? AS DATE) IS NULL OR purchased_on >= CAST(? AS DATE))
        AND (CAST(? AS DATE) IS NULL OR purchased_on <= CAST(? AS DATE))
)
"""

SPEND_OVER_TIME = f"""
WITH {_RECEIPTS_CTE},
totals AS (
    SELECT rr.receipt_id, rr.purchased_on, SUM(l.line_total_cents) AS spend_cents
    FROM rr
    INNER JOIN receipt_line AS l ON l.receipt_id = rr.receipt_id
    GROUP BY rr.receipt_id, rr.purchased_on
)
SELECT
    CAST(DATE_TRUNC(?, purchased_on) AS DATE) AS period,
    COUNT(*) AS receipts,
    CAST(SUM(spend_cents) AS BIGINT) AS spend_cents
FROM totals
GROUP BY 1
ORDER BY 1
"""  # noqa: S608

_CATEGORY_ITEMS = """
SELECT
    COALESCE(CASE WHEN ? = 1 THEN COALESCE(parent_category_slug, category_slug)
                  ELSE category_slug END, 'uncategorized') AS category_slug,
    COALESCE(CASE WHEN ? = 1 THEN COALESCE(parent_category_name_en, category_name_en)
                  ELSE category_name_en END, 'Uncategorized') AS category_name,
    effective_total_cents AS cents
FROM v_line_effective
WHERE receipt_id IN (SELECT receipt_id FROM rr)
"""

_CATEGORY_DEPOSITS = """
UNION ALL
SELECT 'deposit' AS category_slug, 'Deposits' AS category_name, line_total_cents AS cents
FROM receipt_line
WHERE line_type IN ('deposit', 'deposit_return')
    AND receipt_id IN (SELECT receipt_id FROM rr)
"""

SPEND_BY_CATEGORY = f"""
WITH {_RECEIPTS_CTE},
lines AS (
    {_CATEGORY_ITEMS}
    {{deposits}}
)
SELECT
    category_slug,
    category_name,
    COUNT(*) AS line_count,
    CAST(SUM(cents) AS BIGINT) AS spend_cents
FROM lines
GROUP BY category_slug, category_name
ORDER BY spend_cents DESC, category_slug
"""  # noqa: S608

SPEND_BY_CHAIN = f"""
WITH {_RECEIPTS_CTE},
totals AS (
    SELECT rr.receipt_id, rr.chain_id, SUM(l.line_total_cents) AS spend_cents
    FROM rr
    INNER JOIN receipt_line AS l ON l.receipt_id = rr.receipt_id
    GROUP BY rr.receipt_id, rr.chain_id
)
SELECT
    COALESCE(c.code, 'unknown') AS chain_code,
    COALESCE(c.name, 'Unknown') AS chain_name,
    COUNT(*) AS receipts,
    CAST(SUM(t.spend_cents) AS BIGINT) AS spend_cents
FROM totals AS t
LEFT JOIN chain AS c ON c.id = t.chain_id
GROUP BY 1, 2
ORDER BY spend_cents DESC, chain_code
"""  # noqa: S608

SHOPPING_FREQUENCY = f"""
WITH {_RECEIPTS_CTE},
gaps AS (
    SELECT
        receipt_id,
        purchased_on,
        DATE_DIFF('day', LAG(purchased_on) OVER (ORDER BY purchased_on, receipt_id), purchased_on)
            AS gap_days
    FROM rr
),
overall AS (
    SELECT AVG(gap_days) AS avg_days_between FROM gaps
)
SELECT
    CAST(DATE_TRUNC(?, g.purchased_on) AS DATE) AS period,
    COUNT(*) AS trips,
    ROUND(MAX(o.avg_days_between), 2) AS avg_days_between
FROM gaps AS g
CROSS JOIN overall AS o
GROUP BY 1
ORDER BY 1
"""  # noqa: S608

WEEKDAY_PROFILE = f"""
WITH {_RECEIPTS_CTE},
totals AS (
    SELECT
        rr.receipt_id,
        ISODOW(rr.purchased_on) AS iso_weekday,
        CASE
            WHEN rr.purchase_hour IS NULL THEN 'unknown'
            WHEN rr.purchase_hour < 12 THEN 'morning'
            WHEN rr.purchase_hour < 17 THEN 'afternoon'
            ELSE 'evening'
        END AS hour_bucket,
        SUM(l.line_total_cents) AS spend_cents
    FROM rr
    INNER JOIN receipt_line AS l ON l.receipt_id = rr.receipt_id
    GROUP BY rr.receipt_id, rr.purchased_on, rr.purchase_hour
)
SELECT
    iso_weekday,
    hour_bucket,
    COUNT(*) AS trips,
    CAST(SUM(spend_cents) AS BIGINT) AS spend_cents
FROM totals
GROUP BY iso_weekday, hour_bucket
ORDER BY iso_weekday, hour_bucket
"""  # noqa: S608

BASKET_SIZE = f"""
WITH {_RECEIPTS_CTE}
SELECT
    rr.receipt_id,
    rr.purchased_on,
    CAST(COUNT(CASE WHEN l.line_type = 'item' THEN 1 END) AS BIGINT) AS items,
    CAST(SUM(l.line_total_cents) AS BIGINT) AS spend_cents
FROM rr
INNER JOIN receipt_line AS l ON l.receipt_id = rr.receipt_id
GROUP BY rr.receipt_id, rr.purchased_on
ORDER BY rr.purchased_on, rr.receipt_id
"""  # noqa: S608

_TOP_PRODUCTS = f"""
WITH {_RECEIPTS_CTE},
lines AS (
    SELECT
        COALESCE(canonical_product_name, chain_product_name, raw_text) AS product_name,
        effective_total_cents
    FROM v_line_effective
    WHERE receipt_id IN (SELECT receipt_id FROM rr)
)
SELECT
    product_name,
    COUNT(*) AS purchases,
    CAST(SUM(effective_total_cents) AS BIGINT) AS spend_cents
FROM lines
GROUP BY product_name
ORDER BY {{order}}, product_name
LIMIT ?
"""  # noqa: S608

PRICE_EVOLUTION = """
SELECT
    CAST(SUBSTRING(CAST(purchased_at AS VARCHAR) FROM 1 FOR 10) AS DATE) AS purchased_on,
    chain_name,
    store_city,
    base_unit,
    CAST(FLOOR(price_per_base_cents) AS BIGINT) AS price_per_base_cents
FROM v_line_effective
WHERE canonical_product_id = ?
    AND price_per_base_cents IS NOT NULL
    AND receipt_status IN ('confirmed', 'parsed')
    AND purchased_at IS NOT NULL
ORDER BY purchased_on, chain_name, line_id
"""

CHEAPEST_CHAIN = """
WITH priced AS (
    SELECT
        canonical_product_id,
        canonical_product_name,
        base_unit,
        chain_name,
        CAST(FLOOR(price_per_base_cents) AS BIGINT) AS price_per_base_cents
    FROM v_line_effective
    WHERE canonical_product_id IS NOT NULL
        AND price_per_base_cents IS NOT NULL
        AND receipt_status IN ('confirmed', 'parsed')
        AND purchased_at IS NOT NULL
        AND CAST(SUBSTRING(CAST(purchased_at AS VARCHAR) FROM 1 FOR 10) AS DATE)
            >= CAST(? AS DATE)
),
per_chain AS (
    SELECT
        canonical_product_id,
        canonical_product_name,
        base_unit,
        chain_name,
        CAST(ROUND(MEDIAN(price_per_base_cents)) AS BIGINT) AS median_cents
    FROM priced
    GROUP BY canonical_product_id, canonical_product_name, base_unit, chain_name
),
ranked AS (
    SELECT
        canonical_product_id,
        canonical_product_name,
        base_unit,
        chain_name,
        median_cents,
        ROW_NUMBER() OVER (
            PARTITION BY canonical_product_id ORDER BY median_cents, chain_name
        ) AS cheap_rank,
        ROW_NUMBER() OVER (
            PARTITION BY canonical_product_id ORDER BY median_cents DESC, chain_name DESC
        ) AS pricey_rank,
        COUNT(*) OVER (PARTITION BY canonical_product_id) AS n_chains
    FROM per_chain
)
SELECT
    c.canonical_product_id,
    c.canonical_product_name AS product_name,
    c.base_unit,
    c.n_chains,
    c.chain_name AS cheapest_chain,
    c.median_cents AS cheapest_cents,
    p.chain_name AS priciest_chain,
    p.median_cents AS priciest_cents,
    ROUND((p.median_cents - c.median_cents) * 100.0 / p.median_cents, 1) AS delta_pct
FROM ranked AS c
INNER JOIN ranked AS p
    ON p.canonical_product_id = c.canonical_product_id AND p.pricey_rank = 1
WHERE c.cheap_rank = 1 AND c.n_chains >= 2
ORDER BY delta_pct DESC, product_name
"""

Period = Literal["week", "month"]


def _run(con: duckdb.DuckDBPyConnection, sql: str, params: list[object]) -> pl.DataFrame:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return pl.DataFrame(cur.fetchall(), schema=cols, orient="row", infer_schema_length=None)


def _range(since: date | None, until: date | None) -> list[object]:
    return [since, since, until, until]


def spend_over_time(
    con: duckdb.DuckDBPyConnection,
    period: Period = "month",
    since: date | None = None,
    until: date | None = None,
) -> pl.DataFrame:
    """Receipts and spend (cents) per week or month; sums all line types."""
    return _run(con, SPEND_OVER_TIME, [*_range(since, until), period])


def spend_by_category(
    con: duckdb.DuckDBPyConnection,
    level: Literal[1, 2] = 1,
    since: date | None = None,
    until: date | None = None,
    include_deposits: bool = False,
) -> pl.DataFrame:
    """Net item spend (discounts folded in) per category level; deposits optional."""
    sql = SPEND_BY_CATEGORY.replace("{deposits}", _CATEGORY_DEPOSITS if include_deposits else "")
    return _run(con, sql, [*_range(since, until), level, level])


def spend_by_chain(
    con: duckdb.DuckDBPyConnection, since: date | None = None, until: date | None = None
) -> pl.DataFrame:
    """Receipts and spend (cents) per chain."""
    return _run(con, SPEND_BY_CHAIN, _range(since, until))


def shopping_frequency(con: duckdb.DuckDBPyConnection, period: Period = "week") -> pl.DataFrame:
    """Trips per period plus the overall average days between trips."""
    return _run(con, SHOPPING_FREQUENCY, [*_range(None, None), period])


def weekday_profile(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Trips and spend by ISO weekday (1=Mon) and hour bucket."""
    return _run(con, WEEKDAY_PROFILE, _range(None, None))


def basket_size(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Item count and spend in cents per receipt."""
    return _run(con, BASKET_SIZE, _range(None, None))


def top_products(
    con: duckdb.DuckDBPyConnection, n: int = 20, by: Literal["spend", "count"] = "spend"
) -> pl.DataFrame:
    """Top `n` products (canonical name, else chain product name) by spend or purchase count."""
    order = "spend_cents DESC" if by == "spend" else "purchases DESC, spend_cents DESC"
    return _run(con, _TOP_PRODUCTS.replace("{order}", order), [*_range(None, None), n])


def price_evolution(con: duckdb.DuckDBPyConnection, canonical_id: int) -> pl.DataFrame:
    """Price per base unit (cents) over time, by chain and city."""
    return _run(con, PRICE_EVOLUTION, [canonical_id])


def cheapest_chain(con: duckdb.DuckDBPyConnection, since: date | None = None) -> pl.DataFrame:
    """Per canonical product bought at >= 2 chains: median price per chain, cheapest, delta %.

    `since` defaults to 90 days before today.
    """
    start = since if since is not None else date.today() - timedelta(days=90)
    return _run(con, CHEAPEST_CHAIN, [start])
