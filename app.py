"""
Streamlit UI for the Invoice Particulars Extractor - Enhanced Version.

Flow:
  1. Upload (single or multiple invoices)
  2. Select fields to extract (collapsible categories, persistent selection)
  3. Extract all selected documents (live progress, isolated UI)
  4. Review & Edit results
  5. Export (single file or ZIP for batch)

UI-only: extraction/validation logic lives in extract.py / processing / models.
"""

from __future__ import annotations

import html
import io
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from exporters.excel_exporter import build_workbook
from exporters.json_exporter import to_json_bytes
from extract import extract_invoice
from extraction.pdf_renderer import render_page_to_png_bytes
from models.invoice_schema import ExtractionResult, FieldWarning, Invoice, InvoiceItem
from processing.validator import validate_invoice
from usage.tracker import get_session_summary, init_db, new_session_id
from utils.field_selector import (
    FIELD_CATEGORIES,
    filter_result_by_fields,
    get_field_label,
)
from utils.warning_labels import CATEGORY_ORDER, category_label, humanize_warning_field

st.set_page_config(page_title="Invoice Extractor", page_icon=":material/receipt_long:", layout="wide")


@st.cache_resource
def _init_db_once() -> None:
    """
    Probe Supabase once per app process, not once per rerun.

    Streamlit re-executes this whole script top-to-bottom on every widget
    interaction (checkbox, click, etc). A bare `init_db()` call here would
    hit Supabase on every single one of those reruns — st.cache_resource
    makes it run exactly once and reuse the cached (None) result after
    that, until the app process restarts.
    """
    init_db()


_init_db_once()
if "session_id" not in st.session_state:
    # One id per browser session (survives reruns, reset on new tab).
    # Every token-usage row logged during this session is tagged with it,
    # so we can total up "how much did this visit cost" at any time.
    st.session_state.session_id = new_session_id()

# ============================================================================
# FIELD TOOLTIPS
# ============================================================================

FIELD_TOOLTIPS = {
    "invoice_number": "Unique identifier printed on the invoice, e.g. INV-2024-0042",
    "invoice_date": "Date the invoice was issued (YYYY-MM-DD when possible)",
    "due_date": "Payment due date printed on the invoice",
    "invoice_type": "Category such as Tax Invoice, Proforma, Credit Note",
    "currency": "3-letter ISO code (USD, EUR, INR, …)",
    "purchase_order_number": "Buyer's PO number referenced on the invoice",
    "reference_number": "Any additional reference (contract, project, etc.)",
    "vendor.name": "Name of the seller / issuer",
    "vendor.address": "Full billing address of the seller",
    "vendor.gst": "Seller's tax ID (GST / VAT / TIN)",
    "vendor.email": "Contact email of the seller",
    "vendor.phone": "Contact phone of the seller",
    "customer.name": "Name of the buyer / recipient",
    "customer.address": "Full billing address of the buyer",
    "customer.gst": "Buyer's tax ID",
    "customer.email": "Buyer's email if printed on the invoice",
    "financials.subtotal": "Sum of line items before tax and adjustments",
    "financials.tax": "Total tax amount (CGST + SGST + IGST or VAT)",
    "financials.total_amount": "Grand total payable",
    "financials.discount": "Any discount applied to the invoice",
    "financials.shipping": "Shipping / freight charges",
    "financials.cgst": "Central GST (India)",
    "financials.sgst": "State GST (India)",
    "financials.igst": "Integrated GST (India)",
    "financials.round_off": "Rounding adjustment applied to the total",
    "line_items": "Table of products / services billed with qty and price",
}


def tooltip_for(field_key: str) -> str | None:
    return FIELD_TOOLTIPS.get(field_key)


def mi(icon_name: str, size: str | None = None, color: str | None = None) -> str:
    """
    One Google Material Symbol (rounded style), as an inline HTML span.

    Use this everywhere an emoji used to sit inside a raw `unsafe_allow_html`
    string — Streamlit's native `:material/name:` markdown shortcode isn't
    reliably substituted inside large raw-HTML blocks, so those spots need
    the actual webfont ligature instead (see the `.mi` CSS class + Material
    Symbols @import above). For real widget params (st.button(icon=...),
    st.popover(icon=...), st.set_page_config(page_icon=...), or plain
    st.markdown() text with no HTML), use the native ":material/name:"
    shortcode directly — it's simpler and just as reliable there.

    `icon_name` is the Material Symbols name in snake_case, e.g.
    "check_circle", "description", "archive" — browse the full set at
    https://fonts.google.com/icons
    """
    style = ""
    if size:
        style += f"font-size:{size};"
    if color:
        style += f"color:{color};"
    style_attr = f' style="{style}"' if style else ""
    return f'<span class="mi"{style_attr}>{icon_name}</span>'


# ============================================================================
# STYLES
# ============================================================================

