"""
PDF page -> image rendering (Path B: for scanned PDFs / vision extraction).
"""

from __future__ import annotations

import base64
from pathlib import Path

import pymupdf as fitz  # PyMuPDF

from config import get_logger

logger = get_logger(__name__)


def render_page_to_png_bytes(pdf_path: str | Path, dpi: int, page_number: int = 0) -> bytes:
    """Render a single PDF page to PNG bytes at the given DPI."""
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_number]
        zoom = dpi / 72.0  # PDF base is 72 DPI
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix)
        png_bytes = pix.tobytes("png")
    finally:
        doc.close()

    logger.info(
        "Rendered '%s' page %d to PNG at %d DPI (%d bytes)",
        Path(pdf_path).name, page_number, dpi, len(png_bytes),
    )
    return png_bytes


def png_bytes_to_base64(png_bytes: bytes) -> str:
    return base64.b64encode(png_bytes).decode("utf-8")


def image_file_to_base64(image_path: str | Path) -> tuple[str, str]:
    """
    Read an image file (png/jpg/jpeg) and return (base64_data, media_type).
    """
    path = Path(image_path)
    ext = path.suffix.lower()
    media_type = "image/png" if ext == ".png" else "image/jpeg"

    data = path.read_bytes()
    return base64.b64encode(data).decode("utf-8"), media_type
