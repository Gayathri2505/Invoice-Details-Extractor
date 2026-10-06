"""
Field selector utilities for filtering extracted invoice data.

Provides:
- List of all available fields
- Field categories (metadata, financial, parties, line items)
- Methods to filter invoice data based on selected fields
"""

from __future__ import annotations

from models.invoice_schema import Invoice, InvoiceItem, ExtractionResult


# All available invoice fields organized by category
FIELD_CATEGORIES = {
    "Invoice Metadata": {
        "invoice_number": "Invoice Number",
        "invoice_date": "Invoice Date",
        "due_date": "Due Date",
        "invoice_type": "Invoice Type",
        "currency": "Currency",
        "purchase_order_number": "PO Number",
        "reference_number": "Reference Number",
    },
    "Vendor Information": {
        "vendor.name": "Vendor Name",
        "vendor.address": "Vendor Address",
        "vendor.email": "Vendor Email",
        "vendor.phone": "Vendor Phone",
        "vendor.gst": "Vendor GST",
        "vendor.pan": "Vendor PAN",
        "vendor.tax_id": "Vendor Tax ID",
    },
    "Customer Information": {
        "customer.name": "Customer Name",
        "customer.address": "Customer Address",
        "customer.email": "Customer Email",
        "customer.phone": "Customer Phone",
        "customer.gst": "Customer GST",
        "customer.pan": "Customer PAN",
        "customer.tax_id": "Customer Tax ID",
    },
    "Financial Summary": {
        "financials.subtotal": "Subtotal",
        "financials.discount": "Discount",
        "financials.shipping": "Shipping",
        "financials.other_charges": "Other Charges",
        "financials.cgst": "CGST",
        "financials.sgst": "SGST",
        "financials.igst": "IGST",
        "financials.tax": "Total Tax",
        "financials.round_off": "Round Off",
        "financials.total_amount": "Total Amount",
        "financials.amount_paid": "Amount Paid",
        "financials.balance_due": "Balance Due",
    },
    "Line Items": {
        "line_items": "Line Items (Description, Quantity, Unit Price, etc.)",
    },
}

# All fields flattened for easy access
ALL_FIELDS = {}
for category, fields in FIELD_CATEGORIES.items():
    ALL_FIELDS.update(fields)


def get_all_field_names() -> list[str]:
    """Get list of all available field names."""
    return list(ALL_FIELDS.keys())


def get_field_label(field_name: str) -> str:
    """Get human-readable label for a field name."""
    return ALL_FIELDS.get(field_name, field_name)


def filter_invoice_by_fields(invoice: Invoice, selected_fields: list[str]) -> Invoice:
    """
    Create a filtered copy of invoice containing only selected fields.
    
    Args:
        invoice: Original invoice object
        selected_fields: List of field names to keep
        
    Returns:
        Filtered invoice object
    """
    if not selected_fields or "line_items" in selected_fields:
        # If no fields selected or line_items included, keep all
        filtered = invoice.model_copy(deep=True)
    else:
        # Start with full copy
        data = invoice.model_dump()
        
        # Clear fields not in selected list
        for field in ["invoice_number", "invoice_date", "due_date", "invoice_type",
                      "currency", "purchase_order_number", "reference_number"]:
            if f"invoice_{field.split('_', 1)[-1]}" not in selected_fields and field not in selected_fields:
                data[field] = None
        
        # Handle nested vendor/customer fields
        if data.get("vendor"):
            vendor_data = data["vendor"]
            vendor_fields = {k: v for k, v in vendor_data.items()}
            for key in vendor_fields:
                if f"vendor.{key}" not in selected_fields:
                    vendor_data[key] = None
            data["vendor"] = vendor_data
            
        if data.get("customer"):
            customer_data = data["customer"]
            customer_fields = {k: v for k, v in customer_data.items()}
            for key in customer_fields:
                if f"customer.{key}" not in selected_fields:
                    customer_data[key] = None
            data["customer"] = customer_data
        
        # Handle financial fields
        if data.get("financials"):
            fin_data = data["financials"]
            fin_fields = {k: v for k, v in fin_data.items()}
            for key in fin_fields:
                if f"financials.{key}" not in selected_fields:
                    fin_data[key] = None
            data["financials"] = fin_data
        
        # Handle line items
        if "line_items" not in selected_fields:
            data["line_items"] = []
        
        filtered = Invoice(**data)
    
    return filtered


def filter_result_by_fields(result: ExtractionResult, selected_fields: list[str]) -> ExtractionResult:
    """
    Create a filtered copy of extraction result containing only selected fields.
    
    Args:
        result: Original extraction result
        selected_fields: List of field names to keep
        
    Returns:
        Filtered extraction result
    """
    filtered = result.model_copy(deep=True)
    filtered.invoice = filter_invoice_by_fields(result.invoice, selected_fields)
    filtered.selected_fields = selected_fields
    return filtered
