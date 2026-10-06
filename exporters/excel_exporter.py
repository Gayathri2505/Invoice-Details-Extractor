"""
Excel export.

Produces one workbook that works for a single invoice or a whole batch,
built for an end user to actually read/filter rather than a raw data dump:

  - "Summary"      : counts (total / ok / warning / error) + one row per
                     uploaded file (file name, invoice #, vendor, status).
  - "Invoices"     : one row per invoice, header-level fields only,
                     including a Damage Remarks column (invoice-level
                     damage notes not tied to a specific line item).
  - "Line Items"   : one row per line item, with source_file / invoice_number
                     / vendor_name / invoice_date repeated on each row so the
                     sheet is filterable/sortable on its own; includes a
                     Damage Note column for remarks tied to that item.
  - "Review Notes" : one row per warning across all invoices (severity,
                     field, and the actual message text) — the Summary/
                     Invoices sheets only carry a count, so this is where
                     the underlying warning text (including damage flags)
                     is visible in the export.

Only fields the user actually selected are included as columns (unioned
across every document in the export). A column is blank when a field was
selected but the invoice has no value for it; the column doesn't exist at
all when it was never selected for that export. Status/warning-count/
damage columns and the Review Notes sheet are always included regardless
of field selection, same as Status already was.
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from models.invoice_schema import ExtractionResult
from utils.field_selector import get_field_label
from utils.warning_labels import category_label, humanize_warning_field

HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="6D28D9", end_color="6D28D9", fill_type="solid")
TITLE_FONT = Font(bold=True, size=14)
SUBTLE_FONT = Font(color="64748B")
STATUS_FILL = {
    "OK": PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
    "WARN": PatternFill(start_color="FEF9C3", end_color="FEF9C3", fill_type="solid"),
    "ERR": PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
}

CURRENCY_FMT = "#,##0.00"

# Invoice-level (non-nested) fields, in display order, mapped to their
# field-selector key and how to read them off an Invoice.
_INVOICE_FIELD_ORDER = [
    "invoice_number", "invoice_date", "due_date", "invoice_type", "currency",
    "purchase_order_number", "reference_number",
]
_VENDOR_FIELD_ORDER = ["name", "address", "email", "phone", "gst", "pan", "tax_id"]
_CUSTOMER_FIELD_ORDER = ["name", "address", "email", "phone", "gst", "pan", "tax_id"]
_FINANCIAL_FIELD_ORDER = [
    "subtotal", "discount", "shipping", "other_charges",
    "cgst", "sgst", "igst", "tax", "round_off",
    "total_amount", "amount_paid", "balance_due",
]
_FINANCIAL_CURRENCY_FIELDS = set(_FINANCIAL_FIELD_ORDER)

_LINE_ITEM_FIELD_ORDER = [
    "description", "product_code", "hsn_sac", "quantity", "unit",
    "unit_price", "discount", "tax_rate", "tax_amount", "amount", "damage_note",
]
_LINE_ITEM_CURRENCY_FIELDS = {"unit_price", "discount", "tax_amount", "amount"}
_LINE_ITEM_LABELS = {
    "description": "Description", "product_code": "Product Code", "hsn_sac": "HSN/SAC",
    "quantity": "Quantity", "unit": "Unit", "unit_price": "Unit Price",
    "discount": "Discount", "tax_rate": "Tax Rate (%)", "tax_amount": "Tax Amount",
    "amount": "Amount", "damage_note": "Damage Note",
}


def _severity(result: ExtractionResult) -> str:
    if any(w.severity == "error" for w in result.warnings):
        return "ERR"
    if any(w.severity == "warning" for w in result.warnings):
        return "WARN"
    return "OK"


def _union_selected_fields(results: dict[str, ExtractionResult]) -> set[str]:
    selected: set[str] = set()
    for result in results.values():
        selected.update(result.selected_fields or [])
    return selected


def _style_header_row(ws: Worksheet, row: int, num_cols: int) -> None:
    for col in range(1, num_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center")


def _autosize_columns(ws: Worksheet, num_cols: int, min_width: int = 10, max_width: int = 42) -> None:
    for col in range(1, num_cols + 1):
        letter = get_column_letter(col)
        longest = 0
        for cell in ws[letter]:
            value = cell.value
            if value is not None:
                longest = max(longest, len(str(value)))
        ws.column_dimensions[letter].width = max(min_width, min(longest + 2, max_width))


# --------------------------------------------------------------------------
# Summary sheet
# --------------------------------------------------------------------------

def _write_summary_sheet(ws: Worksheet, results: dict[str, ExtractionResult]) -> None:
    total = len(results)
    err_count = sum(1 for r in results.values() if _severity(r) == "ERR")
    warn_count = sum(1 for r in results.values() if _severity(r) == "WARN")
    ok_count = total - err_count - warn_count

    ws["A1"] = "Invoice Extraction Summary"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:D1")

    stats = [
        ("Invoices processed", total),
        ("OK", ok_count),
        ("Warnings", warn_count),
        ("Errors", err_count),
    ]
    for i, (label, value) in enumerate(stats):
        r = 3 + i
        ws.cell(row=r, column=1, value=label).font = SUBTLE_FONT
        ws.cell(row=r, column=2, value=value).font = Font(bold=True)

    table_start = 3 + len(stats) + 2
    headers = ["File Name", "Invoice Number", "Vendor", "Status", "Warnings"]
    for col, label in enumerate(headers, start=1):
        ws.cell(row=table_start, column=col, value=label)
    _style_header_row(ws, table_start, len(headers))

    row = table_start + 1
    for file_name, result in results.items():
        sev = _severity(result)
        ws.cell(row=row, column=1, value=file_name)
        ws.cell(row=row, column=2, value=result.invoice.invoice_number)
        ws.cell(row=row, column=3, value=result.invoice.vendor.name)
        status_cell = ws.cell(row=row, column=4, value=sev)
        status_cell.fill = STATUS_FILL[sev]
        status_cell.alignment = Alignment(horizontal="center")
        ws.cell(row=row, column=5, value=len(result.warnings))
        row += 1

    ws.freeze_panes = ws.cell(row=table_start + 1, column=1)
    ws.auto_filter.ref = f"A{table_start}:E{row - 1}" if row > table_start + 1 else f"A{table_start}:E{table_start}"
    _autosize_columns(ws, len(headers))


# --------------------------------------------------------------------------
# Invoices sheet
# --------------------------------------------------------------------------

def _invoices_columns(selected_fields: set[str]) -> list[tuple[str, str, bool]]:
    """Returns list of (column_key, header_label, is_currency)."""
    cols: list[tuple[str, str, bool]] = [("source_file", "File Name", False)]

    for key in _INVOICE_FIELD_ORDER:
        if key in selected_fields:
            cols.append((key, get_field_label(key), False))

    for key in _VENDOR_FIELD_ORDER:
        fk = f"vendor.{key}"
        if fk in selected_fields:
            cols.append((fk, get_field_label(fk), False))

    for key in _CUSTOMER_FIELD_ORDER:
        fk = f"customer.{key}"
        if fk in selected_fields:
            cols.append((fk, get_field_label(fk), False))

    for key in _FINANCIAL_FIELD_ORDER:
        fk = f"financials.{key}"
        if fk in selected_fields:
            cols.append((fk, get_field_label(fk), key in _FINANCIAL_CURRENCY_FIELDS))

    cols.append(("status", "Status", False))
    cols.append(("warning_count", "Warnings", False))
    cols.append(("damage_remarks", "Damage Remarks", False))
    return cols


def _get_invoice_field(result: ExtractionResult, key: str, file_name: str):
    if key == "source_file":
        return file_name
    if key == "status":
        return _severity(result)
    if key == "warning_count":
        return len(result.warnings)
    if key == "damage_remarks":
        return "; ".join(result.invoice.damage_remarks) if result.invoice.damage_remarks else None
    inv = result.invoice
    if key.startswith("vendor."):
        return getattr(inv.vendor, key.split(".", 1)[1])
    if key.startswith("customer."):
        return getattr(inv.customer, key.split(".", 1)[1])
    if key.startswith("financials."):
        return getattr(inv.financials, key.split(".", 1)[1])
    return getattr(inv, key)


def _write_invoices_sheet(ws: Worksheet, results: dict[str, ExtractionResult], selected_fields: set[str]) -> None:
    cols = _invoices_columns(selected_fields)
    for col_idx, (_, label, _) in enumerate(cols, start=1):
        ws.cell(row=1, column=col_idx, value=label)
    _style_header_row(ws, 1, len(cols))

    row = 2
    for file_name, result in results.items():
        for col_idx, (key, _, is_currency) in enumerate(cols, start=1):
            value = _get_invoice_field(result, key, file_name)
            cell = ws.cell(row=row, column=col_idx, value=value)
            if is_currency and value is not None:
                cell.number_format = CURRENCY_FMT
            if key == "status" and value:
                cell.fill = STATUS_FILL.get(value, PatternFill())
                cell.alignment = Alignment(horizontal="center")
        row += 1

    last_row = max(row - 1, 1)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{last_row}"
    _autosize_columns(ws, len(cols))


# --------------------------------------------------------------------------
# Line Items sheet
# --------------------------------------------------------------------------

def _write_line_items_sheet(ws: Worksheet, results: dict[str, ExtractionResult], selected_fields: set[str]) -> None:
    include_items = "line_items" in selected_fields
    context_cols = [
        ("source_file", "File Name"),
        ("invoice_number", "Invoice Number"),
        ("vendor_name", "Vendor"),
        ("invoice_date", "Invoice Date"),
    ]
    item_keys = _LINE_ITEM_FIELD_ORDER if include_items else []
    headers = [label for _, label in context_cols] + [_LINE_ITEM_LABELS[k] for k in item_keys]

    for col_idx, label in enumerate(headers, start=1):
        ws.cell(row=1, column=col_idx, value=label)
    _style_header_row(ws, 1, len(headers))

    row = 2
    if include_items:
        for file_name, result in results.items():
            inv = result.invoice
            context_values = [file_name, inv.invoice_number, inv.vendor.name, inv.invoice_date]
            if not inv.line_items:
                for col_idx, value in enumerate(context_values, start=1):
                    ws.cell(row=row, column=col_idx, value=value)
                row += 1
                continue
            for item in inv.line_items:
                for col_idx, value in enumerate(context_values, start=1):
                    ws.cell(row=row, column=col_idx, value=value)
                item_data = item.model_dump()
                for offset, key in enumerate(item_keys, start=1):
                    value = item_data.get(key)
                    cell = ws.cell(row=row, column=len(context_values) + offset, value=value)
                    if key in _LINE_ITEM_CURRENCY_FIELDS and value is not None:
                        cell.number_format = CURRENCY_FMT
                row += 1

    last_row = max(row - 1, 1)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last_row}"
    _autosize_columns(ws, len(headers))


# --------------------------------------------------------------------------
# Review Notes sheet
# --------------------------------------------------------------------------

def _write_review_notes_sheet(ws: Worksheet, results: dict[str, ExtractionResult]) -> None:
    """One row per warning (across all invoices), with the actual message
    text — not just a count. Mirrors the app's "Review Notes" card so the
    Excel export doesn't lose that detail."""
    headers = ["File Name", "Invoice Number", "Category", "Severity", "Field", "Message"]
    for col, label in enumerate(headers, start=1):
        ws.cell(row=1, column=col, value=label)
    _style_header_row(ws, 1, len(headers))

    row = 2
    for file_name, result in results.items():
        for w in result.warnings:
            ws.cell(row=row, column=1, value=file_name)
            ws.cell(row=row, column=2, value=result.invoice.invoice_number)
            ws.cell(row=row, column=3, value=category_label(w.category))
            sev_cell = ws.cell(row=row, column=4, value=w.severity.upper())
            sev_fill_key = "ERR" if w.severity == "error" else "WARN"
            sev_cell.fill = STATUS_FILL[sev_fill_key]
            sev_cell.alignment = Alignment(horizontal="center")
            ws.cell(row=row, column=5, value=humanize_warning_field(w.field, result.invoice))
            ws.cell(row=row, column=6, value=w.message)
            row += 1

    last_row = max(row - 1, 1)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last_row}"
    _autosize_columns(ws, len(headers))


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def build_workbook(results: dict[str, ExtractionResult]) -> bytes:
    """Build the full workbook for one or more extraction results.

    `results` maps the original uploaded file name -> its ExtractionResult
    (already filtered down to the user's selected fields).
    """
    selected_fields = _union_selected_fields(results)

    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "Summary"
    _write_summary_sheet(summary_ws, results)

    invoices_ws = wb.create_sheet("Invoices")
    _write_invoices_sheet(invoices_ws, results, selected_fields)

    line_items_ws = wb.create_sheet("Line Items")
    _write_line_items_sheet(line_items_ws, results, selected_fields)

    review_notes_ws = wb.create_sheet("Review Notes")
    _write_review_notes_sheet(review_notes_ws, results)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def to_excel_bytes(result: ExtractionResult, file_name: str | None = None) -> bytes:
    """Single-invoice convenience wrapper around build_workbook()."""
    name = file_name or result.source_file
    return build_workbook({name: result})