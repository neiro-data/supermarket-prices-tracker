import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from price_tracker.ingest import extract as extract_mod
from price_tracker.ingest.ocr import OcrResult
from price_tracker.ingest.service import IngestOutcome, ingest_file


class FakeOcrEngine:
    def __init__(self, text: str = "REWE\nMilch 1,49 B", confidence: float = 90.0) -> None:
        self.text, self.confidence, self.calls = text, confidence, 0

    def image_to_text(self, img: Image.Image) -> OcrResult:
        self.calls += 1
        return OcrResult(self.text, self.confidence)


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    p = tmp_path / "bon.png"
    Image.new("RGB", (200, 300), "white").save(p)
    return p


def test_new_then_duplicate(db: sqlite3.Connection, photo: Path, tmp_path: Path) -> None:
    engine, archive = FakeOcrEngine(), tmp_path / "archive"
    assert ingest_file(db, photo, engine, archive) is IngestOutcome.NEW
    assert ingest_file(db, photo, engine, archive) is IngestOutcome.DUPLICATE
    row = db.execute(
        "SELECT media_type, extraction_method, ocr_confidence, raw_text, archive_path "
        "FROM source_file"
    ).fetchone()
    assert (row["media_type"], row["extraction_method"]) == ("image", "ocr")
    assert row["ocr_confidence"] == 90.0
    assert "Milch 1,49" in row["raw_text"]
    assert Path(row["archive_path"]).exists()
    assert engine.calls == 1


def test_force_reextracts(db: sqlite3.Connection, photo: Path, tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    ingest_file(db, photo, FakeOcrEngine("old"), archive)
    assert ingest_file(db, photo, FakeOcrEngine("new text"), archive, force=True) is (
        IngestOutcome.NEW
    )
    rows = db.execute("SELECT raw_text FROM source_file").fetchall()
    assert [r["raw_text"] for r in rows] == ["new text"]


def test_unsupported_file_fails(db: sqlite3.Connection, tmp_path: Path) -> None:
    bad = tmp_path / "x.txt"
    bad.write_text("hi")
    assert ingest_file(db, bad, FakeOcrEngine(), tmp_path / "a") is IngestOutcome.FAILED


def test_pdf_text_layer_skips_ocr(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "ebon.pdf"
    pdf.write_bytes(b"%PDF-fake")
    text = "REWE\nA 1,00\nB 2,00\nC 3,00\nSUMME 6,00"
    monkeypatch.setattr(extract_mod, "extract_text_layer", lambda p: [text])
    engine = FakeOcrEngine()
    assert ingest_file(db, pdf, engine, tmp_path / "a") is IngestOutcome.NEW
    row = db.execute("SELECT extraction_method, ocr_confidence, page_count FROM source_file")
    r = row.fetchone()
    assert (r["extraction_method"], r["ocr_confidence"], r["page_count"]) == ("text_layer", None, 1)
    assert engine.calls == 0


def test_pdf_without_text_falls_back_to_ocr(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(extract_mod, "extract_text_layer", lambda p: [""])
    monkeypatch.setattr(
        extract_mod, "render_pages", lambda p: [Image.new("RGB", (100, 100), "white")] * 2
    )
    engine = FakeOcrEngine()
    assert ingest_file(db, pdf, engine, tmp_path / "a") is IngestOutcome.NEW
    r = db.execute("SELECT extraction_method, page_count FROM source_file").fetchone()
    assert (r["extraction_method"], r["page_count"]) == ("ocr", 2)
    assert engine.calls == 2
