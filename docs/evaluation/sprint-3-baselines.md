# Sprint 3 baselines (S3-18, task #45)

This report records what ShopSense measures at the end of Sprint 3, and the floor set for each measure. The floors
are in [`backend/evals/thresholds.yaml`](../../backend/evals/thresholds.yaml). US-49's quality gate will enforce them in
Sprint 4.

- **Date and code:** measured on 14 Nov 2026, on main `a6148f0`.
- **Owner:** Harsh Kodavala.
- **Machine labels:** every "own pairs" number comes from machine labels (ml/DATA.md 4.1), not people.

To run it again, from `backend/`:

```bash
python -m evals.run_baselines        # offline: writes evals/results/sprint-3-offline.json (no network)
python -m evals.live_briefs          # live, by hand, inside the budget: writes evals/results/sprint-3-live.json
python -m evals.thresholds --check   # every measured value against its floor
```

## The findings

1. **The safety measures hold where they can be checked.**
   - **Offline (the five briefs):** no shown product broke a hard rule, and the most-mentioned product never won while
     it fit worst. Both are checked by `evals/checks.py`'s own reading of the rubric and values, not by decide's code.
   - **Live:** no shown product broke a rule, by decide's own filter.
   - **Not yet measured live:** whether a shown product lacks a source, and the popularity check. Their independent
     checks were written after this run (section 5), and the record didn't keep the data to apply them afterwards. The
     next live run measures both.
2. **The demo risk from S3-10b is real.** Across the five live searches, **no shown product got a score.**
   - **Air fryer:** the only brief that showed anything. It showed 4 products, all "not ranked", with 0% coverage.
   - **Smartphone:** it searched fully, but its sources gave only 7 claims, all from Reddit, and nothing passed the
     rules.
   - **The other three briefs** ran after the app's own daily Serper limit was spent (section 5), so they found little
     or nothing.
3. **The trained matcher's target is not shown.** Its precision on our own pairs is 1.0, but that comes from one
   called pair, against a target of 0.95. Fuzzy stays the default.
4. **Learned trust beats majority vote**, 3 to 0 of 5 seeded conflicts, but it is no better than the fixed tier
   order (3 of 5). Five conflicts is a small sample; the plan asks for 30.

## 1. Matching

**The fuzzy baseline and the trained matcher, on their test parts**, from
[`ml/results/matching_baseline.json`](../../ml/results/matching_baseline.json) and
[`ml/results/matcher_v1.json`](../../ml/results/matcher_v1.json). Intervals are 95% bootstrap.

| Test set | Method | Precision | Recall | F1 |
|---|---|---|---|---|
| Our own pairs (machine labels), 57 pairs | Fuzzy, threshold 1.0 | 0.667 [0.0, 1.0] | 0.286 [0.0, 0.667] | 0.400 [0.0, 0.727] |
| Our own pairs (machine labels) | Matcher, threshold 0.9646 | 1.000 [1.0, 1.0], from 1 pair | 0.143 [0.0, 0.5] | 0.250 [0.0, 0.667] |
| WDC 80cc20 | Fuzzy 0.79 | 0.263 | 0.328 | 0.292 |
| WDC 80cc20 | Matcher | 0.529 [0.286, 0.800] | 0.018 | 0.035 |
| WDC 50cc50 | Fuzzy 0.79 | 0.325 | 0.400 | 0.359 |
| WDC 50cc50 | Matcher | 0.818 [0.643, 0.958] | 0.036 | 0.069 |
| WDC 20cc80 | Fuzzy 0.78 | 0.377 | 0.482 | 0.423 |
| WDC 20cc80 | Matcher | 1.000 [1.0, 1.0] | 0.052 | 0.099 |

**The match stage on our labelled pairs.** This is the app's default stage: rules first, then the fuzzy scorer.
Each pair is run on its own. Source: `evals/pairs.py`, with labels from `ml/labelling`.

| Kind of pair (machine labels) | Pairs | Merged by the stage | Share | What should happen |
|---|---|---|---|---|
| same | 17 | 7 | 0.412 | merge |
| variant | 23 | 8 | 0.348 | merge, as one product with variants (DR-02) |
| different | 106 | 0 | 0.000 | never merge |