_CSS = (
    "<style>"
    "@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap');"
    "@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200');"

    ".mi {"
    "  font-family: 'Material Symbols Rounded';"
    "  font-weight: normal; font-style: normal;"
    "  font-size: 1.1em; line-height: 1; letter-spacing: normal;"
    "  text-transform: none; display: inline-block; white-space: nowrap;"
    "  word-wrap: normal; direction: ltr; vertical-align: -0.18em;"
    "  -webkit-font-feature-settings: 'liga'; font-feature-settings: 'liga';"
    "  -webkit-font-smoothing: antialiased;"
    "}"

    "html, body, [class*='css'] {"
    "  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;"
    "  color: #0F172A;"
    "  -webkit-font-smoothing: antialiased;"
    "}"
    ".block-container { padding-top: 1.6rem; padding-bottom: 4rem; max-width: 1280px; }"
    "#MainMenu, footer, header { visibility: hidden; }"
    "div[data-testid='stToolbar'] { display: none; }"

    "div[data-testid='stVerticalBlockBorderWrapper'] > div > div[data-testid='stVerticalBlock'] { gap: 0.6rem; }"

    ".hero { text-align: center; padding: 1rem 0 1.6rem 0; position: relative; }"
    ".hero::before {"
    "  content: ''; position: absolute; inset: -60px -20% auto -20%; height: 340px;"
    "  background: radial-gradient(600px 200px at 30% 0%, rgba(139,92,246,0.14), transparent 60%),"
    "              radial-gradient(500px 200px at 75% 0%, rgba(99,102,241,0.12), transparent 60%);"
    "  z-index: -1; pointer-events: none;"
    "}"
    ".hero-badge {"
    "  display: inline-flex; align-items: center; gap: 0.45rem;"
    "  padding: 0.35rem 0.85rem; background: rgba(139,92,246,0.08);"
    "  border: 1px solid rgba(139,92,246,0.18); color: #6D28D9;"
    "  font-size: 0.78rem; font-weight: 600; border-radius: 999px;"
    "  margin-bottom: 1rem; letter-spacing: 0.01em;"
    "}"
    ".hero-badge .dot { width: 6px; height: 6px; border-radius: 50%; background: #8B5CF6;"
    "  box-shadow: 0 0 0 3px rgba(139,92,246,0.2); }"
    ".hero h1 {"
    "  font-size: 2.4rem; font-weight: 800; margin: 0 0 0.6rem 0;"
    "  letter-spacing: -0.035em; line-height: 1.1;"
    "  background: linear-gradient(135deg, #18181B 0%, #3F3F46 60%, #6D28D9 100%);"
    "  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;"
    "}"
    ".hero p { color: #64748B; font-size: 1.02rem; max-width: 560px; margin: 0 auto; line-height: 1.6; }"

    ".steps {"
    "  display: flex; align-items: center; justify-content: center;"
    "  gap: 0.25rem; margin: 0 auto 1.8rem auto; max-width: 720px;"
    "}"
    ".step-item {"
    "  display: flex; align-items: center; gap: 0.5rem;"
    "  padding: 0.45rem 0.9rem; border-radius: 999px;"
    "  font-size: 0.82rem; font-weight: 600; color: #94A3B8;"
    "  transition: all 0.2s ease; white-space: nowrap;"
    "}"
    ".step-item .num {"
    "  display: inline-flex; align-items: center; justify-content: center;"
    "  width: 22px; height: 22px; border-radius: 50%;"
    "  background: #F1F1F4; color: #71717A;"
    "  font-size: 0.72rem; font-weight: 700;"
    "}"
    ".step-item.active { color: #6D28D9; background: #F5F3FF; }"
    ".step-item.active .num { background: #8B5CF6; color: white;"
    "  box-shadow: 0 0 0 3px rgba(139,92,246,0.2); }"
    ".step-item.done { color: #10B981; }"
    ".step-item.done .num { background: #D1FAE5; color: #047857; }"
    ".step-connector { flex: 1; height: 2px; background: #F1F1F4; max-width: 40px; }"
    ".step-connector.done { background: #A7F3D0; }"

    "div[data-testid='stFileUploader'] { margin-bottom: 1rem; }"
    "div[data-testid='stFileUploaderDropzone'] {"
    "  background: linear-gradient(180deg, #FCFCFD 0%, #F8F7FC 100%);"
    "  border: 1.5px dashed #D4D4D8; border-radius: 18px;"
    "  padding: 2.6rem 1.5rem; transition: all 0.22s cubic-bezier(0.4, 0, 0.2, 1);"
    "  min-height: 200px; display: flex; flex-direction: column;"
    "  justify-content: center; align-items: center; position: relative; overflow: hidden;"
    "}"
    "div[data-testid='stFileUploaderDropzone']:hover {"
    "  border-color: #A78BFA;"
    "  background: linear-gradient(180deg, #FAF8FF 0%, #F5F0FF 100%);"
    "  transform: translateY(-2px);"
    "  box-shadow: 0 12px 32px -12px rgba(139, 92, 246, 0.25);"
    "}"
    "div[data-testid='stFileUploaderDropzone'] svg {"
    "  color: #8B5CF6; width: 42px; height: 42px;"
    "  filter: drop-shadow(0 6px 14px rgba(139,92,246,0.35));"
    "}"
    "div[data-testid='stFileUploaderDropzoneInstructions'] > div > span {"
    "  color: #18181B; font-weight: 600; font-size: 1.05rem; margin-top: 0.6rem;"
    "}"
    "div[data-testid='stFileUploaderDropzoneInstructions'] > div > small { display: none; }"
    "div[data-testid='stFileUploaderDropzone'] button {"
    "  background: white; border: 1px solid #E4E4E7; color: #18181B;"
    "  font-weight: 600; border-radius: 10px; padding: 0.5rem 1.15rem;"
    "  margin-top: 0.9rem;"
    "  box-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 4px 12px -4px rgba(139,92,246,0.12);"
    "  transition: all 0.15s ease;"
    "}"
    "div[data-testid='stFileUploaderDropzone'] button:hover {"
    "  border-color: #A78BFA; color: #6D28D9; background: #FAF8FF;"
    "}"
    "div[data-testid='stFileUploaderFile'] {"
    "  background: white; border: 1px solid #EDEDF0; border-radius: 12px;"
    "  padding: 0.65rem 0.95rem; box-shadow: 0 1px 3px rgba(0,0,0,0.03);"
    "}"

    "div[data-testid='stButton'] button[kind='primary'] {"
    "  background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%);"
    "  color: white; border: none; border-radius: 12px;"
    "  padding: 0.85rem 1.5rem; font-weight: 700; font-size: 1rem;"
    "  letter-spacing: -0.01em;"
    "  box-shadow: 0 6px 18px -4px rgba(139, 92, 246, 0.45),"
    "              inset 0 1px 0 rgba(255,255,255,0.15);"
    "  transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);"
    "  height: auto;"
    "}"
    "div[data-testid='stButton'] button[kind='primary']:hover:not(:disabled) {"
    "  transform: translateY(-2px);"
    "  box-shadow: 0 10px 26px -6px rgba(139, 92, 246, 0.55),"
    "              inset 0 1px 0 rgba(255,255,255,0.2);"
    "}"
    "div[data-testid='stButton'] button[kind='primary']:disabled {"
    "  background: #F1F1F3; color: #A1A1AA; box-shadow: none;"
    "}"
    "div[data-testid='stButton'] button[kind='secondary'] {"
    "  border-radius: 10px; font-weight: 600;"
    "  border: 1px solid #E4E4E7; background: white; color: #3F3F46;"
    "  padding: 0.55rem 1rem; transition: all 0.15s ease;"
    "  box-shadow: 0 1px 2px rgba(0,0,0,0.02);"
    "}"
    "div[data-testid='stButton'] button[kind='secondary']:hover {"
    "  border-color: #A78BFA; color: #6D28D9; background: #FAF8FF;"
    "  box-shadow: 0 4px 12px -2px rgba(139,92,246,0.2);"
    "}"

    "div[data-testid='stPopover'] > button {"
    "  border-radius: 10px !important; font-weight: 600 !important;"
    "  border: 1px solid #E4E4E7 !important; background: white !important;"
    "  color: #18181B !important; padding: 0.55rem 1rem !important;"
    "  box-shadow: 0 1px 2px rgba(0,0,0,0.03) !important;"
    "}"
    "div[data-testid='stPopover'] > button:hover {"
    "  border-color: #A78BFA !important; color: #6D28D9 !important;"
    "  background: #FAF8FF !important;"
    "}"
    "div[data-testid='stPopoverBody'] {"
    "  border-radius: 14px !important; border: 1px solid #EDEDF0 !important;"
    "  box-shadow: 0 12px 40px -8px rgba(0,0,0,0.15) !important;"
    "  padding: 0.5rem !important; min-width: 220px;"
    "}"
    "div[data-testid='stPopoverBody'] div[data-testid='stDownloadButton'] button {"
    "  border-radius: 10px !important; font-weight: 600 !important;"
    "  font-size: 0.9rem !important; padding: 0.65rem 1rem !important;"
    "  border: none !important; background: transparent !important;"
    "  color: #18181B !important; box-shadow: none !important;"
    "  text-align: left !important; justify-content: flex-start !important;"
    "  width: 100% !important;"
    "}"
    "div[data-testid='stPopoverBody'] div[data-testid='stDownloadButton'] button:hover {"
    "  background: #F7F5FF !important; color: #6D28D9 !important;"
    "}"

    "div[data-testid='stVerticalBlockBorderWrapper'] {"
    "  background: white; border: 1px solid #EDEDF0 !important;"
    "  border-radius: 18px !important; padding: 1.4rem 1.5rem !important;"
    "  box-shadow: 0 1px 3px rgba(0,0,0,0.03),"
    "              0 8px 24px -12px rgba(15, 23, 42, 0.06);"
    "  margin-bottom: 1rem;"
    "}"

    ".section-title {"
    "  font-weight: 700; font-size: 0.72rem; letter-spacing: 0.1em;"
    "  text-transform: uppercase; color: #64748B; margin: 0 0 1.1rem 0;"
    "  display: flex; align-items: center; gap: 0.55rem;"
    "}"
    ".section-title::before {"
    "  content: ''; width: 3px; height: 12px;"
    "  background: linear-gradient(180deg, #8B5CF6, #6366F1); border-radius: 2px;"
    "  flex-shrink: 0;"
    "}"

    ".field-cat-count {"
    "  font-size: 0.72rem; font-weight: 700; color: #94A3B8;"
    "  background: #F1F1F4; padding: 3px 10px; border-radius: 999px;"
    "  letter-spacing: 0.02em;"
    "}"
    ".field-cat-count.active {"
    "  background: #EDE9FE; color: #6D28D9;"
    "}"

    "div[data-testid='stCheckbox'] { margin-bottom: -0.4rem; }"
    "div[data-testid='stCheckbox'] label p {"
    "  font-size: 0.9rem !important; font-weight: 500 !important;"
    "  color: #3F3F46 !important;"
    "}"

    "div[data-testid='stVerticalBlockBorderWrapper'] "
    "div[data-testid='stHorizontalBlock'] "
    "div[data-testid='column']:first-child "
    "div[data-testid='stButton'] button {"
    "  width: 36px !important; height: 36px !important;"
    "  min-width: 36px !important; max-width: 36px !important;"
    "  padding: 0 !important; border-radius: 10px !important;"
    "  font-size: 1rem !important; line-height: 1 !important;"
    "  display: inline-flex !important; align-items: center !important;"
    "  justify-content: center !important;"
    "  background: #FFFFFF !important; border: 1px solid #E4E4E7 !important;"
    "  color: #52525B !important; box-shadow: 0 1px 2px rgba(0,0,0,0.02) !important;"
    "}"
    "div[data-testid='stVerticalBlockBorderWrapper'] "
    "div[data-testid='stHorizontalBlock'] "
    "div[data-testid='column']:first-child "
    "div[data-testid='stButton'] button p {"
    "  font-size: 1rem !important; color: #52525B !important; margin: 0 !important;"
    "}"
    "div[data-testid='stVerticalBlockBorderWrapper'] "
    "div[data-testid='stHorizontalBlock'] "
    "div[data-testid='column']:first-child "
    "div[data-testid='stButton'] button:hover {"
    "  border-color: #A78BFA !important; color: #6D28D9 !important;"
    "  background: #FAF8FF !important;"
    "}"
    "div[data-testid='stVerticalBlockBorderWrapper'] "
    "div[data-testid='stHorizontalBlock'] "
    "div[data-testid='column']:last-child "
    "div[data-testid='stButton'] button {"
    "  padding: 0.45rem 0.9rem !important;"
    "  font-size: 0.82rem !important;"
    "  min-height: 34px !important; height: 34px !important;"
    "}"
    "div[data-testid='stVerticalBlockBorderWrapper'] "
    "div[data-testid='stHorizontalBlock'] {"
    "  align-items: center;"
    "}"

    ".metric-grid { display: grid; grid-template-columns: repeat(3, 1fr);"
    "  gap: 0.75rem; margin-bottom: 1.2rem; }"
    ".metric-card {"
    "  background: linear-gradient(180deg, #FCFCFE 0%, #F8F8FC 100%);"
    "  border: 1px solid #EDEDF0; border-radius: 14px;"
    "  padding: 1rem 1.1rem; transition: all 0.18s ease;"
    "}"
    ".metric-card:hover { border-color: #D8D8E4;"
    "  box-shadow: 0 6px 20px -8px rgba(15,23,42,0.1); }"
    ".metric-card .label { font-size: 0.68rem; font-weight: 700;"
    "  letter-spacing: 0.09em; text-transform: uppercase;"
    "  color: #94A3B8; margin-bottom: 0.35rem; }"
    ".metric-card .value { font-family: 'JetBrains Mono', ui-monospace, monospace;"
    "  font-size: 1.45rem; font-weight: 600; letter-spacing: -0.03em;"
    "  color: #0F172A; line-height: 1.1; }"
    ".metric-card.total {"
    "  background: linear-gradient(135deg, #F5F3FF 0%, #EEF2FF 100%);"
    "  border-color: #DDD6FE;"
    "}"
    ".metric-card.total .label { color: #7C3AED; }"
    ".metric-card.total .value { color: #4C1D95; }"

    ".summary-metrics {"
    "  display: grid; grid-template-columns: repeat(4, 1fr);"
    "  gap: 0.75rem; margin-bottom: 1.4rem;"
    "}"
    ".summary-metric {"
    "  background: white; border: 1px solid #EDEDF0; border-radius: 14px;"
    "  padding: 1rem 1.1rem;"
    "}"
    ".summary-metric .label {"
    "  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.09em;"
    "  text-transform: uppercase; color: #94A3B8; margin-bottom: 0.35rem;"
    "}"
    ".summary-metric .value {"
    "  font-family: 'JetBrains Mono', ui-monospace, monospace;"
    "  font-size: 1.6rem; font-weight: 700; letter-spacing: -0.03em;"
    "  color: #0F172A; line-height: 1;"
    "}"
    ".summary-metric.ok .value { color: #059669; }"
    ".summary-metric.warn .value { color: #D97706; }"
    ".summary-metric.err .value { color: #DC2626; }"

    ".kv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem 1.4rem; }"
    ".kv-item { min-width: 0; }"
    ".kv-label { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.09em;"
    "  text-transform: uppercase; color: #94A3B8; margin-bottom: 0.3rem; }"
    ".kv-value { font-size: 0.94rem; font-weight: 600; color: #0F172A;"
    "  word-break: break-word; line-height: 1.4; }"
    ".kv-value.muted { color: #CBD5E1; font-weight: 500; }"
    ".kv-value.mono { font-family: 'JetBrains Mono', ui-monospace, monospace;"
    "  font-weight: 500; letter-spacing: -0.01em; }"
    ".empty-state { color: #CBD5E1; font-style: italic; font-weight: 500; font-size: 0.9rem; }"

    ".doc-status-badge { display: inline-flex; align-items: center; gap: 0.4rem;"
    "  padding: 5px 12px; border-radius: 999px; font-size: 0.78rem; font-weight: 600;"
    "  white-space: nowrap; }"
    ".doc-status-ok { background: #DCFCE7; color: #166534; border: 1px solid #BBF7D0; }"
    ".doc-status-warn { background: #FEF9C3; color: #854D0E; border: 1px solid #FDE68A; }"
    ".doc-status-error { background: #FEE2E2; color: #991B1B; border: 1px solid #FECACA; }"

    ".extract-wrap { max-width: 720px; margin: 0 auto; padding: 2rem 0 1rem 0; }"
    ".extract-header { text-align: center; margin-bottom: 2rem; }"
    ".extract-header h2 {"
    "  font-size: 1.5rem; font-weight: 800; margin: 0 0 0.4rem 0;"
    "  letter-spacing: -0.03em; color: #18181B;"
    "}"
    ".extract-header p { color: #64748B; font-size: 0.95rem; margin: 0; }"
    ".spinner-ring {"
    "  display: inline-block; width: 48px; height: 48px;"
    "  border: 3px solid #EDE9FE; border-top-color: #8B5CF6;"
    "  border-radius: 50%; animation: spin 1s linear infinite;"
    "  margin-bottom: 1rem;"
    "}"
    "@keyframes spin { to { transform: rotate(360deg); } }"

    ".file-list {"
    "  background: white; border: 1px solid #EDEDF0;"
    "  border-radius: 16px; padding: 0.6rem;"
    "  box-shadow: 0 1px 3px rgba(0,0,0,0.03);"
    "}"
    ".file-item {"
    "  display: flex; align-items: center; gap: 0.85rem;"
    "  padding: 0.85rem 1rem; border-radius: 10px;"
    "  transition: all 0.2s ease; margin-bottom: 0.25rem;"
    "}"
    ".file-item:last-child { margin-bottom: 0; }"
    ".file-item .icon-box {"
    "  width: 32px; height: 32px; border-radius: 8px;"
    "  display: flex; align-items: center; justify-content: center;"
    "  flex-shrink: 0; font-size: 0.9rem;"
    "}"
    ".file-item.pending { background: #FAFAFC; }"
    ".file-item.pending .icon-box { background: #F1F1F4; color: #94A3B8; }"
    ".file-item.running { background: linear-gradient(90deg, #F5F3FF, #FAF8FF); }"
    ".file-item.running .icon-box { background: #EDE9FE; color: #6D28D9; }"
    ".file-item.done { background: #F0FDF4; }"
    ".file-item.done .icon-box { background: #D1FAE5; color: #047857; }"
    ".file-item.error { background: #FEF2F2; }"
    ".file-item.error .icon-box { background: #FEE2E2; color: #991B1B; }"
    ".file-item .name {"
    "  flex: 1; font-size: 0.9rem; font-weight: 600; color: #18181B;"
    "  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
    "}"
    ".file-item .name-muted { color: #94A3B8; font-weight: 500; }"
    ".file-item .status {"
    "  font-size: 0.78rem; font-weight: 600; color: #64748B;"
    "  white-space: nowrap; flex-shrink: 0;"
    "}"
    ".file-item.running .status { color: #6D28D9; }"
    ".file-item.done .status { color: #047857; }"
    ".file-item.error .status { color: #991B1B; }"

    ".pulse-dot {"
    "  display: inline-block; width: 8px; height: 8px; border-radius: 50%;"
    "  background: #8B5CF6;"
    "  box-shadow: 0 0 0 4px rgba(139,92,246,0.15);"
    "  animation: pulse 1.5s ease-in-out infinite;"
    "}"
    "@keyframes pulse {"
    "  0%, 100% { box-shadow: 0 0 0 4px rgba(139,92,246,0.15); }"
    "  50% { box-shadow: 0 0 0 8px rgba(139,92,246,0.05); }"
    "}"

    ".progress-track {"
    "  background: #F1F1F4; border-radius: 999px; height: 6px;"
    "  overflow: hidden; margin-top: 1.2rem;"
    "}"
    ".progress-fill {"
    "  height: 100%; border-radius: 999px;"
    "  background: linear-gradient(90deg, #8B5CF6, #6366F1);"
    "  transition: width 0.4s ease;"
    "}"
    ".progress-label {"
    "  display: flex; justify-content: space-between;"
    "  font-size: 0.82rem; font-weight: 600; color: #64748B;"
    "  margin-top: 0.6rem;"
    "}"

    ".li-table { width: 100%; border-collapse: separate; border-spacing: 0;"
    "  font-size: 0.9rem; border: 1px solid #EDEDF0; border-radius: 14px;"
    "  overflow: hidden; }"
    ".li-table thead th {"
    "  background: #FAFAFC; font-size: 0.68rem; font-weight: 700;"
    "  letter-spacing: 0.09em; text-transform: uppercase; color: #64748B;"
    "  text-align: left; padding: 0.75rem 1rem;"
    "  border-bottom: 1px solid #EDEDF0; white-space: nowrap;"
    "}"
    ".li-table thead th.num { text-align: right; }"
    ".li-table tbody td { padding: 0.75rem 1rem; border-bottom: 1px solid #F5F5F7;"
    "  color: #18181B; vertical-align: top; }"
    ".li-table tbody tr:last-child td { border-bottom: none; }"
    ".li-table tbody tr:hover td { background: #FCFCFE; }"
    ".li-table td.num, .li-table th.num {"
    "  text-align: right; font-family: 'JetBrains Mono', ui-monospace, monospace;"
    "  font-weight: 500; font-variant-numeric: tabular-nums; letter-spacing: -0.01em;"
    "}"
    ".li-table td.muted { color: #CBD5E1; }"
    ".li-table td.desc { font-weight: 600; max-width: 340px; }"

    "div[data-testid='stAlert'] { border-radius: 12px !important;"
    "  border: 1px solid #FDE68A !important; background: #FFFBEB !important; }"
    "div[data-testid='stExpander'] { border: 1px solid #EDEDF0 !important;"
    "  border-radius: 12px !important; background: #FAFAFC;"
    "  box-shadow: none !important; margin-top: 0.5rem; }"

    ".doc-list-dot {"
    "  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;"
    "}"
    ".doc-list-dot.ok { background: #10B981; }"
    ".doc-list-dot.warn { background: #F59E0B; }"
    ".doc-list-dot.err { background: #EF4444; }"

    "</style>"
)
st.markdown(_CSS, unsafe_allow_html=True)

