"""
Digital PDF text extraction (Path A: PyMuPDF).

Only handles page 1 for this MVP (single-page invoices).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from config import get_logger

logger = get_logger(__name__)


def extract_page_text(pdf_path: str | Path, page_number: int = 0) -> str:
    """Extract raw text from a single PDF page, preserving rough reading order."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_number]
        # "text" mode gives a reasonable reading-order approximation, which is
        # what GPT-4.1 needs to reconstruct labels/values/tables semantically.
        text = page.get_text("text")
    finally:
        doc.close()
    return text


def has_reliable_text(pdf_path: str | Path, min_chars: int, page_number: int = 0) -> bool:
    """
    Heuristic: does this PDF page have a usable embedded text layer?

    A low character count usually means either a scanned page with no text
    layer, or a garbage OCR text layer — either way, we should prefer the
    vision path instead of trusting this text.
    """
    text = extract_page_text(pdf_path, page_number)
    stripped = text.strip()
    reliable = len(stripped) >= min_chars
    logger.info(
        "Digital text check on '%s' page %d: %d chars, reliable=%s",
        Path(pdf_path).name, page_number, len(stripped), reliable,
    )
    return reliable
