# The database (Supabase Postgres)

ShopSense saves each shopper request, with its session ID, in a Supabase Postgres database (US-01, #14). It also has
a table for finished searches (US-52), one for product aliases kept between searches (US-15), a profile for each
signed-in user (US-30), and what is remembered for a signed-in user (US-06). The database is **optional**: with no
`DATABASE_URL` everything works and nothing is saved, and when the database is down, slow or paused a search still
finishes (build rule 10).

## What is stored, and why

One row per shopper request, in `public.requests`:

| Column | What it holds |
|---|---|
| `id` | A random UUID |
| `session_id` | The session the request belongs to, for example `sess_3f9a…` |
| `request_text` | The request exactly as typed (1 to 1000 characters) |
| `intent` | What we understood (category, use, budget, rules), only when it was clear enough to research |
| `intent_source` | How it was understood: `llm` or `rules` (the keyword rules, when no LLM answered) |
| `created_at` | When it was saved |
| `user_id` | The signed-in owner (US-30). Empty for a guest: see "Accounts" below |

- **Why:** to see what people really ask for, to build evaluation sets from real requests, and later to remember
  returning shoppers (US-06).
- **Not stored:** no IP address, user agent, name or email. A signed-in shopper's row holds only their user id.
- **Private:** requests are what people typed and may be personal. Never send stored requests to Gemini's free tier
  or any other model service that may train on its inputs. Never paste rows into chat, Slack or an issue.

Row level security is on. `anon` has no access, so the Data API with no one signed in can't read or write it. Since
US-30, a signed-in user (`authenticated`) may read their own rows and nothing else (see "Accounts"). Only the backend's
own database login can write.

### Finished searches

One row per finished search, in `public.searches` (US-52):

| Column | What it holds |
|---|---|
| `id` | A random UUID. This is not `SearchResult.search_id`, which is inside `result` |
| `session_id` | The session the search belongs to, with the same check as `public.requests` |
| `rubric` | The rubric the search used (`SearchResult.request.rubric`) |
| `result` | The whole `SearchResult` as `model_dump(mode="json")` writes it, with Reddit's words taken out (see below) |
| `created_at` | When it was saved |
| `user_id` | The signed-in owner (US-30). Empty for a guest: see "Accounts" below |

- **Why:** the pipeline keeps its state in memory and saves once at the end (US-52, build rule 10).
- **No foreign key to `public.requests`:** saving a request can fail while the search saves later, and that search
  must still be kept. A row in one table never needs a row in the other. The only foreign key is the owner's, to
  `auth.users`.
- **More than one per session:** one session can run more than one search, so `session_id` has an index but is not
  unique.
- **Not stored:** no IP address, user agent, name or email. A signed-in shopper's row holds only their user id.
- **Private:** `result` holds the request as typed, so this is private data, like `public.requests`. It stays in our
  database only. Never send it to Gemini's free tier or any other model service that may train on its inputs, and
  never paste rows into chat, Slack or an issue.
- **No expiry, so no Reddit text (US-12, D-53):** rows stay until someone deletes them, and Reddit data may be kept at
  most 24 hours. So Reddit's words never reach this table ("What a saved result holds", below). D-53 covers every copy, so
  the other two are limited as well: a finished run stays in the server's memory for `SEARCH_RUN_KEEP_MINUTES` (1 to 120
  minutes), even while a progress stream reads it, and the Reddit cache serves an entry only while it is younger than 24
  hours less that, less the longest search (150 seconds), one tick of the backend's own timer (10 minutes) and a
  60-second margin. The timer deletes expired copies within those 10 minutes, search or no search, so a run and a
  Reddit file are gone before 24 hours ([api-setup.md](api-setup.md), "Keeping Reddit data").
- **Guests (decision D-36):** there is no sign-in yet, so every row is a guest's. A guest's request and search are
  stored only under the anonymous session ID, are never linked to an account, and are never used to remember
  preferences.

### What a saved result holds

`rubric` holds the rubric as it was searched with, in the shopper's own words. `result` holds the whole `SearchResult` the
shopper saw, with one difference: no Reddit text. A row stays until someone deletes it, and D-53 lets us keep Reddit data
for at most 24 hours, so `without_reddit_text` in `backend/app/research/retention.py` runs at the save. The response to
the shopper is not changed, with one exception: a progress stream still open when its run is dropped at its keep-time
sends its final event through the same function (`app/api/search_runs.py`), so that copy holds no Reddit text either.
It runs nowhere else.

| Part of the result | In the saved row |
|---|---|
| A Reddit source's title, text and comment bodies, at any depth | The marker `[Reddit text is not kept]`. A merged thread's `Same topic: <url>` lines stay at the end of its text, and only those naming a subreddit we search, so they hold an address and no free words |
| A Reddit source's `text_hash` | The hash of what is saved, so it can't identify the original |
| Author hashes, on the thread and on each comment | Cleared. A hash can follow one person from thread to thread, and a saved row has no use for that |
| A Reddit source's URL, community, type, fetch time, publish time, archive, upvotes, comment count and quality | Kept |
| Each comment's ID, parent, depth, upvotes and quality | Kept |
| The `quote` of any claim, unless its source is listed and isn't Reddit | The marker. Its value, score and source link stay. A value can be a few of a poster's words, such as "about 8 hours" |
| A held product's filter reason that quotes a value made of Reddit's or a video's viewers' words (decide quotes up to 80 characters: `It fails "Wi-Fi 7": it has ...`) | The plain reason built from its rule: `It fails "Wi-Fi 7"`, or `We couldn't check "Wi-Fi 7" from its sources`. A reason is matched by the rule it quotes or the attribute it names ("Sources disagree on its ..."), or for "Sources disagree: A or B" by the replaced side it lists, never by loose words. One reason becomes one plain reason. A reason about another attribute, a budget's or a leave-out's holds no one's words and stays (S5-2, D-70) |
| Web, review and shop sources, and everything else in the result | Saved whole |

