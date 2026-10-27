-- US-01 (#14): one row per shopper request, saved by the backend with its session ID.
-- Stored: the session ID, the request as typed, the detected intent (when it is ready for research) and how it
-- was detected. No IP address, user agent, name or email. Requests may be personal: never send them to a model
-- service that may train on its inputs.

create table public.requests (
  id uuid primary key default gen_random_uuid(),
  -- the contract's SessionId (backend/app/contracts/common.py)
  session_id text not null check (session_id ~ '^sess_[A-Za-z0-9]{6,40}$'),
  -- Intent.raw_query allows 1 to 1000 characters
  request_text text not null check (char_length(request_text) between 1 and 1000),
  intent jsonb,
  -- Intent.detected_by: the LLM, or the keyword rules when no LLM answered
  intent_source text check (intent_source in ('llm', 'rules')),
  created_at timestamptz not null default now()
);

create index on public.requests (session_id);

-- Supabase exposes tables in public through its Data API and grants new tables to anon and authenticated.
-- Row level security with no policies, plus the revoke, means only the backend's own database login can use it.
alter table public.requests enable row level security;
revoke all on table public.requests from anon, authenticated;

comment on table public.requests is 'One row per shopper request. Written only by the backend.';
