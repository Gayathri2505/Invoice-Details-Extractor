"""
Date normalization: various raw date strings -> ISO 8601 (YYYY-MM-DD).
"""

from __future__ import annotations

from datetime import datetime

import dateparser

from config import get_logger

logger = get_logger(__name__)

# Ordered by how commonly we expect to see them on Indian / international
# invoices. Add more formats here as real samples reveal them (Phase 9).
_CANDIDATE_FORMATS = [
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%b-%Y",
    "%d.%m.%Y",
    "%m/%d/%Y",
]

# Fallback for anything the explicit formats above don't match: written or
# non-Latin-script dates (Japanese, Chinese, Korean, Arabic, French,
# German, ...). Rather than hardcoding one literal format per language,
# dateparser auto-detects the language/calendar and parses it. Strict
# parsing rejects partial/garbage text (e.g. an invoice number) instead of
# guessing a date out of it.
_DATEPARSER_SETTINGS = {"STRICT_PARSING": True}


def normalize_date(raw: str | None) -> tuple[str | None, bool]:
    """
    Returns (iso_date_or_none, was_ambiguous_or_failed).

    was_ambiguous_or_failed=True means the caller should raise a review
    warning: either parsing failed outright, or the format was ambiguous
    (e.g. "01/02/2026" could be Jan 2 or Feb 1).
    """
    if not raw or not raw.strip():
        return None, False

    cleaned = raw.strip()

    parsed: datetime | None = None
    for fmt in _CANDIDATE_FORMATS:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            break
        except ValueError:
            continue

    if parsed is not None:
        ambiguous = _is_ambiguous_numeric_date(cleaned)
        return parsed.strftime("%Y-%m-%d"), ambiguous

    # None of the explicit fast-path formats matched — try generic
    # multilingual parsing before giving up.
    try:
        parsed = dateparser.parse(cleaned, settings=_DATEPARSER_SETTINGS)
    except Exception:
        logger.exception("dateparser raised while parsing date '%s'", raw)
        parsed = None

    if parsed is None:
        logger.warning("Could not parse date '%s' with any known format", raw)
        return None, True

    # dateparser already resolved the language/calendar's own ordering, so
    # there's no day/month ambiguity left to flag here.
    return parsed.strftime("%Y-%m-%d"), False


def _is_ambiguous_numeric_date(cleaned: str) -> bool:
    """
    A numeric date like 03/04/2026 is ambiguous between DD/MM and MM/DD
    when both components could plausibly be a day (<=12). Flag for review
    rather than silently guessing.
    """
    for sep in ("/", "-", "."):
        parts = cleaned.split(sep)
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            a, b, _ = parts
            if len(a) <= 2 and len(b) <= 2:
                try:
                    a_i, b_i = int(a), int(b)
                except ValueError:
                    return False
                if a_i <= 12 and b_i <= 12 and a_i != b_i:
                    return True
    return False