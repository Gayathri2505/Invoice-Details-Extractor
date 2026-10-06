"""
File validation and type detection.

Responsible ONLY for: does this file exist, is it a supported type, and
(for PDFs) is it a single page as expected in this MVP. No extraction
logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from config import SUPPORTED_EXTENSIONS, get_logger

logger = get_logger(__name__)


class UnsupportedDocumentError(Exception):
    pass


@dataclass
class LoadedDocument:
    path: Path
    file_type: str          # "pdf" | "image"
    page_count: int         # 1 for images


def load_document(file_path: str | Path) -> LoadedDocument:
    path = Path(file_path)

    if not path.exists():
        raise UnsupportedDocumentError(f"File not found: {path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedDocumentError(
            f"Unsupported file extension '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    if ext == ".pdf":
        try:
            doc = fitz.open(str(path))
        except Exception as e:
            raise UnsupportedDocumentError(f"Corrupt or unreadable PDF: {e}") from e

        page_count = doc.page_count
        doc.close()

        if page_count == 0:
            raise UnsupportedDocumentError("PDF has zero pages.")
        if page_count > 1:
            # MVP is single-page only. We don't fail hard — we log and let the
            # caller decide (e.g. process page 1 only, or reject). Multi-page
            # support is a planned follow-up phase.
            logger.warning(
                "PDF '%s' has %d pages; this MVP only processes single-page "
                "invoices. Only page 1 will be used.",
                path.name, page_count,
            )

        logger.info("Loaded PDF '%s' (%d page(s))", path.name, page_count)
        return LoadedDocument(path=path, file_type="pdf", page_count=page_count)

    # Image formats: png, jpg, jpeg
    logger.info("Loaded image '%s'", path.name)
    return LoadedDocument(path=path, file_type="image", page_count=1)