Since schema 1.7.0 a result carries `citations`: each claim a value cites, with its source and its quote. A citation's
quote is optional, so on save a Reddit one, or one whose source isn't listed, becomes `null`, not the marker. A result
with no Reddit sources is saved byte for byte as it was before.

`tests/unit/research/test_retention.py` lists every text field in a `SearchResult` and what it can hold, so a new field
fails until someone decides. It also fails when US-16, US-20 or US-42 makes real a stage whose fields could carry
Reddit's words (resolved values, community notes, explanations). Handle those in `retention.py` first. US-15 made match
real: product names are kept as names, from any source (Om, 11 Nov 2026), and the test records why.

Since schema 1.11.0 (US-29) a citation also keeps what its claim said and when: its value or score, unit, polarity,
type and retrieval time. A text value from Reddit or a video's viewers becomes the marker at the save (with its unit
dropped), so the claim's own words never reach the row; a number, a yes or no and a score stay. So every claim a value
cites is in the row with its source id, its quote (or `null` for Reddit), and its time, and
`GET /api/me/searches/{id}/evidence` rebuilds the same evidence the run gave.

**Every ranking decision names the claims behind it** through the value it read, already in the row: a contribution
(`decision.ranked[].contributions[]`) and a checked value (`decision.checked_values[]`) name a product and a criterion
or attribute, a held product (`decision.filtered[]`) names its `rule_attribute`, and `products[].values[]` holds that
product's value for it, with its supporting and conflicting claim ids. No new column or table was needed.

A reopened search shows Reddit evidence as links, not quotes: the marker's meaning in plain words, and the thread's URL.
The words can only be read on Reddit, as they were never kept.

**The random-claim test** (`tests/integration/test_saved_evidence_in_postgres.py`, `db`): each of the five recorded
gate runs is replayed and saved as the pipeline saves it; then 20 of its cited claims, picked with a fixed seed, are
found by SQL alone in the saved row, each with its source, its quote (or `null` for Reddit) and its time. Another test
checks that no Reddit or video-viewer text reaches a saved row.

Row level security is on, the same as `public.requests`: `anon` has no access, and a signed-in user may read their own
rows only (see "Accounts"). **Who can read it:** the backend through `DATABASE_URL`, project members through the
Supabase dashboard, and each signed-in user their own searches. Only the backend writes.

### Product aliases

One row per alias and category, in `public.product_aliases` (US-15, DR-06):

| Column | What it holds |
|---|---|
| `alias` | A product name in normalised form: lower case, generic words removed, at most 80 characters, for example `sony wh1000xm5` |
| `product_id` | The product it names, as the contract's `ProductId`, for example `prd_sony_wh1000xm5` |
| `category` | The category of the search that saved it, a slug such as `headphones` |
| `first_seen` | When a search first saved it |
| `last_seen` | When a search last saved it again |

- **Why:** a later search gives the same product the same id, even when its fullest name differs from the one an
  earlier search saw.
- **One row per alias and category** (the primary key): the first product id saved for an alias is kept, and saving it
  again only moves `last_seen` on. A search reads the most recently saved first, at most 5,000 per category.
- **What is saved:** only the names of a product that merged more than one name, and only names whose model codes all
  appear in the product's own name. A short form such as "XM5" is never saved, as it could name another product. A
  product with no model code keeps all its names, a misspelling included.
- **Not stored:** no session ID, request text or anything else about the shopper.
- **Reddit (D-53):** a normalised product name is a name, not a poster's words, so names are kept from any source,
  Reddit included, with no 24-hour expiry. Om decided this on 11 Nov 2026.
- **Optional, like the rest (D-28):** the backend also keeps aliases in memory. The pipeline reads a category's aliases
  in the background while research runs, and writes new ones in the background after match. So a database that is
  off, down or slow never delays a search; it only means fewer aliases. When the database is down, alias reads and
  writes count on the circuit breaker that request and search saves share, like any other call. A missing table does
  not: it is logged once, and that backend keeps aliases in memory until it is restarted, even after the migration is
  applied.

Row level security is on with no policies, and the table is revoked from `anon` and `authenticated`. The revoke is
guarded, so the migration also runs on a plain Postgres. So the Data API cannot read or write it. **Who can read it:**
the backend through `DATABASE_URL`, and project members through the Supabase dashboard. The Security Advisor shows
**RLS Enabled No Policy** (INFO) for this table. That is expected: it is the intended design. Since US-30 it is the only
table with that finding: the others have policies (`preferences` too, since US-06).

### Accounts (US-30)

Login uses Supabase Auth with Google sign-in. Supabase keeps the accounts in its own `auth.users` table, with the email
address; ShopSense never copies it. The migration `20261115141557_add_user_tables` adds:

- **`user_id`** on `public.requests` and `public.searches`: the signed-in owner, a `uuid` that refers to `auth.users`.
  It has an index. **Deleting a user deletes their requests, searches and profile** (`on delete cascade`).
