"""
Canonical invoice schema.

This is the contract between GPT-4.1's structured output and the rest of
the application. Every field is Optional because we NEVER want the model
to fabricate a value it did not actually see on the invoice — missing
data must come through as `None`, not a guess.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ConfigDict


class InvoiceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, description="Line item description / name")
    product_code: str | None = Field(default=None, description="SKU / product / item code")
    hsn_sac: str | None = Field(default=None, description="HSN or SAC code, if present")
    quantity: float | None = None
    unit: str | None = Field(default=None, description="Unit of measure, e.g. EA, KG, HRS")
    unit_price: float | None = None
    discount: float | None = None
    tax_rate: float | None = Field(default=None, description="Tax rate as a percentage, e.g. 18.0 for 18%")
    tax_amount: float | None = None
    amount: float | None = Field(default=None, description="Line total (usually qty * unit_price - discount)")
    damage_note: str | None = Field(
        default=None,
        description=(
            "Verbatim remark found on the invoice indicating THIS specific "
            "line item's goods were damaged/received damaged (e.g. a note "
            "printed next to or under that product row). Null if no such "
            "remark is tied to this item."
        ),
    )


class PartyInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    gst: str | None = Field(default=None, description="GSTIN if present")
    pan: str | None = None
    tax_id: str | None = Field(default=None, description="Any other tax identifier (VAT, EIN, etc.)")


class FinancialInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subtotal: float | None = None
    discount: float | None = None
    shipping: float | None = None
    other_charges: float | None = None
    cgst: float | None = None
    sgst: float | None = None
    igst: float | None = None
    tax: float | None = Field(default=None, description="Total tax amount (sum of all tax components)")
    round_off: float | None = None
    total_amount: float | None = None
    amount_paid: float | None = None
    balance_due: float | None = None


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: str | None = None
    invoice_date: str | None = Field(default=None, description="Date exactly as printed/written on the invoice, unmodified")
    due_date: str | None = Field(default=None, description="Date exactly as printed/written on the invoice, unmodified")
    purchase_order_number: str | None = None
    reference_number: str | None = None
    invoice_type: str | None = Field(default=None, description="e.g. Tax Invoice, Credit Note, Proforma")
    currency: str | None = Field(default=None, description="ISO currency code, e.g. INR, USD")

    vendor: PartyInfo = Field(default_factory=PartyInfo)
    customer: PartyInfo = Field(default_factory=PartyInfo)

    financials: FinancialInfo = Field(default_factory=FinancialInfo)

    line_items: list[InvoiceItem] = Field(default_factory=list)

    damage_remarks: list[str] = Field(
        default_factory=list,
        description=(
            "Verbatim damage-related remarks found anywhere on the invoice "
            "that are NOT tied to one specific line item (e.g. a general "
            "note like 'goods received damaged' printed elsewhere on the "
            "document). Line-item-specific remarks belong on that item's "
            "damage_note instead, not here."
        ),
    )


class FieldWarning(BaseModel):
    """A single validation/normalization warning attached to a field."""

    field: str
    message: str
    severity: str = Field(default="warning", description="'warning' or 'error'")
    category: str = Field(
        default="data_mismatch",
        description=(
            "'data_mismatch' (arithmetic/consistency checks e.g. qty x "
            "price != amount, GST format), 'missing_field' (a required "
            "field wasn't extracted), or 'damage_flag' (a damage-related "
            "remark found on the document). Used to group warnings for "
            "display/export."
        ),
    )


class ExtractionResult(BaseModel):
    """Wraps the extracted Invoice with metadata about how it was processed."""

    invoice: Invoice
    source_file: str
    extraction_path: str = Field(description="'digital_pdf' | 'vision' | 'text_plus_vision'")
    warnings: list[FieldWarning] = Field(default_factory=list)
    needs_review: bool = False
    selected_fields: list[str] = Field(
        default_factory=list,
        description="List of field names that were selected for extraction"
    )
    token_usage: dict | None = Field(
        default=None,
        description="Token usage for this document's extraction call: "
                     "{prompt_tokens, completion_tokens, total_tokens, cached_tokens, estimated_cost}",
    )