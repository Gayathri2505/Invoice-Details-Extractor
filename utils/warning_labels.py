"""
Turns a FieldWarning.field path (e.g. "line_items[1].amount",
"financials.total_amount", "vendor.gst") into a human-readable label for
display (Review Notes card, Excel "Review Notes" sheet).

This is a DISPLAY-ONLY concern — FieldWarning.field itself stays as the
precise, machine-parseable path (processing/validator.py keeps writing it
that way, and anything that needs to programmatically locate the field
should keep using the raw path). Only the label shown to a person goes
through here.
"""

from __future__ import annotations

import re

from models.invoice_schema import Invoice

_LINE_ITEM_RE = re.compile(r"^line_items\[(\d+)\]\.(\w+)$")

# Known path prefixes -> a human prefix (empty string means "drop it",
# e.g. "financials.total_amount" doesn't need a "Financials" prefix).
_SEGMENT_PREFIX_LABELS = {
    "financials": "",
    "vendor": "Vendor",
    "customer": "Customer",
}

# Field-name -> label overrides for cases the generic title-casing below
# wouldn't get quite right.
_FIELD_LABELS = {
    "gst": "GST",
    "amount": "Amount",
    "damage_note": "Damage Note",
    "damage_remarks": "Damage Remarks",
    "total_amount": "Total Amount",
    "subtotal": "Subtotal",
    "tax": "Tax",
    "invoice_date": "Invoice Date",
    "invoice_number": "Invoice Number",
    "due_date": "Due Date",
}


# Display order + label for each warning category, used to group the
# Review Notes card (app.py) and the "Review Notes" export sheets.
CATEGORY_ORDER = ["damage_flag", "data_mismatch", "missing_field"]
CATEGORY_LABELS = {
    "damage_flag": "Damage Flags",
    "data_mismatch": "Data Mismatches",
    "missing_field": "Missing Fields",
}


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category.replace("_", " ").title())


def _label_for(field_name: str) -> str:
    return _FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())


def humanize_warning_field(field: str, invoice: Invoice | None = None) -> str:
    """
    Best-effort human-readable label for a FieldWarning.field path.
    Never raises — falls back to a lightly cleaned-up version of the raw
    path if the shape isn't recognized, so display code never breaks on
    an unexpected/future field path.
    """
    try:
        match = _LINE_ITEM_RE.match(field)
        if match:
            idx, sub_field = int(match.group(1)), match.group(2)
            label = f"Line item {idx + 1}"
            if invoice is not None and 0 <= idx < len(invoice.line_items):
                desc = invoice.line_items[idx].description
                if desc:
                    label += f" ({desc})"
            return f"{label} → {_label_for(sub_field)}"

        if "." in field:
            prefix, sub_field = field.split(".", 1)
            prefix_label = _SEGMENT_PREFIX_LABELS.get(prefix, prefix.replace("_", " ").title())
            sub_label = _label_for(sub_field)
            return f"{prefix_label} {sub_label}".strip()

        return _label_for(field)
    except Exception:
        return field.replace("_", " ").replace(".", " ").strip().title() or field