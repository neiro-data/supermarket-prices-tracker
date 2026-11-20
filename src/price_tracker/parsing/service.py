"""Persist parsed receipts: chain/store lookup, receipt + lines, chain_product links."""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from price_tracker.domain.models import LineType, ParsedReceipt, ReceiptStatus, Unit
from price_tracker.parsing import profiles as _profiles  # noqa: F401  (registers profiles)
from price_tracker.parsing.base import PARSERS
from price_tracker.parsing.detect import ChainInfo, detect_chain, load_chain_infos
from price_tracker.parsing.validate import assess
from price_tracker.products.matching import auto_match_all
from price_tracker.products.normalize import identity_key
from price_tracker.products.units import parse_pack_size

logger = logging.getLogger(__name__)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Explicit BEGIN/COMMIT with ROLLBACK on error (connections are in autocommit mode)."""
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def get_or_create_chain_product(
    conn: sqlite3.Connection, chain_id: int, raw_name: str, is_weighed: bool
) -> int:
    norm = identity_key(raw_name)
    row = conn.execute(
        "SELECT id FROM chain_product WHERE chain_id = ? AND normalized_name = ?",
        (chain_id, norm),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    pack = parse_pack_size(raw_name)
    cur = conn.execute(
        "INSERT INTO chain_product (chain_id, raw_name, normalized_name, is_weighed, "
        "pack_size_milli, pack_unit) VALUES (?, ?, ?, ?, ?, ?)",
        (
            chain_id,
            raw_name,
            norm,
            int(is_weighed),
            pack.amount_milli if pack else None,
            pack.unit.value if pack else None,
        ),
    )
    return int(cur.lastrowid or 0)


def _chain_id(conn: sqlite3.Connection, code: str | None) -> int | None:
    if code is None:
        return None
    row = conn.execute("SELECT id FROM chain WHERE code = ?", (code,)).fetchone()
    return int(row["id"]) if row else None


def _store_id(conn: sqlite3.Connection, chain_id: int | None, p: ParsedReceipt) -> int | None:
    s = p.store
    if chain_id is None or s is None or not (s.postal_code or s.street):
        return None
    row = conn.execute(
        "SELECT id FROM store WHERE chain_id = ? AND street IS ? AND postal_code IS ?",
        (chain_id, s.street, s.postal_code),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO store (chain_id, name, street, postal_code, city, country_code) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (chain_id, s.name, s.street, s.postal_code, s.city, s.country_code),
    )
    return int(cur.lastrowid or 0)


def parse_source_file(conn: sqlite3.Connection, source_file_id: int) -> int:
    """Parse stored raw text into a receipt (replacing lines if it exists); return receipt id."""
    src = conn.execute(
        "SELECT raw_text, ocr_confidence FROM source_file WHERE id = ?", (source_file_id,)
    ).fetchone()
    if src is None:
        raise ValueError(f"no source_file {source_file_id}")
    chains = load_chain_infos()
    found: ChainInfo | None = detect_chain(src["raw_text"], chains)
    profile = found.parser_profile if found else "generic"
    parsed: ParsedReceipt | None = None
    failure: str | None = None
    try:
        parsed = PARSERS[profile].parse(src["raw_text"], found.code if found else None)
        status, reason = assess(parsed, src["ocr_confidence"])
    except Exception as e:  # noqa: BLE001 - a parser bug must not lose the ingested file
        logger.warning("parse failed for source_file %s: %s", source_file_id, e)
        failure = f"parser error: {e}"
        status, reason = ReceiptStatus.FAILED, failure
    now = now_utc()
    with transaction(conn):
        chain_id = _chain_id(conn, found.code if found else None)
        existing = conn.execute(
            "SELECT id FROM receipt WHERE source_file_id = ?", (source_file_id,)
        ).fetchone()
        header = (
            chain_id,
            _store_id(conn, chain_id, parsed) if parsed else None,
            parsed.purchased_at.strftime("%Y-%m-%dT%H:%M:%S")
            if parsed and parsed.purchased_at
            else None,
            parsed.currency if parsed else "EUR",
            parsed.total_cents if parsed else None,
            sum(ln.line_total_cents for ln in parsed.lines) if parsed else None,
            parsed.payment_method if parsed else None,
            profile,
            parsed.confidence if parsed else 0.0,
            status.value,
            reason,
        )
        if existing is None:
            cur = conn.execute(
                "INSERT INTO receipt (chain_id, store_id, purchased_at, currency, total_cents, "
                "lines_sum_cents, payment_method, parser_profile, parse_confidence, status, "
                "review_reason, source_file_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*header, source_file_id, now, now),
            )
            receipt_id = int(cur.lastrowid or 0)
        else:
            receipt_id = int(existing["id"])
            conn.execute(
                "UPDATE receipt SET chain_id = ?, store_id = ?, purchased_at = ?, currency = ?, "
                "total_cents = ?, lines_sum_cents = ?, payment_method = ?, parser_profile = ?, "
                "parse_confidence = ?, status = ?, review_reason = ?, updated_at = ? WHERE id = ?",
                (*header, now, receipt_id),
            )
            conn.execute("DELETE FROM receipt_line WHERE receipt_id = ?", (receipt_id,))
        for ln in parsed.lines if parsed else ():
            cp_id: int | None = None
            if ln.line_type is LineType.ITEM and chain_id is not None:
                cp_id = get_or_create_chain_product(
                    conn, chain_id, ln.name or ln.raw_text.strip(), ln.quantity_unit is Unit.KG
                )
            conn.execute(
                "INSERT INTO receipt_line (receipt_id, line_no, raw_text, line_type, "
                "chain_product_id, quantity_milli, quantity_unit, unit_price_cents, "
                "line_total_cents, vat_code, applies_to_line_no) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt_id,
                    ln.line_no,
                    ln.raw_text,
                    ln.line_type.value,
                    cp_id,
                    ln.quantity_milli,
                    ln.quantity_unit.value,
                    ln.unit_price_cents,
                    ln.line_total_cents,
                    ln.vat_code,
                    ln.applies_to_line_no,
                ),
            )
        auto_match_all(conn)
    return receipt_id
