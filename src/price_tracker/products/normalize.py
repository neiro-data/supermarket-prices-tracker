"""Product-name normalization: fold, strip pack sizes and punctuation, expand abbreviations."""

import re
import tomllib
from functools import lru_cache
from importlib import resources

from price_tracker.domain.models import Unit
from price_tracker.products.units import PackSize, parse_pack_size, strip_pack_tokens

_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_DECIMAL = re.compile(r"(?<=\d)[.,](?=\d)")
_SEPARATORS = re.compile(r"[^\w%\-\x00]+|_")


@lru_cache(maxsize=1)
def load_abbreviations() -> dict[str, str]:
    text = resources.files("price_tracker").joinpath("data/abbreviations.toml").read_text("utf-8")
    return {k.lower(): v.lower() for k, v in tomllib.loads(text)["abbreviations"].items()}


def _expand(token: str, abbr: dict[str, str]) -> str:
    if token in abbr:
        return abbr[token]
    return " ".join(abbr.get(part, part) for part in token.split("-"))


def normalize_name(raw: str) -> str:
    """Fold case/umlauts, drop pack sizes and punctuation (keep `%`), expand abbreviations."""
    text = strip_pack_tokens(raw.lower().translate(_FOLD))
    text = _SEPARATORS.sub(" ", _DECIMAL.sub("\x00", text))
    abbr = load_abbreviations()
    out = " ".join(_expand(tok, abbr) for tok in text.split())
    return " ".join(out.replace("-", " ").replace("\x00", ".").split())


PACK_SEP = " #"
_PACK_SUFFIX = {Unit.KG: "g", Unit.L: "ml", Unit.PC: "pc"}


def pack_token(pack: PackSize) -> str:
    """Canonical pack token: `330ml` and `0,33l` both give `330ml`; `1kg` gives `1000g`."""
    return f"{pack.amount_milli}{_PACK_SUFFIX[pack.unit]}"


def identity_key(raw: str) -> str:
    """Chain-product identity: normalized name plus canonical pack token (`cola #330ml`)."""
    name = normalize_name(raw) or raw.strip().lower()
    pack = parse_pack_size(raw)
    return f"{name}{PACK_SEP}{pack_token(pack)}" if pack else name


def key_name(key: str) -> str:
    """Identity key without the pack token (used for fuzzy comparison)."""
    return key.split(PACK_SEP, 1)[0]
