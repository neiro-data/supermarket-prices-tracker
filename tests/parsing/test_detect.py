import pytest

from price_tracker.parsing.detect import detect_chain, load_chain_infos


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("REWE Markt GmbH\nStr. 1", "rewe"),
        ("Lidl sagt Danke", "lidl"),
        ("ALDI SÜD\nTeststr", "aldi_sued"),
        ("ALDI SUED", "aldi_sued"),
        ("aldi-nord filiale", "aldi_nord"),
        ("NETTO Marken-Discount", "netto"),
        ("Unbekannter Laden", None),
    ],
)
def test_detect_chain(text: str, code: str | None) -> None:
    found = detect_chain(text, load_chain_infos())
    assert (found.code if found else None) == code


def test_keyword_beyond_header_ignored() -> None:
    text = "\n".join(["x"] * 20 + ["REWE"])
    assert detect_chain(text, load_chain_infos()) is None


def test_aldi_profiles() -> None:
    found = detect_chain("ALDI SÜD", load_chain_infos())
    assert found is not None and found.parser_profile == "aldi"