# ============================================================================
# STATE
# ============================================================================

DEFAULTS = {
    "step": "upload",
    "uploaded_files": None,
    "selected_fields": [],
    "results": {},
    "previews": {},
    "active_doc": None,
    "uploader_key": str(uuid.uuid4()),
    "_open_categories": None,
    "_field_selection": {},
}

for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

if st.session_state._open_categories is None:
    st.session_state._open_categories = set(FIELD_CATEGORIES.keys())


# ============================================================================
# TOKEN USAGE (sidebar) — hidden from the frontend.
# Usage is still logged/persisted by usage/tracker.py; we just don't render
# it in the UI anymore. Uncomment render_usage_sidebar() below to bring it
# back for an internal/admin build.
# ============================================================================

def render_usage_sidebar():
    usage = get_session_summary(st.session_state.session_id)
    with st.sidebar:
        st.markdown("### :material/pin: Token usage — this session")
        if usage["calls"] == 0:
            st.caption("No model calls yet.")
        else:
            c1, c2 = st.columns(2)
            c1.metric("API calls", usage["calls"])
            c2.metric("Est. cost", f"${usage['estimated_cost']:.4f}")
            st.metric("Total tokens", f"{usage['total_tokens']:,}")
            st.caption(
                f"Prompt: {usage['prompt_tokens']:,} · "
                f"Completion: {usage['completion_tokens']:,}"
            )
        st.caption(f"Session ID: `{st.session_state.session_id}`")


