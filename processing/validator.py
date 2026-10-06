"""
Deterministic validation — arithmetic and format checks that GPT should
never be trusted to self-verify (Section 14 of the design doc).

Produces warnings; never silently mutates extracted values.
"""

from __future__ import annotations

import re

from models.invoice_schema import FieldWarning, Invoice

# Tolerance for floating point / rounding differences (in currency units).
_AMOUNT_TOLERANCE = 1.0

_GST_PATTERN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


def validate_invoice(invoice: Invoice) -> list[FieldWarning]:
    warnings: list[FieldWarning] = []

    warnings.extend(_validate_totals(invoice))
    warnings.extend(_validate_tax_breakdown(invoice))
    warnings.extend(_validate_line_items(invoice))
    warnings.extend(_validate_gst(invoice))
    warnings.extend(_validate_required_fields(invoice))
    warnings.extend(_validate_damage_notes(invoice))

    return warnings


def _validate_totals(invoice: Invoice) -> list[FieldWarning]:
    f = invoice.financials
    warnings: list[FieldWarning] = []

    components = [f.subtotal, f.tax, f.shipping, f.other_charges, f.round_off]
    if f.subtotal is None or f.total_amount is None:
        return warnings  # not enough data to validate

    computed = f.subtotal
    computed -= f.discount or 0.0
    computed += f.shipping or 0.0
    computed += f.tax or 0.0
    computed += f.other_charges or 0.0
    computed += f.round_off or 0.0

    diff = abs(computed - f.total_amount)
    if diff > _AMOUNT_TOLERANCE:
        warnings.append(
            FieldWarning(
                field="financials.total_amount",
                message=(
                    f"Total mismatch: computed {computed:.2f} from subtotal/discount/"
                    f"shipping/tax/other_charges/round_off, but extracted total is "
                    f"{f.total_amount:.2f} (diff {diff:.2f})."
                ),
                severity="error",
                category="data_mismatch",
            )
        )
    return warnings


def _validate_tax_breakdown(invoice: Invoice) -> list[FieldWarning]:
    f = invoice.financials
    warnings: list[FieldWarning] = []

    if f.tax is None:
        return warnings

    components = [c for c in (f.cgst, f.sgst, f.igst) if c is not None]
    if not components:
        return warnings

    computed_tax = sum(components)
    diff = abs(computed_tax - f.tax)
    if diff > _AMOUNT_TOLERANCE:
        warnings.append(
            FieldWarning(
                field="financials.tax",
                message=(
                    f"Tax mismatch: CGST+SGST+IGST = {computed_tax:.2f}, but "
                    f"extracted total tax is {f.tax:.2f} (diff {diff:.2f})."
                ),
                severity="warning",
                category="data_mismatch",
            )
        )
    return warnings


def _validate_line_items(invoice: Invoice) -> list[FieldWarning]:
    warnings: list[FieldWarning] = []

    for idx, item in enumerate(invoice.line_items):
        if item.quantity is None or item.unit_price is None or item.amount is None:
            continue

        expected = (item.quantity * item.unit_price) - (item.discount or 0.0)
        diff = abs(expected - item.amount)
        if diff > _AMOUNT_TOLERANCE:
            warnings.append(
                FieldWarning(
                    field=f"line_items[{idx}].amount",
                    message=(
                        f"Line item {idx} amount mismatch: qty({item.quantity}) x "
                        f"unit_price({item.unit_price}) - discount({item.discount or 0}) "
                        f"= {expected:.2f}, but extracted amount is {item.amount:.2f}."
                    ),
                    severity="warning",
                    category="data_mismatch",
                )
            )

    if invoice.line_items and invoice.financials.subtotal is not None:
        items_sum = sum(i.amount for i in invoice.line_items if i.amount is not None)
        diff = abs(items_sum - invoice.financials.subtotal)
        if diff > _AMOUNT_TOLERANCE:
            warnings.append(
                FieldWarning(
                    field="financials.subtotal",
                    message=(
                        f"Sum of line item amounts ({items_sum:.2f}) does not match "
                        f"extracted subtotal ({invoice.financials.subtotal:.2f})."
                    ),
                    severity="warning",
                    category="data_mismatch",
                )
            )

    return warnings


def _validate_gst(invoice: Invoice) -> list[FieldWarning]:
    warnings: list[FieldWarning] = []
    for label, party in (("vendor", invoice.vendor), ("customer", invoice.customer)):
        if party.gst and not _GST_PATTERN.match(party.gst.strip().upper()):
            warnings.append(
                FieldWarning(
                    field=f"{label}.gst",
                    message=f"GSTIN '{party.gst}' does not match the expected GST format.",
                    severity="warning",
                    category="data_mismatch",
                )
            )
    return warnings


def _validate_damage_notes(invoice: Invoice) -> list[FieldWarning]:
    """Surface any damage remarks the LLM found (line-item-specific or general)
    as review warnings, so damaged-goods invoices get flagged same as amount
    mismatches."""
    warnings: list[FieldWarning] = []

    for idx, item in enumerate(invoice.line_items):
        if item.damage_note:
            label = item.description or f"line item {idx}"
            warnings.append(
                FieldWarning(
                    field=f"line_items[{idx}].damage_note",
                    message=f"Possible damaged goods on '{label}': \"{item.damage_note}\"",
                    severity="warning",
                    category="damage_flag",
                )
            )

    for note in invoice.damage_remarks:
        warnings.append(
            FieldWarning(
                field="damage_remarks",
                message=f"This invoice mentions damaged goods: \"{note}\"",
                severity="warning",
                category="damage_flag",
            )
        )

    return warnings


def _validate_required_fields(invoice: Invoice) -> list[FieldWarning]:
    warnings: list[FieldWarning] = []
    required = {
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "financials.total_amount": invoice.financials.total_amount,
    }
    for field_name, value in required.items():
        if value is None:
            warnings.append(
                FieldWarning(
                    field=field_name,
                    message="Required field is missing.",
                    severity="error",
                    category="missing_field",
                )
            )
    return warnings