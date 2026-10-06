"""
Token usage tracking.

Every call to the vision/chat model (GPT-4.1 via Azure OpenAI) reports a
`usage` object with prompt/completion/total tokens. This module:

  1. Logs that usage (to the standard logger, so it shows up in your
     existing logs / log aggregator), and
  2. Persists it to a Supabase Postgres table (`token_usage`), one row per
     API call, so you can query "how many tokens did session X / file Y /
     today cost me".

Nothing here depends on Streamlit — `extract.py` and the CLI both get
tracking for free. `app.py` just needs to pass a `session_id` through.

Backing store: Supabase (hosted Postgres), via the `supabase` Python
client. Configure with SUPABASE_URL + SUPABASE_KEY (see config.py). Create
the table once in the Supabase SQL editor — see `SUPABASE_SCHEMA.sql` in
this package for the exact statement.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from supabase import Client, create_client

from config import SUPABASE_KEY, SUPABASE_URL, get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_client: Client | None = None

TABLE = "token_usage"

# If Supabase is unreachable (DNS blip, network drop, etc.) even after
# retrying, we append the row here instead of silently dropping it. Each
# line is one JSON record — same shape as the `token_usage` row — so it can
# be inspected or backfilled into Supabase later.
FALLBACK_LOG_PATH = Path(__file__).resolve().parent.parent / "usage_fallback.jsonl"

# Insert retry policy: a transient DNS/network error is usually gone within
# a second or two, so a couple of short retries covers most blips without
# noticeably delaying extraction.
_INSERT_MAX_ATTEMPTS = 3
_INSERT_RETRY_DELAY_SECONDS = 1.5

# Rough $ / 1M tokens. Edit these to match your actual Azure OpenAI
# pricing/region — this is only used for the (optional) estimated_cost
# column. The UI no longer displays cost/token details, but they're still
# computed and persisted here in case you need them later (admin view,
# billing export, etc).
MODEL_PRICING_PER_MILLION = {
    # model name (as it appears in GPT_MODEL / deployment) -> (input, output)
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}
DEFAULT_PRICING = (2.00, 8.00)  # fallback if model name isn't in the table above

# Cached prompt tokens (prompt_tokens_details.cached_tokens) are billed at a
# discount vs. fresh input tokens — 50% for the gpt-4.1/gpt-4o families.
# Without this, estimated_cost overstates real spend whenever the prompt
# (e.g. the system prompt + a repeated document) gets a cache hit.
CACHED_INPUT_DISCOUNT = 0.5


def _pricing_for(model: str) -> tuple[float, float]:
    key = (model or "").lower()
    for name, price in MODEL_PRICING_PER_MILLION.items():
        if name in key:
            return price
    return DEFAULT_PRICING


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    in_price, out_price = _pricing_for(model)
    cached_tokens = min(cached_tokens, prompt_tokens)  # never discount more than we billed
    fresh_prompt_tokens = prompt_tokens - cached_tokens
    cached_price = in_price * CACHED_INPUT_DISCOUNT
    return (
        (fresh_prompt_tokens / 1_000_000) * in_price
        + (cached_tokens / 1_000_000) * cached_price
        + (completion_tokens / 1_000_000) * out_price
    )


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def _get_client() -> Client:
    """Lazily create (and cache) the Supabase client."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                if not SUPABASE_URL or not SUPABASE_KEY:
                    raise RuntimeError(
                        "SUPABASE_URL / SUPABASE_KEY are not set. Add them to your "
                        "environment or .env file (see config.py)."
                    )
                _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def init_db() -> None:
    """
    No-op for Supabase in the sense that we don't create tables here — the
    `token_usage` table is managed via a migration (see
    SUPABASE_SCHEMA.sql), not created on the fly by the app. We still probe
    it on startup so misconfiguration (missing credentials, missing table)
    surfaces immediately instead of silently dropping usage rows later.
    """
    try:
        client = _get_client()
        client.table(TABLE).select("id").limit(1).execute()
    except Exception:
        logger.exception(
            "Could not reach Supabase table '%s'. Make sure SUPABASE_URL/"
            "SUPABASE_KEY are set and the table has been created "
            "(see SUPABASE_SCHEMA.sql).",
            TABLE,
        )
        raise