# render_usage_sidebar()  # disabled: cost/token details hidden from frontend


def reset_session():
    st.session_state.step = "upload"
    st.session_state.uploaded_files = None
    st.session_state.selected_fields = []
    st.session_state.results = {}
    st.session_state.previews = {}
    st.session_state.active_doc = None
    st.session_state.uploader_key = str(uuid.uuid4())
    st.session_state._open_categories = set(FIELD_CATEGORIES.keys())
    st.session_state._field_selection = {}
    for key in list(st.session_state.keys()):
        if key.startswith("cb_") or key.startswith("chevron_") \
                or key.startswith("select_all_") or key.startswith("preset_"):
            del st.session_state[key]


# ============================================================================
# HELPERS
# ============================================================================

def render_step_indicator(current: str):
    steps = [
        ("upload", "1", "Upload"),
        ("select_fields", "2", "Fields"),
        ("extracting", "3", "Extract"),
        ("results", "4", "Review"),
    ]
    order = [s[0] for s in steps]
    cur_idx = order.index(current) if current in order else 0

    parts = ['<div class="steps">']
    for i, (key, num, label) in enumerate(steps):
        if i < cur_idx:
            cls, marker = "step-item done", "✓"
        elif i == cur_idx:
            cls, marker = "step-item active", num
        else:
            cls, marker = "step-item", num
        parts.append(f'<div class="{cls}"><span class="num">{marker}</span>{label}</div>')
        if i < len(steps) - 1:
            conn = "step-connector done" if i < cur_idx else "step-connector"
            parts.append(f'<div class="{conn}"></div>')
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def result_severity(result: ExtractionResult) -> str:
    if any(w.severity == "error" for w in result.warnings):
        return "err"
    if any(w.severity == "warning" for w in result.warnings):
        return "warn"
    return "ok"


