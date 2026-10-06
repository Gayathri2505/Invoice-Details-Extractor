"""
Core AI extraction module.

Uses OpenAI's Structured Outputs (response_format = Pydantic model) so
GPT-4.1's response is schema-guaranteed to match `Invoice` — no
free-text JSON parsing / repair loop needed. This is the recommended
approach over prompting for raw JSON.
"""

from __future__ import annotations

import time
from pathlib import Path

from openai import APITimeoutError, AzureOpenAI

from config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_MAX_RETRIES,
    AZURE_OPENAI_TIMEOUT_SECONDS,
    GPT_MODEL,
    PDF_RENDER_DPI,
    get_logger,
)
from extraction.pdf_renderer import (
    image_file_to_base64,
    png_bytes_to_base64,
    render_page_to_png_bytes,
)
from models.invoice_schema import Invoice
from prompts.invoice_extraction import SYSTEM_PROMPT, build_user_content_text
from usage.tracker import log_usage

logger = get_logger(__name__)

_client: AzureOpenAI | None = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        if not AZURE_OPENAI_API_KEY or not AZURE_OPENAI_ENDPOINT:
            raise RuntimeError(
                "AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT not set. Add them to your .env file."
            )
        _client = AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION,
            timeout=AZURE_OPENAI_TIMEOUT_SECONDS,
            max_retries=AZURE_OPENAI_MAX_RETRIES,
        )
    return _client


def _build_user_content(raw_text: str | None, image_b64: str | None, media_type: str) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": build_user_content_text(raw_text)}]
    if image_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
            }
        )
    return content


def _call_parse(client: AzureOpenAI, messages: list[dict]):
    """Wraps the structured-output completion call with elapsed-time
    logging and a clear, fast error on timeout — instead of the SDK's
    default 10-minute silent hang, this surfaces a real error at
    AZURE_OPENAI_TIMEOUT_SECONDS (see config.py) so the UI doesn't look
    stuck forever."""
    start = time.monotonic()
    try:
        completion = client.beta.chat.completions.parse(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=messages,
            response_format=Invoice,
            temperature=0,
        )
    except APITimeoutError:
        elapsed = time.monotonic() - start
        logger.error(
            "Azure OpenAI request timed out after %.1fs (limit=%.0fs). "
            "This usually means a network/connectivity issue reaching "
            "Azure — check the endpoint/network, or raise "
            "AZURE_OPENAI_TIMEOUT_SECONDS if the document is just large.",
            elapsed, AZURE_OPENAI_TIMEOUT_SECONDS,
        )
        raise RuntimeError(
            f"Azure OpenAI request timed out after {elapsed:.0f}s. "
            "Check your network connection to Azure and try again."
        ) from None
    else:
        logger.info("Azure OpenAI request completed in %.1fs", time.monotonic() - start)
        return completion


def extract_invoice_from_text(
    raw_text: str, session_id: str | None = None, file_name: str | None = None
) -> tuple[Invoice, str, dict | None]:
    """Text-only extraction path (digital PDF with reliable embedded text)."""
    client = _get_client()
    logger.info("Calling %s (text-only extraction path)", GPT_MODEL)

    completion = _call_parse(client, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_content(raw_text, None, "")},
    ])
    usage_record = _track(completion, session_id, file_name, "digital_pdf", _safe_parsed(completion))
    invoice = _unwrap_parsed(completion)
    return invoice, "digital_pdf", usage_record


def extract_invoice_from_image(
    image_path: str | Path, session_id: str | None = None, file_name: str | None = None
) -> tuple[Invoice, str, dict | None]:
    """Vision-only extraction path (scanned PDF page image, or a plain image file)."""
    client = _get_client()
    image_b64, media_type = image_file_to_base64(image_path)
    logger.info("Calling %s (vision-only extraction path)", GPT_MODEL)

    completion = _call_parse(client, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_content(None, image_b64, media_type)},
    ])
    usage_record = _track(completion, session_id, file_name, "vision", _safe_parsed(completion))
    invoice = _unwrap_parsed(completion)
    return invoice, "vision", usage_record


def extract_invoice_from_pdf_page_image(
    pdf_path: str | Path,
    page_number: int = 0,
    session_id: str | None = None,
    file_name: str | None = None,
) -> tuple[Invoice, str, dict | None]:
    """Render a PDF page to an image and run the vision-only path on it."""
    png_bytes = render_page_to_png_bytes(pdf_path, dpi=PDF_RENDER_DPI, page_number=page_number)
    image_b64 = png_bytes_to_base64(png_bytes)

    client = _get_client()
    logger.info("Calling %s (vision-only path, rendered PDF page)", GPT_MODEL)

    completion = _call_parse(client, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_content(None, image_b64, "image/png")},
    ])
    usage_record = _track(completion, session_id, file_name, "vision", _safe_parsed(completion))
    invoice = _unwrap_parsed(completion)
    return invoice, "vision", usage_record


def extract_invoice_from_text_and_pdf_image(
    raw_text: str,
    pdf_path: str | Path,
    page_number: int = 0,
    session_id: str | None = None,
    file_name: str | None = None,
) -> tuple[Invoice, str, dict | None]:
    """Combined path: embedded text + rendered page image together (Section 8)."""
    png_bytes = render_page_to_png_bytes(pdf_path, dpi=PDF_RENDER_DPI, page_number=page_number)
    image_b64 = png_bytes_to_base64(png_bytes)

    client = _get_client()
    logger.info("Calling %s (combined text + vision extraction path)", GPT_MODEL)

    completion = _call_parse(client, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_content(raw_text, image_b64, "image/png")},
    ])
    usage_record = _track(completion, session_id, file_name, "text_plus_vision", _safe_parsed(completion))
    invoice = _unwrap_parsed(completion)
    return invoice, "text_plus_vision", usage_record


def _safe_parsed(completion):
    """Best-effort peek at the parsed output, for usage logging only. Never raises."""
    try:
        return completion.choices[0].message.parsed
    except Exception:
        return None


def _track(
    completion,
    session_id: str | None,
    file_name: str | None,
    extraction_path: str,
    extracted_content=None,
) -> dict | None:
    """Log + persist token usage (and, if provided, the extracted output) for one completion call. Never raises."""
    try:
        return log_usage(
            session_id=session_id or "unknown",
            model=GPT_MODEL,
            usage=getattr(completion, "usage", None),
            file_name=file_name,
            extraction_path=extraction_path,
            call_type="extraction",
            extracted_content=extracted_content,
        )
    except Exception:
        logger.exception("Failed to record token usage (extraction still succeeded)")
        return None


def _unwrap_parsed(completion) -> Invoice:
    message = completion.choices[0].message

    if getattr(message, "refusal", None):
        raise RuntimeError(f"Model refused extraction: {message.refusal}")

    parsed = message.parsed
    if parsed is None:
        raise RuntimeError("Model did not return a parsed structured response.")

    logger.info("GPT extraction completed")
    return parsed