from price_tracker.ingest.pdf import has_usable_text


def test_usable_text() -> None:
    assert has_usable_text(["a\nb\nc\nd\ne 1,49"])


def test_unusable_text_too_few_lines_or_no_price() -> None:
    assert not has_usable_text(["a\nb 1,49"])
    assert not has_usable_text(["a\nb\nc\nd\ne\nf"])
    assert not has_usable_text([""])