def get_status_badge(result: ExtractionResult) -> str:
    sev = result_severity(result)
    error_count = sum(1 for w in result.warnings if w.severity == "error")
    warn_count = sum(1 for w in result.warnings if w.severity == "warning")
    if sev == "err":
        return f'<span class="doc-status-badge doc-status-error">✕ {error_count} error(s)</span>'
    if sev == "warn":
        return f'<span class="doc-status-badge doc-status-warn">⚠ {warn_count} warning(s)</span>'
    return '<span class="doc-status-badge doc-status-ok">✓ OK</span>'


def fmt_text(v, mono: bool = False) -> str:
    if v is None or v == "":
        return '<span class="empty-state">—</span>'
    cls = "kv-value mono" if mono else "kv-value"
    return f'<span class="{cls}">{html.escape(str(v))}</span>'


def export_ready_result(result: ExtractionResult) -> ExtractionResult:
    # NOTE: this used to also strip `.warnings` here, on the theory that
    # exports should be "clean data only." That directly conflicts with
    # the Review Notes sheet/section (Excel + CSV) and the warning-count
    # columns, which exist specifically to carry that detail into the
    # export — so warnings are now kept. Deep copy is kept so downstream
    # export code can't accidentally mutate the in-app result.
    return result.model_copy(deep=True)