How the pairs were chosen:
- **Every pair whose labels agree,** not only S3-5's test part, because that split is ml code, which the backend
  never imports.
- **Dropped: 49 with an "unsure" label, and 80 with a store title longer than 150 characters.** Such a title can
  never be a product mention in the app.
- **The model stage was not rerun on these pairs,** because its threshold was chosen on data that includes them.

## 2. Trust

Source: `backend/scripts/eval_trust.py` on [`ml/labelling/conflicts.csv`](../../ml/labelling/conflicts.csv), the app's own
combiner.

| Method | Right, of 5 conflicts | Right, of the 3 where the answer's page is not a claim |
|---|---|---|
| Learned | 3 | 1 |
| Tier order | 3 | 1 |
| Majority vote | 0 (3 with no answer) | 0 |

## 3. Decide: the five made-up briefs

Source: `backend/tests/fixtures/decide/`, run through `app.decide`.

| Brief | Shown | Held back | Coverage of shown products | As the hand labels expect |
|---|---|---|---|---|
| e-reader under ₹8,000 | 0 | 5 | none shown (a relaxation hint is expected) | yes |
| headphones, ANC, 5k to 15k | 3 | 4 | 1.00, 0.74, 1.00 | yes |
| laptop for coding under 70k | 3 | 6 | 1.00, 0.90, 1.00 | yes |
| 27-inch gaming monitor | 3 | 4 | 1.00, 1.00, 1.00 | yes |
| phone camera under 30k | 2 | 5 | 1.00, 1.00 | yes |

| Measure | Value |
|---|---|
| Rule breaks among shown products | 0 |
| Briefs where the most-mentioned product won while fitting worst | 0 (the laptop brief tests exactly this) |
| Coverage of ranked products | mean 0.967, lowest 0.739 |

## 4. Research and extraction, offline

The recorded runs in `backend/tests/fixtures/recorded/` hold one query's intake, criteria and web searches. They hold
no extraction and no page reads.
- **What they show:** reading the 6 recorded searches as the app reads them gives **55 sources, all search snippets**,
  plus 4 Reddit links.
- **What they can't show:** claims per source, the share of quotes found, and pages read by each reader. The offline
  record marks these `not_recorded` instead of guessing. They are measured live below.

## 5. Live: the full flow on five briefs

**How it ran** (`backend/evals/live_briefs.py`, record in `backend/evals/results/sprint-3-live.json`, no database):
- **The briefs:** one made-up request for each of the five categories with the most data in `listings.csv`.
- **Settings:** `EXTRACT_MAX_SOURCES=8` and `MATCHER=fuzzy`.
- **Calls used:** LLM 30 of 60, Serper 8 of 30, Reddit archive 7 of 30, Jina 2 of 30, Firecrawl 0 of 10.
- **No Supabase session was created.**

| Brief | Sources | Claims kept | Shown | With a score | Can't tell | Search |
|---|---|---|---|---|---|---|
| smartphone | 49 (20 Reddit, 20 snippets, 7 review, 1 maker, 1 store) | 7, all Reddit | 0 | 0 | 13 | 27.1 s |
| air fryer | 28 (19 snippets, 7 review, 2 store) | 8, all review sites | 4 | 0 | 36 | 30.6 s |
| laptop | 3 (Reddit) | 0 | 0 | 0 | 0 | 1.2 s |
| smartwatch | 0 | 0 | 0 | 0 | 0 | 0.3 s |
| earbuds | 19 (Reddit) | 0 | 0 | 0 | 6 | 13.9 s |

- **Methods:** match fuzzy, trust learned, decide weighted, in every brief.
- **Fallbacks:** the run's console showed the criteria stage timing out once (smartphone) and using its fallback. The
  record doesn't hold it: this run kept one time for the whole pre-search. Later runs keep the intake, criteria and
  interview times apart.
- **The longest stages:** research 23.9 s (limit 40), extract 14.7 s, match 0.02 s, verify 0.06 s.
- **Quotes:** 15 of the 16 claims the parser checked had their quote in the source (0.938). Kept claims are counted
  after duplicates go, and a quote found but refused as an instruction counts as not found. Both nudge the share a
  little.