- **`public.profiles`**, one row per signed-in user, made by the backend at first sign-in (S4-10):

  | Column | What it holds |
  |---|---|
  | `id` | The user's id, the same as in `auth.users` |
  | `display_name` | The name shown in the app, if any: 1 to 80 characters, with no space at either end |
  | `created_at` | When it was made |

**Guests (D-36).** A guest's request and search are saved with `user_id` empty. They are never linked to an account
later and never used to remember preferences. No policy ever shows a row with an empty `user_id`.

**An owner never changes (D-72, S4-27).** The migration `20261118104917_keep_owner_fixed` makes the database refuse any
update that changes `user_id` on `public.requests` or `public.searches`:

- a guest's row can never be given a `user_id`, so it is never linked to an account later;
- a user's row can never move to another user, or back to empty.

It is a `before update of user_id` trigger on each table (`requests_user_id_fixed`, `searches_user_id_fixed`), which
calls `public.keep_user_id_fixed()`. The refusal is a `check_violation` error. Saving a row with its owner still works,
other columns still update, and deleting a user still deletes their rows (the cascade deletes, it never updates).
The function has a fixed, empty `search_path`. `public`, `anon` and `authenticated` have no `execute` on it (and a
trigger function can't be called directly anyway). A trigger fires anyway, because Postgres checks `execute` only
when the trigger is made. Only the tables' owner could get round the rule, by switching triggers off on purpose
(for example `session_replication_role = replica`). The Data API's roles can't, and the backend never does.

The rule covers updates only. That a guest's search is never saved again under an account (for example a copy, or a
row inserted with a `user_id` under a guest's session ID) is for the sign-in code to keep (S4-10).

**Who can do what through the Data API** (the browser's publishable key, D-32):

| Role | `requests`, `searches` | `profiles` |
|---|---|---|
| `anon` (no one signed in) | nothing | nothing |
| `authenticated` (signed in) | read their own rows | read their own; change their own `display_name` only |

- The policies are `requests_select_own`, `searches_select_own`, `profiles_select_own` and `profiles_update_own`. Each
  compares the row's owner with `auth.uid()`, the user in the request's sign-in token.
- Neither `anon` nor `authenticated` can insert or delete anything. Every write stays with the backend, through
  `DATABASE_URL`. The secret key's `service_role` keeps the access Supabase gives it, as on the other tables; that key
  never leaves the backend (D-32).
- Supabase grants every new table to `anon` and `authenticated`, so the migration first revokes everything, then
  grants only what the table above shows.

**On a plain Postgres** (the tests), there is no `auth` schema. The migration then makes a small stand-in: an `auth`
schema with `auth.users (id)` and an `auth.uid()` that reads the user from the request's claims, as Supabase's does.
Each piece is made only when it is missing, so on Supabase nothing in `auth` is touched. The migration is safe to run
more than once.

### Preferences (US-06)

What ShopSense remembers for a signed-in shopper: their weights, must-haves and leave-outs, one item a row, in the
shape of `PreferenceItem` (`backend/app/contracts/memory.py`). The migration `20261121042008_create_preferences` adds
`public.preferences` and installs pgvector (the `vector` extension, 0.8.2) in the `extensions` schema, never in
`public`. Nothing writes to the table yet: US-06's memory store and US-11's routes will.

| Column | What it holds |
|---|---|
| `id` | A random UUID |
| `user_id` | The signed-in owner. Required: a guest has no memory at all (D-36) |
| `kind` | `weight`, `hard_rule` (a must-have) or `exclusion` (a leave-out) |
| `category` | The category it holds for, such as `laptop`. Empty only for a brand left out with Always, which holds for every category |
| `criterion_key` | A weight's criterion, such as `battery_life`. A must-have may name one too; a leave-out never does |
| `text` | What the shopper told us, in plain words: 1 to 200 characters |
| `weight` | A weight's 0 to 10 weight; empty for the other kinds |
| `rule` | A must-have's `HardRule` or a leave-out's `Exclusion`, as the contract's JSON; empty for a weight |
| `always` | Kept for every later search. A leave-out's own `always` in `rule` must be the same |
| `embedding` | The item's words as 384 numbers (`extensions.vector(384)`), for finding a similar item. Empty until it is made |
| `created_at`, `updated_at` | When it was saved and last changed. `updated_at` is never earlier |

- **Checks.** They follow the contract, so a row the table keeps always reads back as a `PreferenceItem`:
  - each kind carries its own fields and none of the others' (`preferences_kind_fields`);
  - a must-have's `rule` has a `HardRule`'s fields, and a leave-out's names a brand, model, feature or seller and a
    value (`preferences_rule_shape`);
  - a leave-out's own `always` matches the item's, and only a brand left out with Always has no category;
  - `category` and `criterion_key` are slugs, as in the contract: lower-case letters and digits joined by `_` or
    `-`, at most 64 characters;
  - `text` has no space at either end, as the contract strips them;
  - a weight outside 0 to 10, including NaN and infinity, is refused;
  - an embedding of any other length than 384 is refused ("expected 384 dimensions").
- **What the memory store must check first.** A few values the contract allows can't be stored: the character
  NUL in any text, and a weight too small for `real` (such as `1e-46`). US-06's store refuses or rounds them before
  saving, as `save_search` does with NUL.
- **Index.** `preferences_user_id_category_idx`, on `(user_id, category)`. Every look-up is one user's items in one
  category. The policy and deleting a user use its first column.
- **Who can do what through the Data API.** The same as `requests` and `searches`:
  - `anon` gets nothing;
  - `authenticated` reads its own rows, embeddings included (policy `preferences_select_own`,
    `user_id = (select auth.uid())`);
  - only the backend inserts, changes or deletes, through `DATABASE_URL`.
- **The owner never changes (D-72).** The trigger `preferences_user_id_fixed` calls the same
  `public.keep_user_id_fixed()` as `requests` and `searches`; its comment now names all three. Deleting a user deletes
  their preferences (`on delete cascade`).
- **Private.** `text` is what the shopper typed (D-51), and an embedding is made from it, so both are private data. No
  email, name or IP address is stored.
- **On a plain Postgres** (the tests), the migration makes the `extensions` schema when it is missing, empty. On
  Supabase the schema exists and is left as it is. pgvector itself must be on the server: the tests use
  `pgvector/pgvector:0.8.2-pg17`.

**No vector index (HNSW), decided 21 Nov 2026.** pgvector's README for 0.8.2 (github.com/pgvector/pgvector, tag
`v0.8.2`, read 21 Nov 2026) says:

- with no index, pgvector finds the nearest rows exactly, so it never misses one;
- HNSW is approximate: it trades some recall for speed, builds slower and uses more memory;
- with a `where` filter, an approximate index filters after its scan. It looks at 40 candidates by default
  (`hnsw.ef_search`) and keeps the ones that match, so a narrow filter can return fewer rows than asked for. Iterative
  scans (0.8.0 and later, `hnsw.iterative_scan`) find more, at a cost;
- for a filter that matches few rows, start with an ordinary index on the filter column, which gives fast exact search.

The timing, on 10,000 made-up rows:

- **Set-up.** A local `pgvector/pgvector:0.8.2-pg17`. One user at the 500-item cap (100 in each of 5 categories), and
  1,900 users with 5 items each. Every row has 384 random numbers.
- **The look-up.** The 5 nearest by cosine distance, for one user and one category, as the median of 40 runs.

| Look-up | No vector index | HNSW there too (the planner's choice) | HNSW alone | HNSW alone, iterative scan |
|---|---|---|---|---|
| User at the cap, one category (100 rows) | 0.24 ms, exact | 0.24 ms: the planner keeps the user-and-category index | 1.4 ms, finds 1 of the 5 | 8.5 ms, 4 of the 5 |
| User with 5 items, one category (1 row) | 0.05 ms, exact | 0.04 ms, the same index | 1.8 ms, finds none | 70 ms, finds it |

Building HNSW took 4.4 s, and the index was 20 MB, about the size of the table.

So there is no HNSW index:

- A user holds at most 500 items (`MAX_PREFERENCES`), and an exact scan of one user's category takes under a
  millisecond and never misses a row.
- At this size the planner doesn't use an HNSW index even when one exists, and on its own it misses rows.

Revisit it only if a look-up across users is added, which this design doesn't have. Then add HNSW in a new migration
(`create index concurrently ... using hnsw (embedding extensions.vector_cosine_ops)`), with `hnsw.iterative_scan` on.

### The shopper's controls, and deleting an account (US-11)

The backend's `/api/me/preferences` routes let a signed-in shopper list, edit, export and delete what is remembered
(docs/frontend-api.md). Each statement names the user (`where user_id = %s`, `backend/app/memory/store.py`), as well as
row level security, and an id that isn't theirs finds nothing.

- **Deleting a preference deletes its embedding:** the embedding is a column of the same row, so one `delete` takes
  both. No copy of it is kept anywhere else.
- **Editing the words** stores a new embedding in the same row. If the model can't make one just then, the row keeps
  its old one, and the route says so.
- **Deleting the account** (`DELETE /api/me`) asks Supabase's Auth admin API to delete the user, with the backend's
  `SUPABASE_SECRET_KEY` (docs/api-setup.md). The row leaves `auth.users`, and the foreign keys
  (`on delete cascade`) remove the user's rows in `requests`, `searches`, `profiles` and `preferences`.
- **The read-back:** the backend counts the user's rows in those four tables first (a database that can't answer
  means nothing is deleted), then again after the user's delete, and answers 204 only at zero. A row left means the
  user wasn't really deleted, since every one of those tables cascades: the answer is then 503, never 204.
- **A deleted preference stays deleted:** an interview that recalled it before the shopper deleted it only renews
  rows that still exist when it saves; it never makes a deleted one again.
- **Edits never write over a save:** an edit is written only if the row is still as it was read. Otherwise the route
  answers 409, and the shopper looks again.
- **A new table with a user:** it needs `on delete cascade` to `auth.users`, and a place in the read-back
  (`ACCOUNT_TABLES` in `backend/app/db/accounts.py`). `test_memory_controls_in_postgres.py` fails until both are done.

## Setting up the project (once, by hand, in the browser)

1. On supabase.com, create a project on the **Free** plan, region **South Asia (Mumbai)**.
   - Generate a strong database password and keep it in a password manager.
   - Invite Om (repo admin) and Jenish to the organisation.
2. Open **Project > Connect** and copy the **Session pooler** connection string.
   - Don't use the direct connection: on the Free plan it is IPv6 only and fails on most home and college networks.
     The session pooler works over IPv4.
3. Put it in the root `.env` (the same file as the LLM keys), with `?sslmode=require` at the end:
   ```
   DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<session-pooler-host>:5432/postgres?sslmode=require
   ```
   - The host is the one shown under Session pooler in the dashboard.
   - Never paste the URL or the password into chat, Slack, an issue or a commit. `.env` is git-ignored.
   - Don't create `backend/.env`: the backend would find it first and stop reading the root `.env`.
4. Note the project's Postgres major version (**Settings > Infrastructure**). It is **17**; the tests use the same.
5. Apply the migrations (next section), then open **Advisors > Security**. It should show only **RLS Enabled No
   Policy** (INFO) for `public.product_aliases`, which is expected: see the live check below. Before US-30's migration
   it also shows for `public.requests` and `public.searches`; after it, those two and `public.profiles` have policies
   (see "Accounts"), and so does `public.preferences` (US-06).

`DB_TIMEOUT_SECONDS` (default 2) is how long one call may take before the search carries on without it. A finished
search's save has its own limit, `DB_SAVE_TIMEOUT_SECONDS` (default 15, 2 to 15): see "Saving a finished search".

## Migrations

Every schema change is a new SQL file in `supabase/migrations/`, run in timestamp order. Never edit one that is
already merged; write a new one (see `supabase/migrations/README.md`). The Data & Evaluation Engineer reviews them.

Add one, from the repository root, with the pinned Supabase CLI:

```bash
npx supabase@2.120.0 migration new add_example_table   # use your own short name
```

Apply them to the Supabase project, from the repository root with the backend's virtual environment active (the
first line needs `python-dotenv`). The commands read `DATABASE_URL` from `.env` without printing it.