def build_zip_export(results: dict) -> bytes:
    # One combined workbook (Summary + Invoices + Line Items across all
    # documents) plus one JSON per invoice, since JSON is naturally
    # per-document while Excel is where cross-invoice filtering happens.
    export_results = {name: export_ready_result(result) for name, result in results.items()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("invoices_export.xlsx", build_workbook(export_results))
        for file_name, export_result in export_results.items():
            base = Path(file_name).stem
            zf.writestr(f"{base}.json", to_json_bytes(export_result))
    buf.seek(0)
    return buf.getvalue()


def _cb_key(field_key: str) -> str:
    return f"cb_{field_key}"


def get_selection(field_key: str) -> bool:
    # Persistent store — survives a category being collapsed (which
    # destroys the checkbox widget and, with it, its own session_state
    # entry). This dict is the only thing that doesn't get wiped.
    return st.session_state._field_selection.get(field_key, False)


def set_selection(field_key: str, value: bool):
    st.session_state._field_selection[field_key] = value
    # If that field's checkbox happens to be mounted right now (its
    # category is open), also push the value into the widget's own
    # state directly — otherwise Streamlit would ignore our new value
    # next render, since an existing widget key always wins over the
    # `value=` argument passed to it.
    cb_key = _cb_key(field_key)
    if cb_key in st.session_state:
        st.session_state[cb_key] = value


def _on_field_checkbox_change(field_key: str):
    # Fires immediately when the user clicks a checkbox — before the
    # script reruns — so the persistent store is already correct by
    # the time anything (like a category's "x / y" count) reads it.
    st.session_state._field_selection[field_key] = st.session_state[_cb_key(field_key)]


def count_selected_in_category(cat_fields: dict) -> int:
    return sum(1 for key in cat_fields.keys() if get_selection(key))


# ============================================================================
# STEP 1: UPLOAD
# ============================================================================

if st.session_state.step == "upload":
    st.markdown(
        '<div class="hero">'
        '<div class="hero-badge"><span class="dot"></span> AI-powered extraction</div>'
        '<h1>Invoice Extractor</h1>'
        '<p>Upload invoices, select which fields to extract, and get clean structured data.</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    render_step_indicator("upload")

    left, mid, right = st.columns([1, 2.6, 1])
    with mid:
        uploaded_files = st.file_uploader(
            "Upload your invoices",
            type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            label_visibility="collapsed",
            key=st.session_state.uploader_key,
        )

        proceed_clicked = st.button(
            "Next: Select Fields  →",
            type="primary",
            width='stretch',
            disabled=not uploaded_files,
        )

        st.markdown(
            '<div style="text-align:center;color:#94A3B8;font-size:0.82rem;'
            'margin-top:1rem;letter-spacing:0.01em;">'
            'PDF · PNG · JPG &nbsp;·&nbsp; Upload multiple invoices &nbsp;·&nbsp; up to 200 MB each'
            '</div>',
            unsafe_allow_html=True,
        )

        if proceed_clicked and uploaded_files:
            st.session_state.uploaded_files = uploaded_files
            st.session_state.step = "select_fields"
            st.rerun()
            st.stop()  # FIX: halt execution so nothing below renders this pass

    st.stop()  # FIX: end of step 1 branch — nothing else runs

# ============================================================================
# STEP 2: SELECT FIELDS
# ============================================================================

elif st.session_state.step == "select_fields":
    render_step_indicator("select_fields")

    files = st.session_state.uploaded_files or []
    file_names = [f.name for f in files]

    st.markdown(
        f'<div style="text-align:center;margin-bottom:1.4rem;">'
        f'<h1 style="font-size:1.7rem;font-weight:800;margin:0 0 0.4rem 0;'
        f'letter-spacing:-0.03em;">Select fields to extract</h1>'
        f'<p style="color:#64748B;font-size:0.95rem;margin:0;">'
        f'{len(file_names)} invoice(s) ready &nbsp;·&nbsp; choose exactly what you want</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:0.4rem;'
        f'padding:5px 12px;border-radius:999px;background:#F5F3FF;'
        f'color:#6D28D9;font-size:0.78rem;font-weight:600;'
        f'border:1px solid #DDD6FE;margin:0 0.35rem 0.35rem 0;">📄 {html.escape(n)}</span>'
        for n in file_names
    )
    st.markdown(
        f'<div style="text-align:center;margin-bottom:1.6rem;">{chips}</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div style="font-size:0.72rem;font-weight:700;letter-spacing:0.08em;'
        'text-transform:uppercase;color:#94A3B8;margin-bottom:0.5rem;">Quick presets</div>',
        unsafe_allow_html=True,
    )

    p1, p2, p3, p4 = st.columns(4)
    with p1:
        if st.button("Select all", width='stretch', key="preset_all"):
            for cat_fields in FIELD_CATEGORIES.values():
                for key in cat_fields.keys():
                    set_selection(key, True)
            st.rerun()
    with p2:
        if st.button("Headers only", width='stretch', key="preset_headers"):
            for cat_fields in FIELD_CATEGORIES.values():
                for key in cat_fields.keys():
                    set_selection(key, False)
            for key in ["invoice_number", "invoice_date", "due_date", "currency",
                        "vendor.name", "customer.name"]:
                set_selection(key, True)
            st.rerun()
    with p3:
        if st.button("Financials only", width='stretch', key="preset_fin"):
            for cat_fields in FIELD_CATEGORIES.values():
                for key in cat_fields.keys():
                    set_selection(key, False)
            for key in ["financials.subtotal", "financials.tax", "financials.total_amount"]:
                set_selection(key, True)
            st.rerun()
    with p4:
        if st.button("Clear", width='stretch', key="preset_clear"):
            for cat_fields in FIELD_CATEGORIES.values():
                for key in cat_fields.keys():
                    set_selection(key, False)
            st.rerun()

    st.markdown("<div style='height:0.6rem;'></div>", unsafe_allow_html=True)

    for cat_idx, (category, fields) in enumerate(FIELD_CATEGORIES.items()):
        cat_count = count_selected_in_category(fields)
        all_on = cat_count == len(fields)
        is_open = category in st.session_state._open_categories

        with st.container(border=True):
            head_l, head_mid, head_r = st.columns(
                [0.22, 5, 1.1],
                vertical_alignment="center",
                gap="small",
            )

            with head_l:
                if st.button(
                    "",
                    key=f"chevron_{cat_idx}",
                    help="Expand / collapse",
                    icon=":material/expand_more:" if is_open else ":material/chevron_right:",
                ):
                    if is_open:
                        st.session_state._open_categories.discard(category)
                    else:
                        st.session_state._open_categories.add(category)
                    st.rerun()

            with head_mid:
                count_cls = "field-cat-count active" if cat_count else "field-cat-count"
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:0.6rem;">'
                    f'<span style="display:inline-flex;align-items:center;'
                    f'justify-content:center;width:26px;height:26px;'
                    f'border-radius:8px;background:linear-gradient(135deg,#F5F3FF,#EEF2FF);'
                    f'color:#6D28D9;font-size:0.8rem;">◆</span>'
                    f'<span style="font-size:0.94rem;font-weight:700;color:#18181B;">'
                    f'{html.escape(category)}</span>'
                    f'<span class="{count_cls}">{cat_count} / {len(fields)}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            with head_r:
                select_label = "Deselect all" if all_on else "Select all"
                if st.button(
                    select_label,
                    key=f"select_all_{cat_idx}",
                    type="secondary",
                    width='stretch',
                ):
                    for key in fields.keys():
                        set_selection(key, not all_on)
                    st.rerun()

            if is_open:
                st.markdown(
                    '<div style="height:1px;background:#F1F1F4;'
                    'margin:0.5rem 0 1rem 0;"></div>',
                    unsafe_allow_html=True,
                )
                cols = st.columns(2)
                for i, (field_key, field_label) in enumerate(fields.items()):
                    with cols[i % 2]:
                        cb_key = _cb_key(field_key)
                        # Prime session_state BEFORE creating the widget,
                        # rather than also passing `value=` to it — Streamlit
                        # doesn't allow both a `value=` default and a
                        # manually-managed session_state key on the same
                        # widget in one run (that's what triggered the
                        # "created with a default value but also had its
                        # value set via the Session State API" warning).
                        # `key=` alone is then the single source of truth,
                        # and set_selection()'s direct session_state writes
                        # (for Select all / Deselect all) keep working the
                        # same way.
                        if cb_key not in st.session_state:
                            st.session_state[cb_key] = get_selection(field_key)
                        st.checkbox(
                            field_label,
                            key=cb_key,
                            help=tooltip_for(field_key),
                            on_change=_on_field_checkbox_change,
                            args=(field_key,),
                        )

    selected_fields = [
        key for cat_fields in FIELD_CATEGORIES.values()
        for key in cat_fields.keys()
        if get_selection(key)
    ]

    st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)

    with st.container(border=True):
        info_col, btn_col = st.columns([3, 2])
        with info_col:
            count = len(selected_fields)
            if count:
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:0.7rem;">'
                    f'<span style="font-family:JetBrains Mono, monospace;'
                    f'font-size:1.4rem;font-weight:700;color:#6D28D9;">{count}</span>'
                    f'<span style="color:#64748B;font-size:0.9rem;font-weight:500;">'
                    f'field{"s" if count != 1 else ""} selected</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div style="color:#94A3B8;font-size:0.9rem;">'
                    'Select at least one field to continue.</div>',
                    unsafe_allow_html=True,
                )
        with btn_col:
            b1, b2 = st.columns([1, 1.3])
            with b1:
                if st.button("⬅ Back", width='stretch',
                             type="secondary", key="back_to_upload"):
                    st.session_state.step = "upload"
                    st.session_state.uploaded_files = None
                    st.rerun()
                    st.stop()  # FIX
            with b2:
                extract_clicked = st.button(
                    "Extract  →",
                    type="primary",
                    width='stretch',
                    disabled=not selected_fields,
                    key="go_extract",
                )
                if extract_clicked and selected_fields:
                    st.session_state.selected_fields = selected_fields
                    st.session_state.step = "extracting"
                    for key in list(st.session_state.keys()):
                        if key.startswith("cb_") or key.startswith("chevron_") \
                                or key.startswith("select_all_") or key.startswith("preset_"):
                            del st.session_state[key]
                    st.rerun()
                    st.stop()  # FIX: critical — stops field selector from rendering

    st.stop()  # FIX: end of step 2 branch

# ============================================================================
# STEP 3: EXTRACTING (live progress)
# ============================================================================

