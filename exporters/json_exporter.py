"""JSON export."""

from __future__ import annotations

import json

from models.invoice_schema import ExtractionResult


def to_json_bytes(result: ExtractionResult) -> bytes:
    return json.dumps(result.model_dump(), indent=2, ensure_ascii=False).encode("utf-8")
