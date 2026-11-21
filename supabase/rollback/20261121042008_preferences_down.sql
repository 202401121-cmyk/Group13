-- The down SQL for supabase/migrations/20261121042008_create_preferences.sql (US-06, #61).
--
-- Migrations here only move forward, and `db push` never reads this folder. Run this by hand only to take the
-- preferences table back out, then mark the migration reverted in Supabase's history (docs/database.md,
-- "Preferences (US-06)"). It deletes every remembered item: there is no undo.
--
-- It undoes exactly what the up added, and nothing from earlier migrations: the table (with its index, policy and
-- trigger), the function's comment, and pgvector while nothing else uses it. Safe to run more than once.

drop table if exists public.preferences;

comment on function public.keep_user_id_fixed() is
  'Refuses any change of user_id on requests and searches (US-30, D-72). A trigger function: never called directly.';

-- pgvector stays if another table still uses it. The extensions schema stays: it is Supabase's own.
do $$
begin
  drop extension if exists vector;
exception
  when dependent_objects_still_exist then
    raise notice 'pgvector stays: another object still uses it';
end
$$;
