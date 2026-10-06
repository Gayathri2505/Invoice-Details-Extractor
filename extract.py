"""
Milestone 1: standalone invoice extraction engine.

Usage:
    python extract.py path/to/invoice.pdf
    python extract.py path/to/invoice.jpg -o output.json

This module can also be imported and used directly:

    from extract import extract_invoice
    result = extract_invoice("invoice.pdf")
    print(result.invoice.model_dump())
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config import MIN_RELIABLE_TEXT_CHARS, get_logger
from extraction.document_loader import UnsupportedDocumentError, load_document
from extraction.gpt_invoice_extractor import (
    extract_invoice_from_image,
    extract_invoice_from_pdf_page_image,
    extract_invoice_from_text,
    extract_invoice_from_text_and_pdf_image,
)
from extraction.pdf_extractor import extract_page_text, has_reliable_text
from models.invoice_schema import ExtractionResult
from processing.normalizer import normalize_invoice
from processing.validator import validate_invoice
from usage.tracker import init_db, new_session_id

init_db()

logger = get_logger(__name__)


def extract_invoice(
    file_path: str | Path,
    use_combined_path_for_digital: bool = True,
    session_id: str | None = None,
) -> ExtractionResult:
    """
    Run the full pipeline: load -> extract (GPT-4.1) -> normalize -> validate.

    `use_combined_path_for_digital` controls whether digital PDFs with
    reliable text also get the page image attached (Section 8: text + vision
    combined strategy). Set False to save cost and use text-only when text
    is reliable.

    `session_id` groups this call's token usage with others from the same
    user session for tracking/billing purposes (see usage/tracker.py). If
    omitted, a one-off id is generated so the call is still logged.
    """
    path = Path(file_path)
    doc = load_document(path)
    session_id = session_id or new_session_id()
    file_name = path.name

    if doc.file_type == "pdf":
        if has_reliable_text(path, MIN_RELIABLE_TEXT_CHARS):
            raw_text = extract_page_text(path)
            if use_combined_path_for_digital:
                invoice, extraction_path, usage_record = extract_invoice_from_text_and_pdf_image(
                    raw_text, path, session_id=session_id, file_name=file_name
                )
            else:
                invoice, extraction_path, usage_record = extract_invoice_from_text(
                    raw_text, session_id=session_id, file_name=file_name
                )
        else:
            logger.info("No reliable embedded text; falling back to vision path")
            invoice, extraction_path, usage_record = extract_invoice_from_pdf_page_image(
                path, session_id=session_id, file_name=file_name
            )
    else:
        invoice, extraction_path, usage_record = extract_invoice_from_image(
            path, session_id=session_id, file_name=file_name
        )

    invoice, norm_warnings = normalize_invoice(invoice)
    validation_warnings = validate_invoice(invoice)

    all_warnings = norm_warnings + validation_warnings
    needs_review = any(w.severity == "error" for w in all_warnings) or bool(all_warnings)

    result = ExtractionResult(
        invoice=invoice,
        source_file=str(path),
        extraction_path=extraction_path,
        warnings=all_warnings,
        needs_review=needs_review,
        token_usage=usage_record,
    )

    logger.info(
        "Extraction complete for '%s' via '%s' path: %d warning(s), needs_review=%s",
        path.name, extraction_path, len(all_warnings), needs_review,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract structured data from an invoice.")
    parser.add_argument("invoice_path", help="Path to invoice PDF/PNG/JPG")
    parser.add_argument("-o", "--output", help="Output JSON path (default: <invoice_name>.json)")
    parser.add_argument(
        "--text-only", action="store_true",
        help="For digital PDFs with reliable text, skip attaching the page image (cheaper, faster).",
    )
    args = parser.parse_args()

    session_id = new_session_id()
    try:
        result = extract_invoice(
            args.invoice_path,
            use_combined_path_for_digital=not args.text_only,
            session_id=session_id,
        )
    except UnsupportedDocumentError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        logger.exception("Extraction failed")
        print(f"Extraction failed: {e}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else Path(args.invoice_path).with_suffix(".json")
    output_path.write_text(
        json.dumps(result.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Wrote: {output_path}")
    if result.warnings:
        print(f"\n{len(result.warnings)} warning(s):")
        for w in result.warnings:
            marker = "✕" if w.severity == "error" else "⚠"
            print(f"  {marker} [{w.field}] {w.message}")
    else:
        print("✓ No warnings.")

    from usage.tracker import get_session_summary

    usage = get_session_summary(session_id)
    print(
        f"\nTokens used: {usage['total_tokens']} "
        f"(prompt={usage['prompt_tokens']}, completion={usage['completion_tokens']}) "
        f"~${usage['estimated_cost']:.4f}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())