Git Bash:

```bash
export DATABASE_URL="$(python -c 'from dotenv import dotenv_values; print(dotenv_values(".env")["DATABASE_URL"])')"
npx supabase@2.120.0 db push --db-url "$DATABASE_URL"
unset DATABASE_URL
```

PowerShell:

```powershell
$env:DATABASE_URL = python -c "from dotenv import dotenv_values; print(dotenv_values('.env')['DATABASE_URL'])"
npx supabase@2.120.0 db push --db-url "$env:DATABASE_URL"
Remove-Item Env:DATABASE_URL
```

### After `create_searches` merges (once, by hand)

The migration `20261106160150_create_searches` is only a file until someone applies it.

1. Vaishvi applies it in her own terminal with `db push`, as above, and tells Om.
2. Om checks these read-only: the migration history lists it, row level security is on for `public.searches`, it has
   zero policies, `anon` and `authenticated` have no access, and what the Security Advisor shows.
3. Expect one new Security Advisor finding, **RLS Enabled No Policy** on `public.searches` (INFO). It is the same
   intended design as `public.requests` (see "Live check" below).

**Apply it before the pipeline starts saving searches.** Without the table every search save fails. Search saves and
request saves share one circuit breaker, so 3 failed searches in a row also stop request saves for 30 seconds, and
health says `down` even though the database is fine.

