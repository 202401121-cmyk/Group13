-- US-06 (#61, P-40): what ShopSense remembers for a signed-in shopper. One row per remembered item, in the shape of
-- PreferenceItem (backend/app/contracts/memory.py): a weight for a criterion, a must-have or a leave-out, with the
-- shopper's own words and an embedding of 384 numbers for finding a similar item later.
--
-- Only signed-in users have rows: user_id is required, and a guest has no memory at all (D-36). Row level security
-- lets a signed-in user read only their own rows through the Data API; anon gets nothing, and every insert, change and
-- delete stays with the backend through DATABASE_URL, as for requests and searches. A row's owner never changes
-- (D-72), and deleting the user deletes their rows.
--
-- No vector index (HNSW): every look-up is for one user and one category, a few hundred rows at most, and pgvector's
-- exact scan of those is fast and never misses a row. The reasoning and the timing are in docs/database.md,
-- "Preferences (US-06)".
--
-- Migrations here only move forward; the matching down SQL is supabase/rollback/20261121042008_preferences_down.sql.
-- Safe to run more than once, and on a plain Postgres that has pgvector, as in the tests (guards below).

-- 1. pgvector, in Supabase's extensions schema, never in public. On Supabase the schema exists and is left as it is;
--    on a plain Postgres (the tests) it is made here, empty, as pgvector's home.
do $$
begin
  if not exists (select 1 from pg_namespace where nspname = 'extensions') then
    create schema extensions;
  end if;
end
$$;

create extension if not exists vector with schema extensions;

-- 2. The table.
create table if not exists public.preferences (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  kind text not null check (kind in ('weight', 'hard_rule', 'exclusion')),
  -- a slug, as in the contract. None only for a brand left out with Always, which holds for every category (US-10)
  category text check (category is null or (char_length(category) <= 64 and category ~ '^[a-z0-9]+([_-][a-z0-9]+)*$')),
  criterion_key text check (
    criterion_key is null or (char_length(criterion_key) <= 64 and criterion_key ~ '^[a-z0-9]+([_-][a-z0-9]+)*$')
  ),
  -- what the shopper told us, in plain words (MAX_PREFERENCE_TEXT); the contract strips spaces at either end
  text text not null check (char_length(text) between 1 and 200 and text ~ '^\S(.*\S)?$'),
  -- NaN and infinity fail "between", as they should
  weight real check (weight is null or weight between 0 and 10),
  -- a must-have's HardRule, or a leave-out's Exclusion, as the contract's JSON; none for a weight
  rule jsonb check (rule is null or jsonb_typeof(rule) = 'object'),
  always boolean not null default false,
  -- made after the item is saved, so it may be missing for a while
  embedding extensions.vector(384),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- each kind carries its own fields and none of the others', as PreferenceItem does
  constraint preferences_kind_fields check (
    (kind = 'weight' and criterion_key is not null and weight is not null and rule is null)
    or (kind = 'hard_rule' and rule is not null and weight is null)
    or (kind = 'exclusion' and rule is not null and weight is null and criterion_key is null)
  ),
  -- a must-have's rule has a HardRule's fields, and a leave-out's an Exclusion's
  constraint preferences_rule_shape check (
    (kind <> 'hard_rule' or coalesce(rule ?& array['attribute', 'operator', 'value', 'source_text'], false))
    and (
      kind <> 'exclusion'
      or coalesce(
        rule ->> 'kind' in ('brand', 'model', 'feature', 'seller') and jsonb_typeof(rule -> 'value') = 'string', false
      )
    )
  ),
  -- a leave-out's own Always is the item's Always
  constraint preferences_exclusion_always check (
    kind <> 'exclusion' or coalesce(rule -> 'always' = to_jsonb(always), false)
  ),
  constraint preferences_category_always check (
    (category is null) = coalesce(kind = 'exclusion' and always and rule ->> 'kind' = 'brand', false)
  ),
  constraint preferences_times check (updated_at >= created_at)
);

-- every look-up is one user's items in one category; the policy and deleting a user use user_id, the first column
create index if not exists preferences_user_id_category_idx on public.preferences (user_id, category);

alter table public.preferences enable row level security;

comment on table public.preferences is
  'What is remembered for a signed-in user (US-06, US-11): one weight, must-have or leave-out a row. Written by the '
  'backend; a user may read only their own. A guest has none (D-36).';
comment on column public.preferences.embedding is
  'The item''s words as 384 numbers, for finding a similar item. Read with an exact scan of one user''s rows.';

-- 3. What the Data API roles may do. Supabase grants new tables to anon and authenticated, so everything is revoked
--    first. anon: nothing. authenticated: read, and the policy limits that to the user's own rows. The roles exist on
--    Supabase; the guards let this also run on a plain Postgres.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on table public.preferences from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on table public.preferences from authenticated;
    grant select on table public.preferences to authenticated;

    -- (select auth.uid()) is worked out once per query, not once per row; a session with no user never matches
    drop policy if exists preferences_select_own on public.preferences;
    create policy preferences_select_own on public.preferences
      for select to authenticated using (user_id = (select auth.uid()));
  end if;
end
$$;

-- 4. The owner never changes (D-72): the same check as requests and searches, from 20261118104917_keep_owner_fixed.
drop trigger if exists preferences_user_id_fixed on public.preferences;
create trigger preferences_user_id_fixed
  before update of user_id on public.preferences
  for each row execute function public.keep_user_id_fixed();

comment on function public.keep_user_id_fixed() is
  'Refuses any change of user_id on requests, searches and preferences (US-30, US-06, D-72). A trigger function: '
  'never called directly.';
