-- Run this once in the Supabase SQL editor (Project -> SQL Editor -> New query)
-- to create the table that usage/tracker.py reads/writes.

create table if not exists public.token_usage (
    id                bigint generated always as identity primary key,
    session_id        text not null,
    created_at        timestamptz not null default now(),  -- when this call happened
    file_name         text,
    extraction_path   text,
    call_type         text,
    model             text,
    prompt_tokens     integer not null default 0,
    completion_tokens integer not null default 0,
    cached_tokens     integer not null default 0,
    total_tokens      integer not null default 0,
    estimated_cost    double precision not null default 0,
    extracted_content jsonb  -- the LLM's structured output for this call (e.g. the extracted invoice)
);

create index if not exists idx_token_usage_session on public.token_usage (session_id);
create index if not exists idx_token_usage_created_at on public.token_usage (created_at);

-- ----------------------------------------------------------------------------
-- Migrating an EXISTING table (created before timestamp/extracted_content
-- were added)? Run this instead of / in addition to the CREATE TABLE above:
-- ----------------------------------------------------------------------------
-- alter table public.token_usage
--   alter column created_at type timestamptz
--     using to_timestamp(created_at::double precision),
--   alter column created_at set default now();
--
-- alter table public.token_usage
--   add column if not exists extracted_content jsonb;


-- Row Level Security: enabled by default on new Supabase projects.
-- This app talks to Supabase with the service_role key (server-side only),
-- which bypasses RLS, so no policies are strictly required. If you'd
-- rather use the anon key, enable RLS and add matching policies, e.g.:
--
-- alter table public.token_usage enable row level security;
--
-- create policy "service role full access"
--   on public.token_usage
--   for all
--   to service_role
--   using (true)
--   with check (true);