elif st.session_state.step == "extracting":
    render_step_indicator("extracting")

    files = st.session_state.uploaded_files or []
    total = len(files)
    fields_count = len(st.session_state.selected_fields)

    st.markdown(
        '<div class="extract-wrap">'
        '<div class="extract-header">'
        '<div class="spinner-ring"></div>'
        '<h2>Extracting invoices…</h2>'
        f'<p>{total} file{"s" if total != 1 else ""} '
        f'&nbsp;·&nbsp; {fields_count} field{"s" if fields_count != 1 else ""} each</p>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    list_placeholder = st.empty()
    progress_placeholder = st.empty()

    results = {}
    previews = {}
    statuses = {f.name: "pending" for f in files}

    def render_list():
        items = []
        for f in files:
            name = f.name
            status = statuses.get(name, "pending")

            if status == "pending":
                icon, icon_cls, status_text = "○", "pending", "Waiting"
            elif status == "running":
                icon = '<span class="pulse-dot"></span>'
                icon_cls, status_text = "running", "Extracting…"
            elif status == "done":
                icon, icon_cls, status_text = "✓", "done", "Done"
            else:
                icon, icon_cls, status_text = "✕", "error", "Failed"

            name_cls = "name" if status != "pending" else "name name-muted"

            items.append(
                f'<div class="file-item {icon_cls}">'
                f'<div class="icon-box">{icon}</div>'
                f'<div class="{name_cls}">{html.escape(name)}</div>'
                f'<div class="status">{status_text}</div>'
                f'</div>'
            )
        return '<div class="file-list">' + "".join(items) + '</div>'

    def render_progress(done_count: int, total_count: int):
        pct = int((done_count / total_count) * 100) if total_count else 0
        return (
            '<div class="progress-track">'
            f'<div class="progress-fill" style="width:{pct}%;"></div>'
            '</div>'
            f'<div class="progress-label"><span>Progress</span>'
            f'<span>{done_count} / {total_count}</span></div>'
        )

    list_placeholder.markdown(render_list(), unsafe_allow_html=True)
    progress_placeholder.markdown(render_progress(0, total), unsafe_allow_html=True)

    for idx, uploaded_file in enumerate(files):
        name = uploaded_file.name
        statuses[name] = "running"
        list_placeholder.markdown(render_list(), unsafe_allow_html=True)

        try:
            suffix = Path(name).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded_file.getvalue())
                tmp_path = Path(tmp.name)

            result = extract_invoice(tmp_path, session_id=st.session_state.session_id)
            result.warnings = validate_invoice(result.invoice)
            result.needs_review = bool(result.warnings)
            result = filter_result_by_fields(result, st.session_state.selected_fields)

            results[name] = result
            preview_bytes = (
                render_page_to_png_bytes(tmp_path, dpi=150)
                if suffix.lower() == ".pdf"
                else uploaded_file.getvalue()
            )
            previews[name] = preview_bytes
            statuses[name] = "done"
        except Exception as e:
            statuses[name] = "error"
            st.error(f"Failed to extract {name}: {str(e)}")

        list_placeholder.markdown(render_list(), unsafe_allow_html=True)
        progress_placeholder.markdown(
            render_progress(idx + 1, total), unsafe_allow_html=True
        )
        time.sleep(0.25)

    time.sleep(0.4)

    st.session_state.results = results
    st.session_state.previews = previews
    st.session_state.active_doc = next(iter(results.keys()), None)
    st.session_state.step = "results"
    st.rerun()
    st.stop()  # FIX: guarantees clean exit before results renders

# ============================================================================
# STEP 4: RESULTS
# ============================================================================

