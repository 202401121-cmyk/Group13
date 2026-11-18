-- US-30 criterion 4 (S4-27, D-72, approved 16 Nov 2026; P-29): a row's owner never changes once it is saved.
--
-- A guest's request or search (user_id null) is never linked to an account later, and a signed-in user's row never
-- moves to another user or back to a guest. So any update that changes user_id on public.requests or
-- public.searches is refused. Inserts are untouched (a user's row is saved with their id), and so are deletes:
-- deleting a user still deletes their rows through the foreign key's cascade, which never updates user_id.
--
-- Ships in the same push as 20261115141557_add_user_tables. Safe to run more than once, and on a plain Postgres.

-- 1. The check. A fixed, empty search_path: it reads only the row, never a table, so nothing can be swapped in.
create or replace function public.keep_user_id_fixed()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.user_id is distinct from old.user_id then
    raise exception 'A row''s owner never changes once saved (US-30, D-72): user_id stays as it was'
      using errcode = 'check_violation',
            hint = 'A guest''s row is never linked to an account; a user''s row never moves to another user.';
  end if;
  return new;
end
$$;

comment on function public.keep_user_id_fixed() is
  'Refuses any change of user_id on requests and searches (US-30, D-72). A trigger function: never called directly.';

-- 2. anon and authenticated may not execute it. A trigger still fires for every update: Postgres checks EXECUTE only
--    when the trigger is made. Functions are executable by PUBLIC by default, and Supabase also grants new ones to anon
--    and authenticated directly, so both revokes are needed.
revoke all on function public.keep_user_id_fixed() from public;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on function public.keep_user_id_fixed() from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on function public.keep_user_id_fixed() from authenticated;
  end if;
end
$$;

-- 3. The triggers. "update of user_id" fires only when an update names that column; the check then refuses a real
--    change and lets the same value through.
drop trigger if exists requests_user_id_fixed on public.requests;
create trigger requests_user_id_fixed
  before update of user_id on public.requests
  for each row execute function public.keep_user_id_fixed();

drop trigger if exists searches_user_id_fixed on public.searches;
create trigger searches_user_id_fixed
  before update of user_id on public.searches
  for each row execute function public.keep_user_id_fixed();
