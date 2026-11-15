-- US-30 (#52): accounts. A signed-in shopper's requests and searches carry their user id, each user has a profile, and
-- row level security lets a signed-in user read only their own rows through the Data API.
--
-- Guests (D-36): their rows keep user_id null. They are never linked to an account, and no policy ever shows them.
-- Writing stays with the backend through DATABASE_URL: anon gets nothing, and authenticated may only read its own rows
-- and change its own display name. Supabase Auth (Google sign-in) owns auth.users; this migration never changes it.
--
-- Safe to run more than once, and on a plain Postgres in tests (guards below).

-- 1. Supabase's auth schema, only where it does not exist: a plain Postgres in tests. On Supabase the schema,
--    auth.users and auth.uid() all exist, and nothing here touches them.
do $$
begin
  if not exists (select 1 from pg_namespace where nspname = 'auth') then
    create schema auth;
    if exists (select 1 from pg_roles where rolname = 'anon') then
      grant usage on schema auth to anon;
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
      grant usage on schema auth to authenticated;
    end if;
  end if;
  if to_regclass('auth.users') is null then
    create table auth.users (id uuid primary key);
  end if;
  if to_regprocedure('auth.uid()') is null then
    -- the same reading of the signed-in user as Supabase's: the JWT's "sub", set per request by the Data API
    create function auth.uid() returns uuid language sql stable as $uid$
      select coalesce(
        nullif(current_setting('request.jwt.claim.sub', true), ''),
        (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
      )::uuid
    $uid$;
  end if;
end
$$;

-- 2. The owner of a request or search. Null for a guest, and for every row saved before sign-in existed.
alter table public.requests add column if not exists user_id uuid;
alter table public.searches add column if not exists user_id uuid;

do $$
begin
  if not exists (select 1 from pg_constraint
                 where conname = 'requests_user_id_fkey' and conrelid = 'public.requests'::regclass) then
    alter table public.requests
      add constraint requests_user_id_fkey foreign key (user_id) references auth.users (id) on delete cascade;
  end if;
  if not exists (select 1 from pg_constraint
                 where conname = 'searches_user_id_fkey' and conrelid = 'public.searches'::regclass) then
    alter table public.searches
      add constraint searches_user_id_fkey foreign key (user_id) references auth.users (id) on delete cascade;
  end if;
end
$$;

-- the policies below filter on user_id, and deleting a user looks rows up by it
create index if not exists requests_user_id_idx on public.requests (user_id);
create index if not exists searches_user_id_idx on public.searches (user_id);

comment on column public.requests.user_id is
  'The signed-in owner (US-30). Null for a guest: a guest''s request is never linked to an account (D-36).';
comment on column public.searches.user_id is
  'The signed-in owner (US-30). Null for a guest: a guest''s search is never linked to an account (D-36).';

-- 3. One profile per user, made by the backend at first sign-in. No email: Supabase Auth keeps that.
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  -- MeResponse.display_name (backend/app/contracts/account.py): none, or 1 to 80 characters with no space at either
  -- end (the contract strips spaces, so "   " would be empty there). A user can set it through the Data API.
  display_name text check (
    display_name is null or (char_length(display_name) between 1 and 80 and display_name ~ '^\S(.*\S)?$')
  ),
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

comment on table public.profiles is
  'One row per signed-in user (US-30). Written by the backend; a user may read and rename only their own.';

-- 4. What the Data API roles may do. Supabase grants new tables to anon and authenticated, so everything is revoked
--    first. anon: nothing. authenticated: read, and on profiles change display_name only; the policies then limit it
--    to the user's own rows. The roles exist on Supabase; the guards let this also run on a plain Postgres.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on table public.requests, public.searches, public.profiles from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on table public.requests, public.searches, public.profiles from authenticated;
    grant select on table public.requests, public.searches, public.profiles to authenticated;
    grant update (display_name) on table public.profiles to authenticated;

    -- (select auth.uid()) is worked out once per query, not once per row. A guest row (user_id null) and a session
    -- with no signed-in user never match.
    drop policy if exists requests_select_own on public.requests;
    create policy requests_select_own on public.requests
      for select to authenticated using (user_id = (select auth.uid()));

    drop policy if exists searches_select_own on public.searches;
    create policy searches_select_own on public.searches
      for select to authenticated using (user_id = (select auth.uid()));

    drop policy if exists profiles_select_own on public.profiles;
    create policy profiles_select_own on public.profiles
      for select to authenticated using (id = (select auth.uid()));

    drop policy if exists profiles_update_own on public.profiles;
    create policy profiles_update_own on public.profiles
      for update to authenticated using (id = (select auth.uid())) with check (id = (select auth.uid()));
  end if;
end
$$;
