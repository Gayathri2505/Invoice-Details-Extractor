"""
Central configuration. Loads from environment / .env.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

# --- Azure OpenAI ---
# AZURE_OPENAI_DEPLOYMENT is the name YOU gave the deployment in Azure AI
# Foundry / Azure OpenAI Studio when you deployed gpt-4.1 — it is NOT
# necessarily "gpt-4.1" itself. Check your Azure resource's "Deployments" tab.
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")  # e.g. https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")

GPT_MODEL = AZURE_OPENAI_DEPLOYMENT  # kept for logging/readability elsewhere

# The openai SDK's default request timeout is 10 minutes with no visible
# feedback while waiting — on a flaky network (e.g. the same kind of DNS/
# connectivity blip that can also hit Supabase) this makes the app look
# "stuck" rather than failing with a clear, actionable error. We cap it
# much lower and let the SDK's built-in retry handle brief transient
# blips; anything beyond that surfaces as a real error instead of a
# silent multi-minute hang.
AZURE_OPENAI_TIMEOUT_SECONDS = float(os.getenv("AZURE_OPENAI_TIMEOUT_SECONDS", "90"))
AZURE_OPENAI_MAX_RETRIES = int(os.getenv("AZURE_OPENAI_MAX_RETRIES", "2"))

# For digital PDFs with reliable embedded text, should we ALSO attach a
# rendered page image (the "combined" text+vision path)? This roughly
# doubles prompt tokens per call for those documents since the image adds
# a large chunk of vision tokens that plain text alone doesn't need.
# Default OFF to save cost; flip to "true" only if you're seeing text-only
# extraction miss things a human would catch from the layout/image (e.g.
# tables that don't extract cleanly as text).
EXTRACTION_USE_COMBINED_PATH = os.getenv("EXTRACTION_USE_COMBINED_PATH", "false").lower() == "true"

# Rendering DPI when converting a PDF page to an image for the vision path.
# 200 is a reasonable balance between legibility (small fonts, dense tables)
# and payload size / cost.
PDF_RENDER_DPI = int(os.getenv("PDF_RENDER_DPI", "200"))

# Below this character count, we treat a "digital" PDF's embedded text layer
# as unreliable/garbage (e.g. a bad OCR text layer on a scanned PDF) and fall
# back to the vision path instead of trusting the text.
MIN_RELIABLE_TEXT_CHARS = int(os.getenv("MIN_RELIABLE_TEXT_CHARS", "40"))

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}

# --- Token usage tracking (Supabase Postgres) ---
# Every LLM call's prompt/completion/total tokens (+ estimated cost) is
# persisted to a `token_usage` table in Supabase — one row per call. See
# usage/tracker.py and SUPABASE_SCHEMA.sql for the table definition.
# Get these from your Supabase project: Settings -> API.
#   SUPABASE_URL  -> "Project URL"
#   SUPABASE_KEY  -> "service_role" key (server-side use only — this app
#                     never runs in the browser, so it's safe here, but
#                     never ship it to a client). A "anon" key also works
#                     if you set up matching RLS policies instead.
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)