elif st.session_state.step == "results":
    render_step_indicator("results")

    results = st.session_state.results
    previews = st.session_state.previews

    total_docs = len(results)
    err_docs = sum(1 for r in results.values() if result_severity(r) == "err")
    warn_docs = sum(1 for r in results.values() if result_severity(r) == "warn")
    ok_docs = total_docs - err_docs - warn_docs

    head_l, head_r = st.columns([3, 1.2], vertical_alignment="center")
    with head_l:
        st.markdown(
            '<h1 style="font-size:1.7rem;font-weight:800;margin:0;'
            'letter-spacing:-0.03em;">Extraction Results</h1>',
            unsafe_allow_html=True,
        )
    with head_r:
        b1, b2 = st.columns([1, 1.2])
        with b1:
            if st.button("⬅ Start over", width='stretch',
                         type="secondary", key="start_over"):
                reset_session()
                st.rerun()
                st.stop()  # FIX
        with b2:
            with st.popover("⬇  Export", width='stretch'):
                if total_docs == 1:
                    file_name = next(iter(results.keys()))
                    result = results[file_name]
                    base_name = Path(file_name).stem
                    export_result = export_ready_result(result)
                    st.download_button(
                        "📄  Download JSON",
                        data=to_json_bytes(export_result),
                        file_name=f"{base_name}.json",
                        mime="application/json",
                        width='stretch',
                    )
                    st.download_button(
                        "📈  Download Excel",
                        data=build_workbook({file_name: export_result}),
                        file_name=f"{base_name}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        width='stretch',
                    )
                else:
                    st.markdown(
                        f'<div style="font-size:0.82rem;color:#64748B;'
                        f'padding:0.4rem 0.6rem;">'
                        f'<b style="color:#18181B;">{total_docs} invoices</b> '
                        f'&nbsp;·&nbsp; ZIP bundles one combined Excel workbook '
                        f'+ JSON per file'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    st.download_button(
                        "📦  Download all (.zip)",
                        data=build_zip_export(results),
                        file_name="invoices_export.zip",
                        mime="application/zip",
                        width='stretch',
                    )
                    st.markdown(
                        '<div style="height:1px;background:#F1F1F4;'
                        'margin:0.4rem 0.6rem;"></div>',
                        unsafe_allow_html=True,
                    )
                    export_results = {
                        name: export_ready_result(result) for name, result in results.items()
                    }
                    st.download_button(
                        "📈  Download combined Excel only",
                        data=build_workbook(export_results),
                        file_name="invoices_export.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        width='stretch',
                    )
                    st.markdown(
                        '<div style="height:1px;background:#F1F1F4;'
                        'margin:0.4rem 0.6rem;"></div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        '<div style="font-size:0.72rem;color:#94A3B8;'
                        'padding:0.2rem 0.6rem;font-weight:600;'
                        'letter-spacing:0.06em;text-transform:uppercase;">'
                        'Individual files (JSON)</div>',
                        unsafe_allow_html=True,
                    )
                    for file_name, export_result in export_results.items():
                        base_name = Path(file_name).stem
                        st.download_button(
                            f"📄  {base_name}.json",
                            data=to_json_bytes(export_result),
                            file_name=f"{base_name}.json",
                            mime="application/json",
                            width='stretch',
                            key=f"json_{file_name}",
                        )

    st.markdown(
        f'<div class="summary-metrics">'
        f'<div class="summary-metric">'
        f'<div class="label">Documents</div>'
        f'<div class="value">{total_docs}</div>'
        f'</div>'
        f'<div class="summary-metric ok">'
        f'<div class="label">✓ Invoices with No Issues</div>'
        f'<div class="value">{ok_docs}</div>'
        f'</div>'
        f'<div class="summary-metric warn">'
        f'<div class="label">⚠ Invoices with Warning</div>'
        f'<div class="value">{warn_docs}</div>'
        f'</div>'
        f'<div class="summary-metric err">'
        f'<div class="label">✕ Invoices with Errors</div>'
        f'<div class="value">{err_docs}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not results:
        st.markdown(
            '<div style="text-align:center;padding:3rem 0;color:#94A3B8;">'
            'No results to show.</div>',
            unsafe_allow_html=True,
        )
        st.stop()

    # ------------------------------------------------------------------
    # Per-invoice token usage / cost table — hidden from the frontend.
    # Usage numbers are still available on `result.token_usage` and are
    # still logged by usage/tracker.py; we just don't render this table.
    # ------------------------------------------------------------------

    list_col, detail_col = st.columns([1, 3], gap="large")

    with list_col:
        with st.container(border=True):
            st.markdown(
                '<div class="section-title">Documents</div>',
                unsafe_allow_html=True,
            )

            if st.session_state.active_doc not in results:
                st.session_state.active_doc = next(iter(results.keys()))

            for file_name in results.keys():
                result = results[file_name]
                sev = result_severity(result)

                if st.button(
                    file_name,
                    key=f"doc_select_{file_name}",
                    width='stretch',
                    type="secondary",
                ):
                    st.session_state.active_doc = file_name
                    st.rerun()

                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:0.4rem;'
                    f'margin:-0.55rem 0 0.6rem 0.2rem;font-size:0.72rem;'
                    f'color:#94A3B8;font-weight:600;">'
                    f'<span class="doc-list-dot {sev}"></span>'
                    f'{sev.upper()}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    with detail_col:
        active = st.session_state.active_doc
        if active and active in results:
            result = results[active]
            inv = result.invoice
            f = inv.financials

            st.markdown(
                f'<div style="display:flex;align-items:center;justify-content:space-between;'
                f'gap:1rem;margin-bottom:1rem;">'
                f'<div style="display:flex;align-items:center;gap:0.7rem;min-width:0;">'
                f'{get_status_badge(result)}'
                f'<span style="font-size:1rem;font-weight:700;color:#18181B;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
                f'{html.escape(active)}</span>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Token/cost caption intentionally not rendered in the frontend.
            # (result.token_usage is still populated and logged server-side.)

            p_col, s_col = st.columns([1, 1.25], gap="large")

            with p_col:
                with st.container(border=True):
                    st.markdown(
                        '<div class="section-title">Document</div>',
                        unsafe_allow_html=True,
                    )
                    if active in previews:
                        st.image(previews[active], width='stretch')
                    else:
                        st.markdown(
                            '<div style="padding:2rem 1rem;text-align:center;'
                            'background:#FAFAFC;border-radius:12px;'
                            'border:1px dashed #E4E4E7;color:#94A3B8;'
                            'font-size:0.88rem;">🖼️ No preview</div>',
                            unsafe_allow_html=True,
                        )

            with s_col:
                with st.container(border=True):
                    st.markdown(
                        '<div class="section-title">Summary</div>',
                        unsafe_allow_html=True,
                    )

                    sub_val = f"{f.subtotal:,.2f}" if f.subtotal is not None else "—"
                    tax_val = f"{f.tax:,.2f}" if f.tax is not None else "—"
                    tot_val = f"{f.total_amount:,.2f}" if f.total_amount is not None else "—"
                    st.markdown(
                        f'<div class="metric-grid">'
                        f'<div class="metric-card">'
                        f'<div class="label">Subtotal</div>'
                        f'<div class="value">{sub_val}</div>'
                        f'</div>'
                        f'<div class="metric-card">'
                        f'<div class="label">Tax</div>'
                        f'<div class="value">{tax_val}</div>'
                        f'</div>'
                        f'<div class="metric-card total">'
                        f'<div class="label">Total</div>'
                        f'<div class="value">{tot_val}</div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    field_map = [
                        ("invoice_number", "Invoice #", inv.invoice_number, True),
                        ("invoice_date", "Invoice Date", inv.invoice_date, True),
                        ("due_date", "Due Date", inv.due_date, True),
                        ("currency", "Currency", inv.currency, False),
                        ("vendor.name", "Vendor", inv.vendor.name, False),
                        ("customer.name", "Customer", inv.customer.name, False),
                        ("purchase_order_number", "PO Number", inv.purchase_order_number, True),
                        ("reference_number", "Reference", inv.reference_number, True),
                    ]

                    kv_html = '<div class="kv-grid">'
                    for key, label, value, mono in field_map:
                        if key in result.selected_fields:
                            kv_html += (
                                f'<div class="kv-item">'
                                f'<div class="kv-label">{html.escape(label)}</div>'
                                f'<div>{fmt_text(value, mono)}</div>'
                                f'</div>'
                            )
                    kv_html += "</div>"
                    st.markdown(kv_html, unsafe_allow_html=True)

            if "line_items" in result.selected_fields and inv.line_items:
                with st.container(border=True):
                    st.markdown(
                        f'<div style="display:flex;align-items:center;'
                        f'justify-content:space-between;margin-bottom:1rem;">'
                        f'<div class="section-title" style="margin:0;">Line Items</div>'
                        f'<div style="font-size:0.78rem;font-weight:600;color:#94A3B8;">'
                        f'{len(inv.line_items)} item{"s" if len(inv.line_items) != 1 else ""}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    rows_html = ""
                    for item in inv.line_items:
                        desc = html.escape(str(item.description)) if item.description else "—"
                        desc_cls = "desc" if item.description else "desc muted"
                        qty = item.quantity
                        price = item.unit_price
                        amount = (qty or 0) * (price or 0)
                        qty_html = f"{qty:,.2f}" if qty is not None else "—"
                        price_html = f"{price:,.2f}" if price is not None else "—"
                        amt_html = (
                            f"{amount:,.2f}"
                            if (qty is not None and price is not None)
                            else "—"
                        )
                        rows_html += (
                            f'<tr>'
                            f'<td class="{desc_cls}">{desc}</td>'
                            f'<td class="num">{qty_html}</td>'
                            f'<td class="num">{price_html}</td>'
                            f'<td class="num">{amt_html}</td>'
                            f'</tr>'
                        )

                    st.markdown(
                        '<table class="li-table">'
                        '<thead>'
                        '<tr>'
                        '<th>Description</th>'
                        '<th class="num">Qty</th>'
                        '<th class="num">Unit Price</th>'
                        '<th class="num">Amount</th>'
                        '</tr>'
                        '</thead>'
                        f'<tbody>{rows_html}</tbody>'
                        '</table>',
                        unsafe_allow_html=True,
                    )

            if result.warnings:
                with st.container(border=True):
                    st.markdown(
                        f'<div class="section-title">Review Notes '
                        f'({len(result.warnings)})</div>',
                        unsafe_allow_html=True,
                    )
                    grouped: dict[str, list] = {}
                    for w in result.warnings:
                        grouped.setdefault(w.category, []).append(w)

                    ordered_categories = [c for c in CATEGORY_ORDER if c in grouped]
                    ordered_categories += [c for c in grouped if c not in CATEGORY_ORDER]

                    for cat in ordered_categories:
                        cat_warnings = grouped[cat]
                        st.markdown(
                            f'<div style="font-size:0.75rem;font-weight:700;'
                            f'text-transform:uppercase;letter-spacing:0.03em;'
                            f'color:#6B7280;margin:0.6rem 0 0.35rem;">'
                            f'{html.escape(category_label(cat))} ({len(cat_warnings)})'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        for w in cat_warnings:
                            icon = "✕" if w.severity == "error" else "⚠"
                            color = "#991B1B" if w.severity == "error" else "#854D0E"
                            bg = "#FEF2F2" if w.severity == "error" else "#FFFBEB"
                            border = "#FECACA" if w.severity == "error" else "#FDE68A"
                            field_label = humanize_warning_field(w.field, result.invoice)
                            st.markdown(
                                f'<div style="padding:0.6rem 0.9rem;border-radius:10px;'
                                f'background:{bg};border:1px solid {border};'
                                f'color:{color};font-size:0.85rem;font-weight:600;'
                                f'margin-bottom:0.5rem;">'
                                f'{icon} <b>{html.escape(field_label)}</b> — {html.escape(w.message)}'
                                f'</div>',
                                unsafe_allow_html=True,
                            )

    st.stop()  # FIX: end of step 4 branch