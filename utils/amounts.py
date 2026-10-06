"""
Numeric / currency normalization.

Note: GPT-4.1 is instructed (see prompts/invoice_extraction.py) to already
strip currency symbols and separators before returning numbers, since the
schema requires numeric types. These helpers exist as a deterministic
safety net for any string-ish leftovers and for detecting currency symbols
when `currency` wasn't explicitly identified.
"""

from __future__ import annotations

import re

_CURRENCY_SYMBOL_MAP = {
    "₹": "INR",
    "rs.": "INR",
    "rs": "INR",
    "inr": "INR",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
}


def clean_numeric_string(value: str | float | int | None) -> float | None:
    """Strip currency symbols / thousands separators from a numeric-ish string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    stripped = value.strip()
    if not stripped:
        return None

    # Remove currency symbols/codes and any non numeric characters except
    # digits, minus sign, and decimal point.
    stripped = re.sub(r"[^\d\-.]", "", stripped)
    if not stripped or stripped in {"-", "."}:
        return None

    try:
        return float(stripped)
    except ValueError:
        return None


def detect_currency_from_text(text: str) -> str | None:
    lowered = text.lower()
    for symbol, code in _CURRENCY_SYMBOL_MAP.items():
        if symbol in lowered:
            return code
    return None
