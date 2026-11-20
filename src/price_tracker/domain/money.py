"""Money helpers: integer cents only, never float."""

import re

_CUR_PREFIX_RE = re.compile(r"^(?:€|EUR)\s*", re.IGNORECASE)
_CUR_SUFFIX_RE = re.compile(r"\s*(?:€|EUR)$", re.IGNORECASE)
# Dot thousands groups are only accepted together with a comma decimal (1.234 is ambiguous).
_AMOUNT_RE = re.compile(
    r"(?:(?P<grouped>[0-9]{1,3}(?:\.[0-9]{3})+),(?P<dec_g>[0-9]{1,2})"
    r"|(?P<plain>[0-9]+)(?:,(?P<dec>[0-9]{1,2}))?)"
)


def parse_eur(text: str) -> int:
    """Parse a strict German-style amount to cents.

    Accepts an optional single sign (leading or trailing `-`), an optional `€`/`EUR`
    prefix or suffix, digits with optional `.` thousands separators (only together with
    a comma decimal) and 1-2 comma decimals. Raises ValueError otherwise.
    """
    s = text.strip()
    s = _CUR_PREFIX_RE.sub("", s, count=1)
    s = _CUR_SUFFIX_RE.sub("", s, count=1)
    s = s.strip()
    negative = False
    if s.startswith("-"):
        negative, s = True, s[1:]
    elif s.endswith("-"):
        negative, s = True, s[:-1]
    m = _AMOUNT_RE.fullmatch(s.strip())
    if m is None:
        raise ValueError(f"not a money amount: {text!r}")
    whole = (m["grouped"] or m["plain"]).replace(".", "")
    dec = (m["dec_g"] or m["dec"] or "").ljust(2, "0")
    cents = int(whole) * 100 + int(dec)
    return -cents if negative else cents


def format_eur(cents: int) -> str:
    """Format cents as `1.234,56 €` style (German), e.g. -30 -> `-0,30 €`."""
    sign = "-" if cents < 0 else ""
    euros, rest = divmod(abs(cents), 100)
    return f"{sign}{euros:,}".replace(",", ".") + f",{rest:02d} €"
