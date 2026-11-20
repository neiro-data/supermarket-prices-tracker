"""Chain detection from receipt header text and chain config."""

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

from price_tracker.parsing.common import fold


@dataclass(frozen=True, slots=True)
class ChainInfo:
    """Chain code, parser profile and detection keywords (from chains.toml)."""

    code: str
    parser_profile: str
    keywords: tuple[str, ...]


def load_chain_infos() -> list[ChainInfo]:
    """Read bundled chains.toml."""
    raw = resources.files("price_tracker").joinpath("data", "chains.toml").read_text("utf-8")
    return [
        ChainInfo(c["code"], c["parser_profile"], tuple(c.get("detect_keywords", [])))
        for c in tomllib.loads(raw)["chains"]
    ]


def _norm(text: str) -> str:
    return fold(text).replace("-", " ").replace("_", " ")


def detect_chain(text: str, chains: Sequence[ChainInfo], head: int = 15) -> ChainInfo | None:
    """Chain whose keyword appears in the first `head` lines (case/umlaut-insensitive).

    The longest matching keyword wins, so `aldi sued` beats a bare `aldi`, and
    `netto marken-discount` beats `netto`.
    """
    header = _norm("\n".join(text.splitlines()[:head]))
    best: tuple[int, ChainInfo] | None = None
    for chain in chains:
        for kw in chain.keywords:
            k = _norm(kw)
            if k in header and (best is None or len(k) > best[0]):
                best = (len(k), chain)
    return best[1] if best else None