### `create_product_aliases` (applied 11 Nov 2026)

The migration `20261111083610_create_product_aliases` (US-15) was applied after its pull request (#82) merged.

1. Om applied it from a fresh `main` with `db push`, as above. The dry run listed this one migration only.
2. It was checked read-only the same day: see "Follow-up, 11 Nov 2026" under "Live check".
3. Expect one new Security Advisor finding, **RLS Enabled No Policy** on `public.product_aliases` (INFO). It is the
   same intended design as the other two tables.

Unlike `create_searches`, nothing fails without it: aliases stay in memory, and the missing table is logged once and
never counted on the circuit breaker.

### `add_user_tables` and `keep_owner_fixed` (after both merge, once, by hand, in one push)

The migrations `20261115141557_add_user_tables` (US-30) and `20261118104917_keep_owner_fixed` (S4-27, D-72) are only
files until someone applies them. They ship together, in the same `db push`, so the user tables never exist without
the rule that an owner never changes. Nothing in the backend needs them yet: S4-10's sign-in and API do.

1. Vaishvi applies them from a fresh `main` in her own terminal, with `db push` as above. The dry run should list
   these two migrations only, `add_user_tables` first. She tells Om.
2. Om checks it read-only through the Supabase connector:
   - the migration history lists `20261115141557`;
   - `public.requests` and `public.searches` have a nullable `user_id` with its foreign key to `auth.users` and its
     index, and `public.profiles` exists with row level security on;
   - `pg_policies` lists exactly the four policies in "Accounts", each for `authenticated` only;
   - `anon` has no privileges on the three tables, and `authenticated` has `select` only, plus `update` of
     `profiles.display_name`;
   - the `auth` schema is Supabase's own: `auth.users` has all its usual columns, so the stand-in was not made;
   - the migration history lists `20261118104917`, `pg_trigger` shows `requests_user_id_fixed` and
     `searches_user_id_fixed` (`before update of user_id`), and `has_function_privilege` is false for `anon` and
     `authenticated` on `public.keep_user_id_fixed()`.
3. The Security Advisor should show **RLS Enabled No Policy** for `public.product_aliases` only. Anything new, such as a
   warning about the policies, goes to Vaishvi before S4-10 merges.

### `create_preferences` (by-hand job 5: after it merges, once, by hand)

The migration `20261121042008_create_preferences` (US-06, #61) is only a file until someone applies it. It needs
`add_user_tables` and `keep_owner_fixed` applied first, for `auth.users` and `public.keep_user_id_fixed()`. It also
needs the `vector` extension, which the Supabase project lists as available (0.8.2, read 21 Nov 2026). Nothing in the
backend needs the table yet: US-06's memory store and US-11's routes do.

1. Vaishvi applies it from a fresh `main`, in her own terminal, from the repository root, with the backend's virtual
   environment active. First the dry run: it should list `20261121042008_create_preferences` only. If it lists more,
   stop and tell Om.

   Git Bash:

   ```bash
   git switch main && git pull --ff-only
   export DATABASE_URL="$(python -c 'from dotenv import dotenv_values; print(dotenv_values(".env")["DATABASE_URL"])')"
   npx supabase@2.120.0 db push --db-url "$DATABASE_URL" --dry-run
   npx supabase@2.120.0 db push --db-url "$DATABASE_URL"
   unset DATABASE_URL
   ```

   PowerShell:

   ```powershell
   git switch main; git pull --ff-only
   $env:DATABASE_URL = python -c "from dotenv import dotenv_values; print(dotenv_values('.env')['DATABASE_URL'])"
   npx supabase@2.120.0 db push --db-url "$env:DATABASE_URL" --dry-run
   npx supabase@2.120.0 db push --db-url "$env:DATABASE_URL"
   Remove-Item Env:DATABASE_URL
   ```

   She tells Om.
2. Om checks it read-only through the Supabase connector:
   - the migration history lists `20261121042008`;
   - `pg_extension` shows `vector` 0.8.2 in the schema `extensions`;
   - `public.preferences` has the columns in "Preferences (US-06)", with `embedding` as `extensions.vector(384)`, and
     row level security is on;
   - `pg_policies` lists only `preferences_select_own` on it, for `authenticated`;
   - `anon` has no privilege on it, and `authenticated` has `select` only;
   - `pg_trigger` shows `preferences_user_id_fixed` (`before update of user_id`);
   - `pg_indexes` shows `preferences_pkey` and `preferences_user_id_category_idx`, and no vector index.
3. The Security Advisor should show nothing new: the table has a policy, and pgvector isn't in `public`. Anything new
   goes to Vaishvi before US-06's store merges.

**Rolling it back.** Do this only if the team decides to: it deletes every remembered item, with no undo. Migrations
here move forward only, so the down SQL is in `supabase/rollback/`, and `db push` never reads it. Run it, then mark the
migration reverted in the history, from the root with the backend's virtual environment active (Git Bash):

```bash
export DATABASE_URL="$(python -c 'from dotenv import dotenv_values; print(dotenv_values(".env")["DATABASE_URL"])')"
python -c "import os, pathlib, psycopg; down = pathlib.Path('supabase/rollback/20261121042008_preferences_down.sql'); c = psycopg.connect(os.environ['DATABASE_URL']); c.execute(down.read_text(encoding='utf-8').encode()); c.commit(); c.close()"
npx supabase@2.120.0 migration repair 20261121042008 --status reverted --db-url "$DATABASE_URL"
unset DATABASE_URL
```

The down SQL drops `public.preferences`, with its index, policy and trigger. It puts back the function's earlier
comment, and drops pgvector unless something else uses it. The `extensions` schema stays. It is safe to run more than
once. If pgvector was switched on in the dashboard for something else before job 5, and nothing uses it yet, switch it
on again afterwards.

The migration file is still on `main` after this, so the next `db push` would make the table again. The same pull
request that decides the rollback also decides what happens to the file: remove it, or replace it with a new
migration. Never leave the history and the files out of step.

## "off" and "down"

`GET /api/health` answers `"database": "ok"`, `"off"` or `"down"`. Nothing else about the database is shown.

| | Meaning | What happens |
|---|---|---|
| `ok` | The database answered | Requests are saved |
| `off` | No `DATABASE_URL` is set | Everything works; nothing is saved |
| `down` | It is set, but the database did not answer in time (wrong password, network, paused project) | Searches still finish; the request is not saved |

Each request save waits at most `DB_TIMEOUT_SECONDS`. After 3 failures in a row the backend stops trying for 30 seconds
(saves fail at once, health says `down`), then tries once more. The session remembers the outcome in
`request_saved`: `saved`, `off` or `failed`. The CLI prints one line, and `--no-db` skips the database.

Logs show only the database host and the error type, never the URL, the password or the request text.

### Saving a finished search

`save_search(session_id, rubric, result)` is on every request store, next to `save_request`, and shares its circuit
breaker. It has a time limit of its own (P-38, US-52), `DB_SAVE_TIMEOUT_SECONDS`, 15 seconds by default and 2 to 15
allowed; every other call keeps `DB_TIMEOUT_SECONDS`. Why: on 20 Nov S4-11's live check, a signed-in search finished but
its save timed out at 2 seconds from Om's network, while every other database call worked. A finished search's row is
the largest one we write. So:

- The save runs after the result is complete and outside the search's 150 seconds, so a slow save never cuts the search
  short: the stages have the whole 150. The run waits for it at most its limit and one more second, so a search takes
  at most 150 + 15 + 1 seconds. That is why 15 is also the most allowed: the frontend waits 170 seconds for research,
  so it always covers the search, the save and a margin. A higher value is refused with a warning, and 15 is used.
- The Reddit cache allows for the save too: it serves an answer for 24 hours less the longest search, the save (its
  limit and the 1-second grace), the keep-time, one housekeeping tick and the 60-second margin (`serve_limit()`, D-53),
  so the margin stays 60 seconds at any allowed save limit. By default that is 23 hours 16 minutes 14 seconds.
- While it saves, the progress stream says so once: "Saving your results.", as `done` on the line of the last stage that
  ran OK (the contract has no save stage; a stage that fell back, failed or was skipped keeps its own words). It is not
  said when nothing will be written: with the database off, or while the breaker skips it.
- Everything counts against that limit: building the row, waiting for a connection, the statement
  (`set local statement_timeout` for its one transaction) and the commit. The work must end a tenth of the limit early
  (at most 1 second), so the commit lands inside it; past that the row is rolled back.
- Saves have their own two workers and their own pool of two connections, whose connect timeout and
  `statement_timeout` are the save's limit. Slow saves never hold a connection the other calls need.
- A save that times out counts once against the breaker, like any other failure. An open or half-open breaker answers
  `False` at once: a save never takes the one trial call, which a quick call (a request save, the health check) takes
  instead. A save that only waited behind other saves, and never reached the database, is not counted.
- The run reports `saved` true or false as before. `rubric` and `result` are `model_dump(mode="json")` output. A plain
`model_dump()` holds dates and URLs, which are not JSON, so it is refused. `DatabaseResultStore` in
`backend/app/pipeline.py` calls it at the end of every search, from both the API and the CLI.

It answers `True` only when the row was committed. It never raises, and it answers `False` with one log line when:

- the database is off (this line is at info level, because it is a normal way to run);
- the database is down, or slower than `DB_SAVE_TIMEOUT_SECONDS`;
- the circuit breaker is open, or the store is closed;
- the insert fails;
- the search cannot fit the table: a bad session ID, something that is not JSON, a NaN or infinite number, or the NUL
  character, which `jsonb` cannot hold. These are refused before the database is touched, so one bad result never
  counts towards the circuit breaker.

An insert that finishes after the time limit is rolled back, so a late row does not appear after the answer `False`.
`False` still means "not confirmed" rather than "certainly not there": if the limit passes or the connection drops
during the commit itself, the row can exist. Nothing retries, so this can never make a second row.

## The Free plan pauses idle projects

A Free project is paused after 7 days of low activity. A paused project shows as `down`, and nothing is saved.

- Resume it from the Supabase dashboard (the project page has a **Restore** button). It takes a few minutes.
- **Open the dashboard the day before every review**, so a pause never surprises a demo.

## Changing the password

1. In the dashboard: **Settings > Database > Reset database password**. Use a new strong password from the password
   manager.
2. Update `DATABASE_URL` in the root `.env` on every machine that has it, and in any CI secret that holds it.
3. Restart the backend. Until then it shows `down` (the old password no longer works).

## Tests

- **Unit and drill tests** need no database and run everywhere. A plain `pytest` leaves out the drills that really wait
  for a timeout: run those with `pytest -m slow`. CI runs them all. The drills point the backend at a closed local port
  and at a server that never answers, and check that searches still finish and health says `down`.
- **Integration tests** (marker `db`) need a throwaway Postgres 17 with pgvector (`pgvector/pgvector:0.8.2-pg17`). They
  skip without `TEST_DATABASE_URL`, or on a server without pgvector. With `REQUIRE_DB_TESTS=1` either fails them
  instead. They never run against Supabase: each run creates its
  own database, applies every migration, checks the tables, row level security and the limits, and drops it. The
  `backend` job in CI runs them on every pull request against `pgvector/pgvector:0.8.2-pg17`, with
  `REQUIRE_DB_TESTS=1`.
- A plain Postgres gives `anon` and `authenticated` nothing, so checking their privileges there proves little. One
  test each in `test_searches_table.py`, `test_user_tables.py` and `test_preferences_table.py` copies Supabase's
  default grants first, so a lost `revoke` makes it fail.
- `test_user_tables.py` checks US-30's migration. Each check acts as the Data API does: it sets the role
  (`authenticated` or `anon`) and the user's claims, in a transaction that is rolled back.
  - user A reads only A's requests, searches and profile, and user B can't read A's;
  - `anon` reads nothing, even carrying a user's claims;
  - a guest's rows are invisible to every signed-in user, and a session with no user reads nothing;
  - a user renames only their own profile, can't change its id, and can't insert, update or delete a request or
    search;
  - deleting a user deletes their rows, and only theirs;
  - the columns, foreign keys, indexes, privileges and the four policies, exactly;
  - running the migration again changes nothing, and on a database that already has Supabase's `auth` schema it leaves
    `auth.users` and `auth.uid()` as they were.
- `test_owner_fixed.py` checks that an owner never changes (S4-27):
  - a guest's row can't be given a `user_id`, and a user's row can't move to another user or back to empty;
  - other columns still update, setting the same owner again is no change, a row is still saved with its owner,
    and deleting a user still deletes their rows;
  - the triggers' shape, the function's fixed `search_path`, and no `execute` for `public`, `anon` and
    `authenticated`, also after copying Supabase's default grants on new functions;
  - running the migration again changes nothing, and it runs with no `anon` or `authenticated` role.
- `test_preferences_table.py` checks US-06's table (#61):
  - user A can't select user B's preferences, and reads their own, embeddings included;
  - `anon` reads nothing, even carrying a user's claims, and a session with no user reads nothing;
  - a user can't insert, change or delete a preference through the Data API;
  - changing `user_id` is refused, other columns still change, and deleting a user deletes their rows, and only
    theirs;
  - an embedding of 383 or 385 numbers is refused; so are empty text, text over 200 characters or with a space at
    either end, a weight of 11, NaN or infinity, an unknown kind, a kind with another kind's fields or rule, a
    category or criterion that isn't a slug, and a category or Always that breaks the Always rule;
  - the contract's example items are saved and read back as the same `PreferenceList`;
  - pgvector 0.8.2 is in `extensions`, plus the columns, keys, indexes, privileges and the one policy, exactly;
  - running the migration again changes nothing. It also runs after Supabase's default grants and with no Supabase
    roles;
  - after down, the database matches one that never had the migration, and after up again it matches the first up.
    Down keeps pgvector while another table uses it.
- `test_product_aliases_table.py` checks the aliases table:
  - its columns and primary key;
  - row level security with no policies, and no access for `anon` and `authenticated` (on a plain Postgres these roles
    start with nothing, so only the live check proves the revoke on Supabase);
  - the values `alias`, `product_id` and `category` refuse;
  - the Postgres alias store's round trip, where saving again keeps the first id and moves `last_seen` on;
  - that the migration also runs on a plain Postgres with no Supabase roles.

  The cases that need no database are in `tests/unit/db/test_aliases.py`: the database down, the breaker open, the
  table missing, and a search never waiting.

You need Docker. Run this from the repository root, with the backend's virtual environment active. The password is
made up for this one run, and the database is open only to your own machine.

```bash
PGPASS=$(python -c "import secrets; print(secrets.token_hex(16))")
docker run -d --name shopsense-pg17 -e POSTGRES_PASSWORD="$PGPASS" -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.2-pg17
until docker exec shopsense-pg17 pg_isready -q -h 127.0.0.1 -U postgres; do sleep 1; done   # wait until it is ready
cd backend
TEST_DATABASE_URL="postgresql://postgres:$PGPASS@127.0.0.1:55432/postgres" pytest -m db -rs
cd ..
docker rm -f shopsense-pg17
unset PGPASS
```

## Live check

Run against the team's Supabase project on **3 Nov 2026** (US-01). No URL, host or password is recorded here.

| Check | Result |
|---|---|
| Postgres version | **17.11** (`show server_version`); `major_version = 17` in `supabase/config.toml` already matched |
| Migration | `npx supabase@2.120.0 db push`: "Remote database is up to date". `20261026170303 create_requests` is listed in `supabase_migrations.schema_migrations` |
| Table | `public.requests` exists |
| Row level security | `relrowsecurity` is true |
| Data API roles | `has_table_privilege('anon', 'public.requests', 'select')` and `has_table_privilege('authenticated', 'public.requests', 'insert')` are both false |
| Health | `GET /api/health` answered `"database": "ok"` |
| Saving | One made-up request through the CLI and one through `POST /api/presearch/intake`: exactly 2 rows for their session IDs, both with `intent_source` `llm`. They stayed in the table until the follow-up below deleted them |
| Down drill (wrong password) | Health answered `"database": "down"`. The intake request still answered (HTTP 200 in 3.5 s) and a full CLI search finished (exit 0, about 13 s) with "Saved: no, the database could not be reached. The search goes on." Nothing was saved. The logs show only the host and the error type (`PoolTimeout`, `TimeoutError`, then "skipping the database for 30 s"): no traceback, URL or password |

**Security Advisor** (Advisors > Security, read in the dashboard the same day):

- `public.requests`: **RLS Enabled No Policy**. Row level security is on and has no policies on purpose, and the
  table is revoked from `anon` and `authenticated`, so the Data API can neither read nor write it (the checks
  above). This is the intended design, and the setup step above expects it. The team decides whether to keep it
  as is or add an explicit deny-all policy in a new migration.
- `public.rls_auto_enable()`: **Public Can Execute SECURITY DEFINER Function** and **Signed-In Users Can Execute
  SECURITY DEFINER Function**. Supabase added this function when "automatic RLS" was chosen at project creation.
  Its execute rights are revoked by its own migration ("US-01: lock down the automatic RLS helper function").

### Follow-up, 4 Nov 2026 (W1b)

- **Helper locked:** migration `20261103102906_lock_rls_auto_enable` revoked execute on `public.rls_auto_enable()` from
  `public`, `anon`, `authenticated` and `service_role`. `has_function_privilege('anon', 'public.rls_auto_enable()',
  'execute')` is false.
- **Test rows deleted:** the table held exactly five rows, all made-up test requests. They were the two from the
  check above and three from the full-stack live test and the Sprint 1 review check (3 Nov 17:56 and 17:57 UTC,
  4 Nov 15:47 UTC). Only `session_id` and `created_at` were read. They were deleted by their five session IDs, and
  `select count(*) from public.requests` now returns **0**.
- **Security Advisor**, read the same day: one finding, **RLS Enabled No Policy** on `public.requests` (INFO). The two
  `rls_auto_enable()` warnings are gone. That remaining finding is the intended design: row level security is on,
  there are no policies, and `anon` and `authenticated` have no access, so only the backend (through
  `DATABASE_URL`) can read or write the table.
- **Every later live test** run with the database on adds one row for its session. Delete such rows by session ID
  the same way (read only `session_id` and `created_at` first).

### Follow-up, 11 Nov 2026 (US-15)

After `20261111083610_create_product_aliases` was applied, read-only checks on the team's project:

| Check | Result |
|---|---|
| Migration history | Four migrations; the newest is `20261111083610 create_product_aliases` |
| Table | `public.product_aliases` exists, with `alias`, `product_id`, `category`, `first_seen` and `last_seen`, and `PRIMARY KEY (category, alias)` |
| Row level security | `relrowsecurity` is true, with 0 policies |
| Data API roles | `has_table_privilege` is false for `anon` and `authenticated`, for both `select` and `insert` |
| Rows | 0: the first appear after a search with the database on merges two names of one product |

- **Security Advisor**, read the same day: **RLS Enabled No Policy** (INFO) on `public.product_aliases`,
  `public.requests` and `public.searches`, and nothing else. For all three it is the intended design: row level
  security is on, there are no policies, and `anon` and `authenticated` have no access, so only the backend (through
  `DATABASE_URL`) can read or write them.
