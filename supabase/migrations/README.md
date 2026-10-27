# Database migrations

This folder holds every change to the database schema, as SQL files that run in order.
The first table is `public.requests` (US-01, #14): one row per shopper request. See [docs/database.md](../../docs/database.md)
for how migrations are applied to the Supabase project.

## How to add a change

Every schema change is a **new** SQL file. Create it with the Supabase CLI:

```bash
supabase migration new <short_name>
```

This makes a file named `<timestamp>_<short_name>.sql`, for example `20261020093000_create_searches.sql`.
The timestamp keeps the files in the order they must run. Use a short, plain name that says what changes.

## Rules

- **Never edit a migration that is already merged.** Other members' databases and the shared one have already
  run it, so a change to it would never reach them. Write a new migration that makes the change instead.
- **The Data & Evaluation Engineer owns migrations.**
