"""
CSV export.

CSV is inherently flat, so we export two logical sections in one file:
a single header row of invoice-level fields, then the line items table.
This is the most common way accounting/finance users expect a CSV export
of an invoice to look (easy to paste into Excel, easy to re-import).
"""

from __future__ import annotations

import io

import pandas as pd

from models.invoice_schema import ExtractionResult
from utils.warning_labels import category_label, humanize_warning_field


def _header_dataframe(result: ExtractionResult) -> pd.DataFrame:
    inv = result.invoice
    flat = {
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date,
        "due_date": inv.due_date,
        "purchase_order_number": inv.purchase_order_number,
        "currency": inv.currency,
        "vendor_name": inv.vendor.name,
        "vendor_address": inv.vendor.address,
        "vendor_gst": inv.vendor.gst,
        "customer_name": inv.customer.name,
        "customer_address": inv.customer.address,
        "customer_gst": inv.customer.gst,
        "subtotal": inv.financials.subtotal,
        "discount": inv.financials.discount,
        "shipping": inv.financials.shipping,
        "cgst": inv.financials.cgst,
        "sgst": inv.financials.sgst,
        "igst": inv.financials.igst,
        "tax": inv.financials.tax,
        "round_off": inv.financials.round_off,
        "total_amount": inv.financials.total_amount,
        "damage_remarks": "; ".join(inv.damage_remarks) if inv.damage_remarks else None,
        "needs_review": result.needs_review,
        "warning_count": len(result.warnings),
    }
    return pd.DataFrame([flat])


def _line_items_dataframe(result: ExtractionResult) -> pd.DataFrame:
    rows = [item.model_dump() for item in result.invoice.line_items]
    return pd.DataFrame(rows)


def _warnings_dataframe(result: ExtractionResult) -> pd.DataFrame:
    rows = [
        {
            "category": category_label(w.category),
            "severity": w.severity,
            "field": humanize_warning_field(w.field, result.invoice),
            "message": w.message,
        }
        for w in result.warnings
    ]
    return pd.DataFrame(rows)


def to_csv_bytes(result: ExtractionResult) -> bytes:
    buffer = io.StringIO()
    buffer.write("INVOICE SUMMARY\n")
    _header_dataframe(result).to_csv(buffer, index=False)
    buffer.write("\nLINE ITEMS\n")
    _line_items_dataframe(result).to_csv(buffer, index=False)
    buffer.write("\nREVIEW NOTES\n")
    _warnings_dataframe(result).to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8")