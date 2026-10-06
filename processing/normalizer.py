"""
Deterministic normalization applied AFTER GPT extraction and Pydantic
validation. GPT is not trusted to normalize dates/numbers itself
(see Section 10 of the design doc) — that happens here.
"""

from __future__ import annotations

from config import get_logger
from models.invoice_schema import ExtractionResult, FieldWarning, Invoice
from utils.amounts import clean_numeric_string
from utils.dates import normalize_date

logger = get_logger(__name__)

_NUMERIC_FINANCIAL_FIELDS = [
    "subtotal", "discount", "shipping", "other_charges",
    "cgst", "sgst", "igst", "tax", "round_off",
    "total_amount", "amount_paid", "balance_due",
]

_NUMERIC_LINE_ITEM_FIELDS = [
    "quantity", "unit_price", "discount", "tax_rate", "tax_amount", "amount",
]


def normalize_invoice(invoice: Invoice) -> tuple[Invoice, list[FieldWarning]]:
    warnings: list[FieldWarning] = []

    # --- Dates ---
    invoice.invoice_date, warnings_ = _normalize_and_warn(
        invoice.invoice_date, "invoice_date"
    )
    warnings.extend(warnings_)

    invoice.due_date, warnings_ = _normalize_and_warn(invoice.due_date, "due_date")
    warnings.extend(warnings_)

    # --- Financial numeric fields ---
    for field_name in _NUMERIC_FINANCIAL_FIELDS:
        raw_value = getattr(invoice.financials, field_name)
        cleaned = clean_numeric_string(raw_value)
        if raw_value is not None and cleaned is None:
            warnings.append(
                FieldWarning(
                    field=f"financials.{field_name}",
                    message=f"Could not parse numeric value: {raw_value!r}",
                    severity="warning",
                )
            )
        setattr(invoice.financials, field_name, cleaned)

    # --- Line items ---
    for idx, item in enumerate(invoice.line_items):
        for field_name in _NUMERIC_LINE_ITEM_FIELDS:
            raw_value = getattr(item, field_name)
            cleaned = clean_numeric_string(raw_value)
            if raw_value is not None and cleaned is None:
                warnings.append(
                    FieldWarning(
                        field=f"line_items[{idx}].{field_name}",
                        message=f"Could not parse numeric value: {raw_value!r}",
                        severity="warning",
                    )
                )
            setattr(item, field_name, cleaned)

        if item.description:
            item.description = _clean_text(item.description)

    # --- Text cleanup ---
    invoice.vendor.name = _clean_text(invoice.vendor.name)
    invoice.vendor.address = _clean_text(invoice.vendor.address)
    invoice.customer.name = _clean_text(invoice.customer.name)
    invoice.customer.address = _clean_text(invoice.customer.address)

    if invoice.currency:
        invoice.currency = invoice.currency.strip().upper()

    return invoice, warnings


def _normalize_and_warn(raw: str | None, field_name: str) -> tuple[str | None, list[FieldWarning]]:
    if raw is None:
        return None, []

    iso_date, problem = normalize_date(raw)
    warnings: list[FieldWarning] = []

    if iso_date is None:
        warnings.append(
            FieldWarning(
                field=field_name,
                message=f"Could not parse date value: {raw!r}. Left as null.",
                severity="warning",
            )
        )
    elif problem:
        warnings.append(
            FieldWarning(
                field=field_name,
                message=f"Date '{raw}' is ambiguous (day/month order unclear); please verify: {iso_date}",
                severity="warning",
            )
        )

    return iso_date, warnings


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())  # collapse whitespace/newlines
    return cleaned if cleaned else None
