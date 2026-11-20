"""Parser protocol and profile registry."""

from collections.abc import Callable
from typing import Protocol

from price_tracker.domain.models import ParsedReceipt


class ReceiptParser(Protocol):
    """A chain-specific parser."""

    profile: str

    def parse(self, text: str, chain_code: str | None) -> ParsedReceipt: ...


PARSERS: dict[str, ReceiptParser] = {}


def register[T: ReceiptParser](cls: Callable[[], T]) -> Callable[[], T]:
    """Class decorator: instantiate and register the parser under its `profile`."""
    inst = cls()
    PARSERS[inst.profile] = inst
    return cls