- **Claims per source given to extract:** 15 from 99 (0.15). Search snippets and sources past the 8-source cap count
  too, though extract passes them over. By type:

  | Type | Claims | Sources | Per source |
  |---|---|---|---|
  | Reddit | 7 | 42 | 0.17 |
  | review site | 8 | 14 | 0.57 |
  | store | 0 | 3 | 0 |
  | maker | 0 | 1 | 0 |

- **Two safety measures this record can't give:**
  - **What was checked:** this run counted unsourced shown products as those verify had dropped. The pipeline removes
    those before decide, so that count is always 0.
  - **What was missing:** the popularity check used decide's score, which sets the rank itself, so it couldn't fire.
  - **The fix:** both now have independent checks: a source naming the product, and a fit from the values' own
    scores.
  - **In the record:** applying them needs data this run didn't keep, so both are `not_recorded` and have no floor
    yet.

**Why three briefs found so little.** The research stage keeps its own daily limits in
`backend/.cache/research/usage.json`, and that file counts every run on this laptop that day. By the end of this run
it read **Serper 60 of its 60 a day** and **Firecrawl 30 of 30**. Earlier live runs the same day (S3-15, S3-16) and the
first two briefs had used them up. So from the laptop brief on, research had no web search. It worked as designed: the
limit protects Serper's one-time 2,500 free queries.

**The live floors are therefore provisional.** Rerunning the laptop, smartwatch and earbuds briefs after 00:00 UTC,
when the limits reset, fits the budget left: LLM 30, Serper 22.

## 6. The floors

`backend/evals/thresholds.yaml` holds **61 floors**, one for each measured value:
- **3 safety floors:** the two offline decide checks, and live rule breaks;
- **58 quality floors.**

24 of the 61 come from the live run, 1 of them a safety floor. `python -m evals.thresholds --check` reports 0
problems. It also reports a measured value that has no floor, so a deleted floor can't pass unnoticed.

How each is set (`backend/evals/thresholds.py`):
- **Safety, exact.** Only these may be safety floors, each at most 0:
  - rule breaks among shown products (offline and live);
  - unsourced shown products (live, from the next run);
  - briefs where the most-mentioned product wins while fitting worst (offline, and live from the next run).
- **Quality: the measured value less a stated margin.**
  - 0.05 for a rate. US-49 fails a drop of more than 10 points, so the floor stays inside that.
  - 10% for a count, rounded down.
  - One conflict for the trust counts. A difference, such as learned less majority vote, is never clamped at 0.
  - None for the briefs' hand labels, which are right or wrong.
  - Times have a ceiling at their stage's own limit, never at a measured time.
- **Targets from the plan sit next to their floors and are never lowered:**
  - **Matcher precision 0.95:** met as a number, but not shown, because it comes from one pair.
  - **Learned trust better than majority vote:** met, by 3, on 5 conflicts.

A live floor (`measured_on: live`) can only be checked after a live run. US-49's replay can't recompute one until the
recorder captures extraction and page reads.

## 7. Gaps

| Gap | Owner |
|---|---|
| **No shown product gets a score in real searches:** too few claims (7 to 8 a search) and none on most criteria. This is the demo risk from S3-10b. | Jhanvi (US-14 extraction), with Om |
| Rerun the live briefs after 00:00 UTC, once the daily limits reset. That gives the laptop, smartwatch and earbuds briefs a web search, and measures unsourced shown products and the popularity check live, adding their two safety floors. Then update the live record and its floors. | Harsh |
| The daily research limits are shared by every run on one machine, so a dev's live checks starve the next run | Jenish (US-12) |
| The matcher's precision target rests on one called pair: more of our own labelled matches are needed | Om |
| Trust is judged on 5 seeded conflicts; the plan asks for 30 | Rushiraj and Om (S3-16) |
| The recordings hold no extraction or page reads, so those measures are live only | Harsh (US-49 recorder) |
| Confirm the targets and margins with the team at the Sprint 3 review | Harsh |
| `evals/intake/run_rules_eval.py` fails `mypy --strict` (a `Money` that may be `None`, line 55). CI doesn't type-check `evals/`. | the intake eval's owner |