def _serialize_content(content) -> dict | list | str | int | float | bool | None:
    """
    Best-effort conversion of "what the LLM extracted" into something
    JSON-serializable for the `extracted_content` jsonb column. Accepts a
    Pydantic model (e.g. the parsed `Invoice`), a plain dict/list, or None.
    """
    if content is None:
        return None
    # Pydantic v2 model
    if hasattr(content, "model_dump"):
        try:
            return content.model_dump(mode="json")
        except Exception:
            logger.exception("Failed to model_dump() extracted_content; storing None")
            return None
    # Pydantic v1 model (just in case)
    if hasattr(content, "dict"):
        try:
            return content.dict()
        except Exception:
            logger.exception("Failed to .dict() extracted_content; storing None")
            return None
    if isinstance(content, (dict, list, str, int, float, bool)):
        return content
    # Fall back to string representation rather than dropping it silently.
    return str(content)


def _insert_with_retry(record: dict, *, session_id: str, file_name: str | None) -> None:
    """
    Insert one usage row into Supabase, retrying a couple of times on
    transient errors (e.g. a momentary DNS/network failure) before falling
    back to a local file so the row is never silently lost.

    Usage logging must never break extraction — this always returns
    normally, even when the row ends up only in the fallback file.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _INSERT_MAX_ATTEMPTS + 1):
        try:
            _get_client().table(TABLE).insert(record).execute()
            if attempt > 1:
                logger.info(
                    "Persisted token usage to Supabase on retry attempt %d "
                    "[session=%s file=%s]",
                    attempt, session_id, file_name,
                )
            return
        except Exception as exc:  # noqa: BLE001 - broad on purpose, see docstring
            last_exc = exc
            if attempt < _INSERT_MAX_ATTEMPTS:
                logger.warning(
                    "Supabase insert failed (attempt %d/%d) [session=%s file=%s]: %s "
                    "— retrying in %.1fs",
                    attempt, _INSERT_MAX_ATTEMPTS, session_id, file_name, exc,
                    _INSERT_RETRY_DELAY_SECONDS,
                )
                time.sleep(_INSERT_RETRY_DELAY_SECONDS)

    logger.exception(
        "Failed to persist token usage to Supabase after %d attempts "
        "[session=%s file=%s]; writing to local fallback log instead",
        _INSERT_MAX_ATTEMPTS, session_id, file_name,
        exc_info=last_exc,
    )
    _write_fallback(record)


def _write_fallback(record: dict) -> None:
    """Append one usage record as a JSON line to the local fallback log.
    Best-effort — if even this fails, we just log it and move on."""
    try:
        with _lock, open(FALLBACK_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        logger.exception(
            "Failed to write token usage to local fallback log at %s "
            "(usage row lost)",
            FALLBACK_LOG_PATH,
        )


def log_usage(
    *,
    session_id: str,
    model: str,
    usage,
    file_name: str | None = None,
    extraction_path: str | None = None,
    call_type: str = "extraction",
    extracted_content=None,
) -> dict:
    """
    Record one API call's token usage.

    `usage` is the `.usage` object returned on the completion (has
    `.prompt_tokens`, `.completion_tokens`, `.total_tokens`, and optionally
    `.prompt_tokens_details.cached_tokens`). Also accepts a plain dict with
    the same keys, for callers that don't have the SDK object handy.

    `extracted_content` is optional — pass the model's structured output
    (e.g. the parsed `Invoice`) to persist "what the LLM produced" for this
    call alongside its token/cost numbers. Accepts a Pydantic model, a
    plain dict/list, or None.
    """
    if usage is None:
        prompt_tokens = completion_tokens = total_tokens = cached_tokens = 0
    else:
        def _get(obj, name, default=0):
            if isinstance(obj, dict):
                return obj.get(name, default)
            return getattr(obj, name, default)

        prompt_tokens = _get(usage, "prompt_tokens", 0) or 0
        completion_tokens = _get(usage, "completion_tokens", 0) or 0
        total_tokens = _get(usage, "total_tokens", prompt_tokens + completion_tokens) or 0
        details = _get(usage, "prompt_tokens_details", None)
        cached_tokens = _get(details, "cached_tokens", 0) if details else 0

    cost = estimate_cost(model, prompt_tokens, completion_tokens, cached_tokens)

    record = {
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file_name": file_name,
        "extraction_path": extraction_path,
        "call_type": call_type,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": cost,
        "extracted_content": _serialize_content(extracted_content),
    }

    _insert_with_retry(record, session_id=session_id, file_name=file_name)

    logger.info(
        "Token usage [session=%s file=%s path=%s model=%s]: prompt=%d completion=%d total=%d (~$%.4f)",
        session_id, file_name, extraction_path, model,
        prompt_tokens, completion_tokens, total_tokens, cost,
    )
    return record


def _empty_summary() -> dict:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
    }


def get_session_summary(session_id: str) -> dict:
    try:
        resp = (
            _get_client()
            .table(TABLE)
            .select(
                "prompt_tokens, completion_tokens, cached_tokens, total_tokens, estimated_cost"
            )
            .eq("session_id", session_id)
            .execute()
        )
    except Exception:
        logger.exception("Failed to fetch session summary from Supabase [session=%s]", session_id)
        return _empty_summary()

    rows = resp.data or []
    summary = _empty_summary()
    summary["calls"] = len(rows)
    for r in rows:
        summary["prompt_tokens"] += r.get("prompt_tokens", 0) or 0
        summary["completion_tokens"] += r.get("completion_tokens", 0) or 0
        summary["cached_tokens"] += r.get("cached_tokens", 0) or 0
        summary["total_tokens"] += r.get("total_tokens", 0) or 0
        summary["estimated_cost"] += r.get("estimated_cost", 0.0) or 0.0
    return summary


def get_session_records(session_id: str) -> list[dict]:
    try:
        resp = (
            _get_client()
            .table(TABLE)
            .select("*")
            .eq("session_id", session_id)
            .order("created_at", desc=False)
            .execute()
        )
        return resp.data or []
    except Exception:
        logger.exception("Failed to fetch session records from Supabase [session=%s]", session_id)
        return []


def get_recent_sessions_summary(limit: int = 20) -> list[dict]:
    """
    All-time, grouped by session — handy for an admin/usage-history view.

    Supabase's REST layer (PostgREST) doesn't do arbitrary GROUP BY through
    the client library, so we pull the raw rows and aggregate in Python.
    For very large tables, replace this with a Postgres view/RPC
    (`session_usage_summary`) and call it via `.rpc(...)` instead.
    """
    try:
        resp = (
            _get_client()
            .table(TABLE)
            .select("session_id, created_at, total_tokens, estimated_cost")
            .execute()
        )
        rows = resp.data or []
    except Exception:
        logger.exception("Failed to fetch recent sessions summary from Supabase")
        return []

    by_session: dict[str, dict] = {}
    for r in rows:
        sid = r["session_id"]
        agg = by_session.setdefault(
            sid,
            {
                "session_id": sid,
                "started_at": r["created_at"],
                "last_call_at": r["created_at"],
                "calls": 0,
                "total_tokens": 0,
                "estimated_cost": 0.0,
            },
        )
        agg["calls"] += 1
        agg["total_tokens"] += r.get("total_tokens", 0) or 0
        agg["estimated_cost"] += r.get("estimated_cost", 0.0) or 0.0
        agg["started_at"] = min(agg["started_at"], r["created_at"])
        agg["last_call_at"] = max(agg["last_call_at"], r["created_at"])

    sessions = sorted(by_session.values(), key=lambda s: s["last_call_at"], reverse=True)
    return sessions[:limit]