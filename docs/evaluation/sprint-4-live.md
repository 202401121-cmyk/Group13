# Sprint 4 live baselines (S4-2, #45 follow-up)

**What this is:** the five live briefs run once the way the demo runs them, at extraction's normal cap of 40 sources a
search. The live floors in `backend/evals/thresholds.yaml` now come from this run.

**The record:** `backend/evals/results/sprint-4-live.json` ("the record" below). Every number on this page is read from
it, and each table names the JSON path, except two named where they appear: the run's clock times (the tool's own
start and end lines) and the Sprint 3 floors (`sprint-3-live.json` and the previous `thresholds.yaml`). The record holds numbers and fixed labels only: no request, quote, product
name, link or username (D-53).

## The run
| | |
|---|---|
| Command (from `backend/`) | `DATABASE_URL= python -m evals.live_briefs --out evals/results/sprint-4-live.json --categories smartphone laptop earbuds air_fryer smartwatch --max-sources 40 --budget-llm 200 --budget-serper 30 --budget-reddit 50 --budget-jina 30 --budget-firecrawl 10` |
| When | 17 Nov 2026 (`generated`), 08:01 to 08:05 UTC by the run's own start and end lines, after the daily resets (Serper at 00:00 UTC, Gemini at 08:00 UTC) |
| Code | main `d0788bd` (with S4-1, #115); `commit` |
| Source cap | `EXTRACT_MAX_SOURCES=40`, the cap in `config/extract.yaml`; `budget_note` |
| Database | off: an empty `DATABASE_URL` for this process, and results kept in memory. No Supabase session was created |
| Budget, and what was used | LLM 63 of 200, Serper 18 of 30, Reddit archive 45 of 50, Jina 6 of 30, Firecrawl 1 of 10; `calls_used` |

The run's own budget never refused a call: every brief stayed inside its share. The LLM count is requests really sent.
It is lower than the attempts in the funnel below, because a provider with no room is never sent the request.

## The findings
1. **No brief showed a product.**
   - 4 of the 5 finished; the smartphone brief stopped at intake, which asked a clarifying question.
   - Every product the four found was held back as "can't tell", 49 in all, and each one for its price:
     `measures` `live.ranked_products` is 0, and `briefs[*].funnel.held_back.cant_tell_for_price` gives 10, 22, 3, 14.
2. **Extraction asks for more than the providers' per-minute room allows.**
   - 233 of 287 extraction attempts ended `rate_limited`.
   - That outcome covers two cases: a hold by the pool's own per-minute ledger, where nothing is sent, and a provider's
     429 for a request that was sent. The record can't split them. But the requests really sent (`calls.llm`) less
     the other outcomes leave room for at most 4 sent 429s, so nearly all were held locally.
   - 69 of the 110 sources sent to extract got no answer for that reason.
   - That the 40-source cap is what causes it is an inference: no run at a lower cap was made today to compare.
3. **Only Reddit threads gave claims.**
   - 32 claims were kept, all from Reddit.
   - Review sites (29 sources), stores (11) and makers' pages (3) gave none, so no product got a price.
4. **Extract hit its time limit in 2 of the 4 searches** (laptop 29.58 s and air fryer 28.86 s,
   `briefs[i].stages.extract.status` "timeout"). 17 of the 110 sources sent got no answer for time or failure.
5. **Every search stayed well inside the 150-second limit:** the longest took 64.67 s.
6. **Both live safety checks now have data,** and both are 0:
   - unsourced products shown;
   - the most-mentioned product winning while it fits worst.
   Their floors are added, exact 0, beside the hard-rule floor.

## Per brief: the funnel
Each row is read from `briefs[i]`, with `i` in the order run. The columns, by JSON path:
- `search_seconds` (on the brief itself);
- the rest under `funnel`:
- `sources.found`;
- `extract.search_snippet` and `extract.sent`;
- `extract.no_answer_by_reason`;
- `claims.returned`, `claims.kept` and `claims.scorable`;
- `shown.products` and `shown.with_a_score`;
- `held_back.cant_tell` and `held_back.cant_tell_for_price`.

| Brief | Finished | Search (s) | Sources found | Snippets | Sent to extract | No answer: no room / timed out / gave up / failed | Claims returned / kept / scorable | Shown | With a score | Can't tell (for price) |
|---|---|---|---|---|---|---|---|---|---|---|
| smartphone | no: needs a clarification | - | - | - | - | - | - | - | - | - |
| laptop | yes | 53.37 | 52 | 20 | 32 | 14 / 4 / 6 / 2 | 10 / 9 / 1 | 0 | 0 | 10 (10) |
| earbuds | yes | 45.45 | 51 | 20 | 28 | 21 / 2 / 0 / 0 | 20 / 17 / 0 | 0 | 0 | 22 (22) |
| air_fryer | yes | 64.67 | 51 | 20 | 24 | 15 / 2 / 1 / 0 | 1 / 0 / 0 | 0 | 0 | 3 (3) |
| smartwatch | yes | 58.56 | 49 | 20 | 26 | 19 / 0 / 0 / 0 | 15 / 6 / 1 | 0 | 0 | 14 (14) |

Smartphone's row is `briefs[0]`: `finished` false, `why` "needs a clarification". It used 1 LLM call (`calls.llm`).

## Per brief: provider room
Each row is read from `briefs[i].funnel.llm`: `calls` (attempts), `failed`, `by_outcome` and `by_provider`, which counts
attempts by the data class of the text. The last column is `briefs[i].calls`, the requests the run's guard really let through.

| Brief | LLM attempts | Failed | ok | No room (`rate_limited`) | Timed out | Error | Attempts by provider (data class) | Calls sent |
|---|---|---|---|---|---|---|---|---|
| laptop | 114 | 108 | 6 | 82 | 20 | 6 | deepseek public 6; gemini public 8; groq public 30, reddit 48; mistral public 12; openrouter public 10 | llm 33, serper 2, reddit 10, jina 2, firecrawl 0 |
| earbuds | 63 | 58 | 5 | 56 | 2 | 0 | groq public 22, reddit 30; mistral public 11 | llm 9, serper 6, reddit 12, jina 1, firecrawl 0 |
| air_fryer | 48 | 42 | 6 | 40 | 2 | 0 | gemini public 1; groq public 16, reddit 21; mistral public 9; openrouter public 1 | llm 10, serper 5, reddit 11, jina 1, firecrawl 0 |
| smartwatch | 62 | 55 | 7 | 55 | 0 | 0 | groq public 27, reddit 33; mistral public 2 | llm 10, serper 5, reddit 12, jina 2, firecrawl 1 |
| **All four** | **287** | **263** | **24** | **233** | **24** | **6** | | llm 62 (plus 1 for smartphone's intake) |

The funnel splits attempts by provider, but not each provider's outcomes, so "no room" is given per brief, not per
provider.

## What still loses products, and whose it is
| Cause | The numbers (from the record) | Owner |
|---|---|---|
| **Per-minute provider room.** Once a model's per-minute requests or tokens are spent, the pool holds the attempt (nothing sent), and a provider may answer 429; either way the source gets no answer. Whether the 40-source cap is the cause is an inference: there is no run at a lower cap to compare. | 233 of 287 attempts `rate_limited`, at most 4 of them sent; 69 of 110 sources sent got no answer for no room (`funnel.llm.by_outcome`, `funnel.extract.no_answer_by_reason.no_room`) | US-46 provider rotation, Jhanvi Barot; with US-14 extraction, Jhanvi Barot (how fast 40 sources are sent) |
| **No price, so every product is held back.** With a budget in the request, a product with no price is "can't tell", never shown. | 49 of 49 held back for price; 0 price claims returned (`funnel.held_back.cant_tell_for_price`, `funnel.price.returned`) | US-14, Jhanvi Barot (the price is read from public pages: S4-1's price-first); US-17/US-18, Vaishvi Mehta (the hold-back rule); US-24 commerce, Jenish Kavad (it runs only for ranked products, so it was skipped) |
| **Public pages give no claims.** The record doesn't split "no room" by source type, so the reasons are mixed: no room, the runner giving up for time, and answers with no claims in them. | 0 claims from 29 review-site, 11 store and 3 maker sources; 32 from 80 Reddit threads (`measures` `live.claims_per_source.*`). All 7 sources given up were public pages (`funnel.price.by_source`). Air fryer's 6 answered sources returned 1 claim (`funnel.llm.by_outcome.ok`, `funnel.claims.returned`) | US-14 and US-46, Jhanvi Barot |
| **Extract's time limit.** The stage hit its limit in 2 of the 4 searches, so sources still waiting got no answer. | Laptop 29.58 s and air fryer 28.86 s with status "timeout" (`stages.extract`); 17 of 110 sources sent got no answer for time or failure: 7 given up, 8 timed out, 2 failed (`funnel.extract.no_answer_by_reason`); 24 attempts timed out and 6 errored (`funnel.llm.by_outcome`) | US-14, Jhanvi Barot (extraction's time budget) |
| **The smartphone brief asked a clarifying question,** so no search ran. | `briefs[0].why`: "needs a clarification" | US-02 detection, Jhanvi Barot; US-01 the clarification flow, Yashkumar Patel |
| **Search snippets are never read,** by design: a snippet is a link and a title. | 80 of 203 sources found (`funnel.extract.search_snippet`) | US-12 research, Jenish Kavad, if more pages should be fetched |
| **Reddit threads past extraction's Reddit reading limit.** Not the run's Reddit budget: that was never reached (45 of 50 used). Extract stops reading threads once a search has booked its Reddit tokens (`reddit_tokens_per_search`, 18,000 in `config/models.yaml`). | 13 sources (`funnel.extract.over_reddit_budget`) | US-14 and US-46, Jhanvi Barot |
| **Few claims carry a score.** | 2 of 32 kept claims are scorable (`funnel.claims.scorable`) | US-14, Jhanvi Barot |

## The floors
`python -m evals.thresholds --write` rebuilt the file from the results records. The script reads this record now:
`LIVE_OUT` in `evals/thresholds.py`. S3-18's margins are unchanged:
- 10% below a count, rounded down;
- 0.05 below a rate;
- a ceiling at each stage's own time limit;
- exact 0 for safety.

The offline and simulator floors came out identical, and no target moved. 62 floors became 63, and 3 safety floors
became 5.

| Live floor | Sprint 3 (provisional), from the previous `thresholds.yaml`: floor (measured) | Sprint 4, from the record: floor (measured) |
|---|---|---|
| `live.briefs_finished` | 4 (5) | 3 (4) |
| `live.briefs_with_a_score` | 0 (0) | 0 (0) |
| `live.ranked_products` | 3 (4) | 0 (0) |
| `live.sources_per_brief` | 17.82 (19.8) | 45.67 (50.75) |
| `live.claims_per_source_given` | 0.13 (0.1515) | 0.14 (0.1576) |
| `live.claims_per_source.reddit` | 0.15 (0.1667) | 0.36 (0.4) |
| `live.claims_per_source.review_site` | 0.51 (0.5714) | 0 (0) |
| `live.claims_per_source.retailer` | 0 (0) | 0 (0) |
| `live.claims_per_source.manufacturer` | 0 (0) | 0 (0) |
| `live.quote_found_share` | 0.8875 (0.9375) | 0.8419 (0.8919) |
| `live.pages_direct` | 15 (17) | 35 (39) |
| `live.pages_jina` | 0 (1) | 3 (4) |
| `live.pages_firecrawl` | 0 (0) | 0 (0) |
| `live.search_seconds_max` | at most 150 (30.62) | at most 150 (64.67) |
| `live.stage_seconds_max.*` | at most each stage's limit | at most each stage's limit (the longest: research 36.11, extract 29.58) |
| `live.hard_rule_violations_shown` (safety) | exact 0 (0) | exact 0 (0) |
| `live.unsourced_shown` (safety) | not recorded | **exact 0 (0), new** |
| `live.most_mentioned_worst_fit_wins` (safety) | not recorded | **exact 0 (0), new** |
| `live.coverage_mean` | 0 (0) | **no floor:** no product was ranked, so there is no coverage to measure (`measures`: `missing`) |

**Floors that went down** (they follow this run, as DECIDED 3 says):
- ranked products 3 → 0;
- briefs finished 4 → 3;
- review-site claims per source 0.51 → 0;
- quote-found share 0.8875 → 0.8419.

**Floors that went up:**
- sources per brief 17.82 → 45.67;
- Reddit claims per source 0.15 → 0.36;
- pages read 15 → 35 direct and 0 → 3 by Jina.

A floor of 0 for ranked products means the gate can't yet catch a search that ranks nothing. That gate comes back
once a run ranks products.

## Gaps, with owners
- **A live search ranks nothing at the demo's settings.** The first two causes above are the demo risk.
  - Provider room: Jhanvi Barot.
  - The price hold-back: Jhanvi Barot and Vaishvi Mehta.
- **`live.coverage_mean` has no floor** until a run ranks products. Owner: Harshkumar Kodavala.
  - **The next run:** `python -m evals.live_briefs --out evals/results/sprint-4-live.json …`, then `--write`.
  - **Name the `--out` path:** the tool's default is still the Sprint 3 record, while `evals/thresholds.py` reads the
    Sprint 4 one.
- **The funnel doesn't split each provider's outcomes,** only its attempts. Owner: Harshkumar Kodavala (a later
  `live_briefs.py` change).
