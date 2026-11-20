"""`generic` receipt profile."""

from price_tracker.domain.models import ParsedReceipt
from price_tracker.parsing.base import register
from price_tracker.parsing.engine import ProfileOptions, parse_receipt

_OPTIONS = ProfileOptions(detail="after")


@register
class GenericParser:
    profile = "generic"

    def parse(self, text: str, chain_code: str | None) -> ParsedReceipt:
        return parse_receipt(text, chain_code, self.profile, _OPTIONS)
