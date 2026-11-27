# Repeat cache and speed (US-34, S5-7)

## The repeat cache

A search repeated within its limit answers from `backend/.cache/results/` (`backend/app/cache/results.py`, D-80).

- **Reused only when everything matches.** The key is:
  - the request's words, normalised (only case and runs of spaces don't count);
  - the category;
  - a hash of the whole rubric: every weight with its share, origin and uncertainty, the hard rules, the exclusions,
    the budget, the region and the use.

  For a signed-in shopper, a hash of their user id and of their stored preferences is added. So a changed weight,
  budget or rule misses, and so does another user. A guest never gets a signed-in shopper's result, nor the reverse.
- **What is kept:** the finished result as a saved search keeps it, with no Reddit text (`without_reddit_text`, D-53).
  The user id and the session are left out too; the user is known only by a hash of their id. Only a full, real
  result is kept: every stage ran as itself or had nothing to do, so never a partial or degraded one (a stage that fell
  back, timed out or failed), one where no model answered, sample data, or a hit.
- **For how long:** until `serve_limit()` from when its search finished, about 23 hours, not a full day (D-53). It is
  checked on every read, and the app's housekeeping sweeps the folder every ten minutes. A broken entry is deleted and
  the search runs.
- **A hit:**
  - It is a new search: a new id, the current request's session and owner, and `cached` true with `cached_at`.
  - It is saved as any search is. Its sources open from the citations it kept, as a saved search's do.
  - The results header says "From a search made at <time>".
  - Through the API it ends within 3 seconds, checked on 10 repeats by
    `backend/tests/unit/test_pipeline_result_cache.py`.
- **The switch:** `RESULT_CACHE=on|off` (default on). Off, every search runs.
- **Deleting an account** deletes every result kept for that user as well.
- **Never in the way:** any fault reading, serving or keeping an entry runs the search as usual (build rule 9).

## The speed run

`python -m evals.speed` (from `backend/`) runs briefs b01 to b20 of `backend/evals/briefs/twenty.yaml`, written by
people, through the app's own API. It adds each brief to `backend/evals/results/speed.json`.

- **What it records:**
  - each interview turn's time;
  - each stage's time;
  - the search's time from the post to its end;
  - one repeat's time, and whether it came from the cache.

  Times, statuses, stage names and brief ids only.
- **The interview:** a seeded shopper answers each question with a rough level, so a brief always gets the same
  answers.
- **The database:** off for the run, so nothing is saved and no session is created.
- **The budget:** before each brief it checks today's Serper count against the day cap. With less than 12 left it
  stops for the day, and the next run carries on (By-hand job 7). `usage.json` is only read.
- **The report** (`python -m evals.speed --report`, no call):
  - the share of turns within 5 seconds;
  - the share of searches within 120 seconds, counting only searches that ran. A refused or lost run, or a first
    search answered from the cache (a brief run again within the limit), is listed apart, never counted;
  - the slowest stage by median;
  - every search past 150 seconds (there must be none);
  - the repeats within 3 seconds.
