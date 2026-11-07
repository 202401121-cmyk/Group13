-- US-52: one row per finished search, saved by the backend at the end of the pipeline.
-- Stored: the session ID, the rubric the search used and the whole SearchResult, which holds the request as typed.
-- No IP address, user agent, name or email. Searches may be personal: never send them to a model service that may
-- train on its inputs.
--
-- There is no foreign key to public.requests: saving a request can fail while the search saves later, and that
-- search must still be kept. session_id is not unique, because one session can run more than one search.

create table public.searches (
  id uuid primary key default gen_random_uuid(),
  -- the contract's SessionId (backend/app/contracts/common.py), the same check as public.requests
  session_id text not null check (session_id ~ '^sess_[A-Za-z0-9]{6,40}$'),
  -- SearchResult.request.rubric, kept in its own column so a search can be read without opening the result
  rubric jsonb not null,
  -- the whole SearchResult, as model_dump(mode="json") writes it
  result jsonb not null,
  created_at timestamptz not null default now()
);

create index on public.searches (session_id);

-- Supabase exposes tables in public through its Data API and grants new tables to anon and authenticated.
-- Row level security with no policies, plus the revoke, means only the backend's own database login can use it.
alter table public.searches enable row level security;
revoke all on table public.searches from anon, authenticated;

comment on table public.searches is 'One row per finished search. Written only by the backend.';
