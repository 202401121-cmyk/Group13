-- US-15 (DR-06): the names a product went by in earlier searches, so later searches give it the same product id.
-- Written by the backend's match step after a search, read before the next one. One row per alias and category.
-- An alias is a product name in normalised form (lower case, generic words removed, at most 80 characters): a name,
-- never a sentence. Names are kept from any source, Reddit included (decided by Om, 11 Nov 2026). No shopper data:
-- no session ID, no request text.

create table public.product_aliases (
  -- the normalised name, as app/entities/normalise.py makes it
  alias text not null check (char_length(alias) between 1 and 80 and alias = btrim(alias)),
  -- the contract's ProductId (backend/app/contracts/common.py)
  product_id text not null check (product_id ~ '^prd_[A-Za-z0-9_-]{3,64}$'),
  -- the contract's Slug: the category the search was for, such as laptop or headphones
  category text not null check (category ~ '^[a-z0-9]+(?:[_-][a-z0-9]+)*$' and char_length(category) <= 64),
  first_seen timestamptz not null default now(),
  -- set again each time a search uses the alias, so old ones can be found and cleared later
  last_seen timestamptz not null default now(),
  primary key (category, alias)
);

create index on public.product_aliases (category, product_id);

-- Supabase exposes tables in public through its Data API and grants new tables to anon and authenticated.
-- Row level security with no policies, plus the revoke, means only the backend's own database login can use it.
-- The guard lets the migration also run on a plain Postgres, where Supabase's roles don't exist.
alter table public.product_aliases enable row level security;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on table public.product_aliases from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on table public.product_aliases from authenticated;
  end if;
end
$$;

comment on table public.product_aliases is 'Product aliases kept between searches (US-15, DR-06). Written only by the backend.';
