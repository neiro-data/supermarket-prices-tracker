"""Ingest service: hash, dedup, archive and store extracted text."""

import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from price_tracker.ingest.extract import extract
from price_tracker.ingest.hashing import sha256_file
from price_tracker.ingest.ocr import OcrEngine
from price_tracker.parsing.service import parse_source_file

logger = logging.getLogger(__name__)


class IngestOutcome(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    FAILED = "failed"


def ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    engine: OcrEngine,
    archive_dir: Path,
    force: bool = False,
) -> IngestOutcome:
    """Store one file once. `force` re-extracts and replaces an already-ingested file."""
    try:
        digest = sha256_file(path)
        existing = conn.execute("SELECT id FROM source_file WHERE sha256 = ?", (digest,)).fetchone()
        if existing is not None and not force:
            return IngestOutcome.DUPLICATE
        ex = extract(path, engine)
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / f"{digest}{path.suffix.lower()}"
        if not dest.exists():
            shutil.copy2(path, dest)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        if existing is not None:
            conn.execute(
                "UPDATE source_file SET page_count = ?, extraction_method = ?, "
                "ocr_confidence = ?, raw_text = ?, ingested_at = ? WHERE id = ?",
                (ex.page_count, ex.method, ex.confidence, ex.raw_text, now, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO source_file (sha256, original_name, archive_path, media_type, "
                "page_count, extraction_method, ocr_confidence, raw_text, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    digest,
                    path.name,
                    str(dest),
                    ex.media_type,
                    ex.page_count,
                    ex.method,
                    ex.confidence,
                    ex.raw_text,
                    now,
                ),
            )
        source_id = conn.execute(
            "SELECT id FROM source_file WHERE sha256 = ?", (digest,)
        ).fetchone()["id"]
        parse_source_file(conn, int(source_id))
        return IngestOutcome.NEW
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as e:
        logger.warning("ingest failed for %s: %s", path, e)
        return IngestOutcome.FAILED
