# Sprint 3 review check

- **Checked by:** Palak Patel (QA), task #46, on 15 November 2026, 09:55 to 10:45 UTC.
- **Commit:** `main` at **`ce8090d`** (Merge pull request #112). CI run 43699440887 passed all 5 jobs on it
  ([c3-ci-on-main.txt](sprint-3-check/c3-ci-on-main.txt)).
- **Scope:**
  - the Sprint 3 stories US-09, US-10, US-15, US-16, US-40, US-17, US-18, US-21 and US-07;
  - US-38's page criteria (3, 4 and the page part of 5);
  - the US-46 additions in S3-1 (PR #91);
  - B-2 from Sprint 2 (moved here by D-61);
  - CI on `main`;
  - the live flow, with the database on and off;
  - the Sprint 3 drills;
  - staging (S3-17).
- **Nothing changed:** no application code, test, configuration, workflow, contract or migration was touched. This
  report and its evidence folder, [sprint-3-check/](sprint-3-check/), are the only new files.
- **Machine:** Windows 11, Python 3.11.15 and 3.13.13 in fresh virtual environments, Node 22.18, Docker with
  `postgres:17` (17.11).

Each result is PASS, FAIL, BLOCKED or "Not met yet, as it stands", and names the evidence file that holds the command
and its output. No check was re-run to get a pass. The database tests were started twice, because the first attempt
reached another server (section 2).

## 1. Summary

**Verdict: Not yet: 5 failures (4 FAIL, 1 BLOCKED).**

| Check | PASS | FAIL | BLOCKED | Not met yet, as it stands |
|---|---|---|---|---|
| C1 Acceptance criteria (53 rows) | 47 | 2 | 0 | 4 |
| C2 B-2, core logic offline | 1 | 0 | 0 | 0 |
| C3 CI and regression (8 rows; the fast-suite row is F-1, below) | 7 | 0 | 0 | 0 |
| C4 The live flow, database on and off | 4 | 2 | 0 | 0 |
| C5 Drills | 2 | 0 | 0 | 0 |
| C6 Staging | 0 | 0 | 1 | 0 |
| **Total** | **61** | **4** | **1** | **4** |

C7 lists Sprint 2's six accepted items. They were not re-run and are not counted.

| ID | Failure | Suggested owner |
|---|---|---|
| S3-F1 | Live, most technical questions have no "What does this mean?" link: 1 of 10 in a smartphone search (US-07 criterion 1) | Manu |
| S3-F2 | The live end-to-end test failed with the database on: no product was scored, so the best match card had no link | Jhanvi and Vaishvi; Jenish for offers; Yashkumar for the test |
| S3-F3 | Every live result carries the example explanations and offers ("Nova Book 14", "Example Store"), and the page shows their date | Jhanvi (explain) and Jenish (commerce) |
| S3-F4 | The explanations are short and say why a term matters, but most explain it with more jargon, so they are not in plain language (US-07 criterion 2) | Manu, with Jhanvi for the criteria's wording (US-03) |
| S3-B1 | Staging can't be checked: the Render and Vercel services are not created yet | Pal (Render) and Yashkumar (Vercel) |

**Not met yet, shown as they stand (not counted as failures).** Each waits on data still to be labelled or on a later
story. The task names US-10 criterion 3 and US-16 criterion 8. The plan Om approved for this check adds US-15
criterion 5, and US-15 criterion 2 follows from it (PR #105). If they were counted, the verdict would read 9 failures.
- **US-10 criterion 3:** "Always" can be ticked, but saving it to memory needs login (US-30, Sprint 4).
- **US-15 criterion 2:** the model stage works, but `MATCHER` stays fuzzy by default. PR #105: "The default stays
  fuzzy", until criterion 5 is shown.
- **US-15 criterion 5:** not shown. PR #105: "Criterion 5 (beats fuzzy at precision 0.95) is not shown". On our
  held-out pairs the matcher's precision of 1.0 rests on one called pair, and its F1 (0.25) is below fuzzy's (0.4). It
  needs more labelled pairs.
- **US-16 criterion 8:** 5 of the 30 seeded conflicts are entered.

**F-1, accepted in Sprint 2, still fails, and by far more.** The fast suite took 199.9 s on Python 3.11 and 106.3 s on
3.13, against a 60 s target. It was 64.1 s when the Product Owner accepted it. The verdict counts F-1 as accepted. That
holds only if the Product Owner confirms the acceptance still stands at three times the time (section 3, C3).

## 2. How it was checked

- **Clean environments.** Every suite ran on `main` at `ce8090d`, from the branch `chore/sprint-3-check` (equal to
  `main` until this report):
  - a new Python 3.11.15 venv for the backend CI job, the database tests and the ml job;
  - a new Python 3.13.13 venv for the Windows job;
  - `npm ci` for the frontend.
- **Criteria (C1).**
  - Each criterion is copied word for word from `backlog/stories.yaml`. The US-46 additions come from PR #91's
    description.
  - Each story's backend tests ran together with `-rA`, so every pass is listed, with the network guard on
    (`tests/conftest.py`).
  - The frontend's unit tests and the mock browser suite ran once each. The lines that name a story are picked out in
    [c1-frontend.txt](sprint-3-check/c1-frontend.txt).
  - Where a shopper sees a criterion, a live run checked it too.
- **Database tests (C3).**
  - They ran on a throwaway `postgres:17` container with `REQUIRE_DB_TESTS=1`.
  - The first attempt used port 5432. This machine also runs a Windows PostgreSQL 18 service on that port, so the
    tests reached it and failed on its password, never reaching the container.
  - The service was left alone, and the container was started again on port 55432.
- **Live runs (C4, C5).** Five runs, R1 to R5, each with its own share of the budget.
  - **The backend** ran from a launcher kept outside the repository. It:
    - counts every call to an LLM, Serper, Jina, Firecrawl or a Reddit archive, and refuses any call past the run's
      cap (the guard `evals/live_briefs.py` uses);
    - writes the app's INFO log, so the `llm call` and `research:` lines can be counted;
    - takes the database switch from the shell, as `scripts/dev.py --no-db` does.
  - **The frontend** ran with `npm run dev` in live mode, as `scripts/dev.py` starts it.
  - **`.env`:** the checker did not open, print or change it. The app loaded its keys and settings from it as usual.
    Each run's own settings were temporary shell variables, which win over `.env`.
  - **`EXTRACT_MAX_SOURCES=4` on every run.** The default is 40, and a normal search reads about 30 public and 4
    Reddit sources (`backend/config/extract.yaml`). So each live search here read about a tenth of its usual sources.
  - The walks (R1, R4) were clicked and typed in a real Chromium page through Playwright, as a shopper would.
  - Screenshots and evidence hold no Reddit text, quote or username (D-53). The scan is in
    [scan.txt](sprint-3-check/scan.txt).
- **Timing.** The suites ran one after another. Nothing else from this check ran during the Python 3.11 fast suite.
  The 3.13 suites ran in the background while only files were read, tools loaded and bare connection checks made (no
  test, build or server). Docker Desktop was
  running throughout.

  | Run | What it did | Database |
  |---|---|---|
  | R1 | A walk through every screen: a smartphone request with a budget, a hard rule and an exclusion; the rubric editor; "What does this mean?"; the results and a source drawer | on |
  | R2 | The live end-to-end test (`e2e/live/full-flow.spec.ts`) | on |
  | R3 | The same test | off |
  | R4 | The Reddit-off drill, walked: `REDDIT_SOURCE=off`, the laptop request, the results and a known value's sources | off |
  | R5 | The matcher drill: `MATCHER=model MATCHER_FORCE_FAIL=1 python -m app.cli … --claims --no-db` | off |

### Decisions I took

1. **`EXTRACT_MAX_SOURCES=4` on every live run,** against a default of 40, to fit the LLM budget of 40. Each result's
   coverage note says how many sources were not read. A run at the default would use about 30 or more LLM calls, so
   none was tried. S3-F2 names the cap as a possible cause.
2. **R4 ran with the database off; the plan had it on.** That way the "not saved" banner could be checked on screen.
   R1 and R2 cover the database on.
3. **US-21's source drawer for a known value was checked in R4's walk.** R2's results belong to the test's own browser
   tab, and another browser gets "We couldn't find this search".
4. **The database tests ran on port 55432,** because port 5432 is taken by the Windows service.
5. **Four criteria that wait on labelled data or a later story are shown as "Not met yet, as it stands"**, with their
   reasons, and not counted in the verdict (section 1 says why each is there, and what the count would be).
6. **US-38 criteria 4 and 5 are judged on their page part only,** as the task scopes them. YouTube is Sprint 5 work.
7. **US-18 criterion 6 is PASS.** R4's page, seen in the walk, showed scored cards with coverage, main reasons and a
   source link. In R3 the live test found the link on the best match card and opened its criteria. How weak those live
   scores are is recorded under S3-F2 and in section 7.
8. **"Plain language" (US-07 criterion 2) was judged on all six explanations the live runs showed,** not only the one
   opened (`c4-live-checks.txt`, item 7). Most explain a term with more jargon, so the row is a FAIL.
9. **The fast-suite time is listed under the accepted F-1, not as a new failure,** although it has grown (section 1
   says the verdict depends on the Product Owner confirming F-1).
10. **R4's ten interview answers were clicked by a short script inside the page,** to save time. R1's were clicked one
    by one.

## 3. Results by check

### C1. Acceptance criteria

**US-09 Keep must-haves separate from preferences** ([c1-us-09.txt](sprint-3-check/c1-us-09.txt): 69 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 The rubric has two lists: hard rules, and preferences weighted 0 to 10 that add up to 1 | PASS | Tests. Live (R1): "Must have" holds the budget and the rule; "What counts most" holds 10 weights from 0 to 10 whose shares add to 100% (read from the page; the screenshot shows the page after the edits) | [c1-us-09.txt](sprint-3-check/c1-us-09.txt), [c4-walk.txt](sprint-3-check/c4-walk.txt), [c4-walk-08-brief.png](sprint-3-check/c4-walk-08-brief.png) |
| 2 The user sees which of their statements became hard rules and can switch any of them | PASS | Tests. Live: "at least 8 GB RAM" shows with Must-have and Preference; switched to Preference, it stayed so after a reload; switched back | [c1-us-09.txt](sprint-3-check/c1-us-09.txt), [c4-walk.txt](sprint-3-check/c4-walk.txt) |
| 3 [DR-08] Hard rules only filter products; preferences only change the order | PASS | Tests. Live: the RAM rule left out 4 phones with its reason; preferences only set scores (R3, R4) | [c1-us-09.txt](sprint-3-check/c1-us-09.txt), [c4-walk.txt](sprint-3-check/c4-walk.txt) |
| 4 Criteria the interview did not ask about keep the category's default weight | PASS | Tests. Live: the skipped Dynamic Range shows "Standard", 6 | [c1-us-09.txt](sprint-3-check/c1-us-09.txt), [c4-walk.txt](sprint-3-check/c4-walk.txt) |
| 5 A user's manual weight edits survive if the rubric is rebuilt | PASS | Tests. Live: Battery Life set to 10 stayed 10 ("You set this") after another answer was changed | [c1-us-09.txt](sprint-3-check/c1-us-09.txt), [c4-walk.txt](sprint-3-check/c4-walk.txt) |

**US-10 Rule out brands and features** ([c1-us-10.txt](sprint-3-check/c1-us-10.txt): 36 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Brand, model or feature exclusions can be typed in words, such as "no Xiaomi" or "not touchscreen" | PASS | Tests. Live: "no Xiaomi" in the request became a brand; typed "not a curved screen" became the chip "curved display, Feature"; "no weak speakers" came back as not understood, nothing guessed | [c1-us-10.txt](sprint-3-check/c1-us-10.txt), [c4-walk-07-leave-out.png](sprint-3-check/c4-walk-07-leave-out.png) |
| 2 Excluded items never appear in results, and they show in the filtered-out list with the reason | PASS | Tests. Live (R1): 4 Xiaomi and Redmi phones are only in "Left out", each with "You ruled out Xiaomi". (POCO, a Xiaomi sub-brand, was not caught: section 7) | [c1-us-10.txt](sprint-3-check/c1-us-10.txt), [c4-walk-10-results.png](sprint-3-check/c4-walk-10-results.png) |
| 3 An exclusion can be marked "always" and is then saved to memory | Not met yet, as it stands | It can be marked (live: ticked on "curved display"), but not saved. The page says: "“Always” applies to this search for now. ShopSense will remember it for later searches once you can sign in." For an exclusion from the request: "“Always” can't be kept yet". Needs login (US-30, Sprint 4; D-36) | [c1-us-10.txt](sprint-3-check/c1-us-10.txt), [c1-frontend.txt](sprint-3-check/c1-frontend.txt), [c4-walk.txt](sprint-3-check/c4-walk.txt) |

**US-15 Match product names and variants** ([c1-us-15.txt](sprint-3-check/c1-us-15.txt): 25 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 "Sony WH-1000XM5", "XM5" and "1000XM5 headphones" become one product | PASS | Tests | [c1-us-15.txt](sprint-3-check/c1-us-15.txt) |
| 2 A fine-tuned matching model decides each pair, and only confident matches merge; uncertain pairs stay separate | Not met yet, as it stands | The model stage passes its tests with fake scorers. But `MATCHER` defaults to fuzzy, so every live run here used fuzzy matching. PR #105: "The default stays fuzzy", until criterion 5's target is shown | [c1-us-15.txt](sprint-3-check/c1-us-15.txt) |
| 3 [DR-02] Versions that differ in price or specs, such as RAM, storage or region, are separate variants of one family | PASS | Tests. (Live, a variant and a claim about another variant got mixed: section 7) | [c1-us-15.txt](sprint-3-check/c1-us-15.txt) |
| 4 [DR-06] Aliases are saved and reused in later searches | PASS | Tests, and the Postgres round trip among the database tests. Live: R1 saved a `product_aliases` row ("vivo t5x") in Supabase | [c1-us-15.txt](sprint-3-check/c1-us-15.txt), [c3-db-tests.txt](sprint-3-check/c3-db-tests.txt), section 6 |
| 5 On held-out labelled pairs it beats fuzzy matching, with precision of 0.95 or better (target) | Not met yet, as it stands | Not shown (PR #105). `ml/results/matcher_v1.json`: precision 1.0 from one called pair (tp 1, fp 0), recall 0.14 and F1 0.25, against fuzzy's 0.29 and 0.4. The ML tests that set the threshold pass. Needs more labelled pairs | [c1-us-15.txt](sprint-3-check/c1-us-15.txt) |
| 6 If the model fails to load, fuzzy matching runs instead and the run is logged | PASS | Tests. Live drill R5: "method fuzzy" and the log line | [c1-us-15.txt](sprint-3-check/c1-us-15.txt), [c5-matcher.txt](sprint-3-check/c5-matcher.txt) |

**US-16 Learned source trust, disputes and unknowns** ([c1-us-16.txt](sprint-3-check/c1-us-16.txt): 28 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Claims for one product and criterion are combined using source weights | PASS | Tests | [c1-us-16.txt](sprint-3-check/c1-us-16.txt) |
| 2 [DR-03] Manufacturer and lab sources outrank editorial reviews, and editorial outranks community posts | PASS | Tests | [c1-us-16.txt](sprint-3-check/c1-us-16.txt) |
| 3 Each known site starts from a 0 to 100 trust score adjusted per category, kept in one config table that includes Indian review sites such as 91mobiles, Smartprix, Digit and Gadgets 360 | PASS | Tests | [c1-us-16.txt](sprint-3-check/c1-us-16.txt) |
| 4 Affiliate pages (paths such as /advisor/ or /deals/) start lower than editorial reviews, and Reddit counts as community evidence, never editorial | PASS | Tests | [c1-us-16.txt](sprint-3-check/c1-us-16.txt) |
| 5 Weights are then learned; sources that often disagree with the wider consensus count for less | PASS | Tests (made-up data; learning needs 3 or more sites) | [c1-us-16.txt](sprint-3-check/c1-us-16.txt) |
| 6 When independent sources disagree strongly the state is Disputed, and both values are shown with their sources | PASS | Tests. No live run here produced a Disputed value | [c1-us-16.txt](sprint-3-check/c1-us-16.txt), [c1-frontend.txt](sprint-3-check/c1-frontend.txt) |
| 7 [DR-09] With no trustworthy claim the state is Unknown, and it is never scored as zero | PASS | Tests. Live: an unknown value's sources say "No source gave this value. It isn't counted as zero." | [c1-us-16.txt](sprint-3-check/c1-us-16.txt), [c4-walk-11-drawer-unknown.png](sprint-3-check/c4-walk-11-drawer-unknown.png) |
| 8 It resolves more of the 30 seeded conflicts correctly than majority voting (target) | Not met yet, as it stands | 5 of the 30 conflicts are entered. On those 5, learned gets 3 right, majority voting 0 (tier order also 3). Run now with `python -m scripts.eval_trust` | [c1-us-16.txt](sprint-3-check/c1-us-16.txt) |

**US-40 Never recommend an unsourced product** ([c1-us-40.txt](sprint-3-check/c1-us-40.txt): 11 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 A product with no mention in any source and no source link is dropped before scoring | PASS | Tests. Live: no run logged a product dropped as unsourced (0 "verify: dropped" lines in R1 to R5) | [c1-us-40.txt](sprint-3-check/c1-us-40.txt), [c4-live-checks.txt](sprint-3-check/c4-live-checks.txt) |
| 2 Dropped products are logged with the reason, for testing | PASS | Tests | [c1-us-40.txt](sprint-3-check/c1-us-40.txt) |
| 3 In recorded runs with planted invented product names, none reaches the results | PASS | Tests on a recorded-style run with three planted names. The run is made up: the real recordings hold no extraction | [c1-us-40.txt](sprint-3-check/c1-us-40.txt) |

**US-17 Filter out products that break the rules** ([c1-us-17.txt](sprint-3-check/c1-us-17.txt): 232 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 [DR-01] Products above the budget, breaking a hard rule, or matching an exclusion are removed in code, never by the LLM | PASS | Tests: `app/decide` never imports the LLM client. Live: decide ran in code in every run, with no fallback | [c1-us-17.txt](sprint-3-check/c1-us-17.txt) |
| 2 Products fall into shown, filtered out with a reason (such as "₹94,990 is above your ₹80,000 budget"), and can't tell where a hard-rule value is unknown | PASS | Tests. Live: "₹3,49,990 is above your ₹80,000 budget" (R2); "It fails "at least 8 GB RAM": it has 6 GB" (R1); can't tell for an unknown price or RAM | [c1-us-17.txt](sprint-3-check/c1-us-17.txt), [c4-live-e2e-db-on.txt](sprint-3-check/c4-live-e2e-db-on.txt), [c4-walk.txt](sprint-3-check/c4-walk.txt) |
| 3 The "can't tell" group is visible, with a link to research it | PASS | Tests and the mock browser suite. Live (R1, R4): "Couldn't check (6)" and "(16)", each with "Research it" (a web search in a new tab) | [c1-frontend.txt](sprint-3-check/c1-frontend.txt), [c4-walk-10-results.png](sprint-3-check/c4-walk-10-results.png) |
| 4 If nothing passes, the app names the one rule whose relaxation would help most | PASS | Tests, and the mock browser test `e2e/results-sources.spec.ts:284` ("with nothing left, the relaxation hint says which must-have to loosen"). No live run left nothing shown | [c1-us-17.txt](sprint-3-check/c1-us-17.txt), [c1-frontend.txt](sprint-3-check/c1-frontend.txt) |
| 5 No recommended product breaks a hard rule, checked by tests on every test brief | PASS | Tests on every brief. Live: all 17 shown products in R1 to R4 had a known price within the budget, and R1's met the RAM rule (8 GB) | [c1-us-17.txt](sprint-3-check/c1-us-17.txt), [c4-live-checks.txt](sprint-3-check/c4-live-checks.txt) |

**US-18 Rank on the user's priorities with coverage** ([c1-us-18.txt](sprint-3-check/c1-us-18.txt): 39 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Score is the weighted sum of criterion scores using the user's weights | PASS | Tests. Live (R4): the best match's Battery Life gives "+88 points", and its score is 88 | [c1-us-18.txt](sprint-3-check/c1-us-18.txt), [c5-reddit-off.txt](sprint-3-check/c5-reddit-off.txt) |
| 2 [DR-10] Mention counts show how strong the evidence is but never set the score by themselves | PASS | Tests | [c1-us-18.txt](sprint-3-check/c1-us-18.txt) |
| 3 [DR-09] An unknown criterion takes the average of similar products and is marked estimated | PASS | Tests. Live (R4): the runner-up's Battery Life reads "Estimated +88 points" | [c1-us-18.txt](sprint-3-check/c1-us-18.txt), [c5-reddit-off-results.png](sprint-3-check/c5-reddit-off-results.png) |
| 4 Each product shows a data-coverage percentage | PASS | Tests. Live: every card shows "Data coverage n%" | [c1-us-18.txt](sprint-3-check/c1-us-18.txt), [c4-walk-10-results.png](sprint-3-check/c4-walk-10-results.png) |
| 5 Labels show best match, runner-up, and the gap to the leader | PASS | Tests. Live (R4): "Best match" and "Runner-up". Every ranked product scored 88, so no gap showed live | [c1-us-18.txt](sprint-3-check/c1-us-18.txt), [c1-frontend.txt](sprint-3-check/c1-frontend.txt) |
| 6 The results page shows ranked cards with score, coverage, main reasons and source links | PASS | Live (R4, seen): cards with "Score 88", coverage, main reasons and a 91mobiles.com link. R3: the live test found an `https` link on the best match card and opened its criteria; its scores were 88. In R1 and R2 no card had a score (S3-F2) | [c5-reddit-off.txt](sprint-3-check/c5-reddit-off.txt), [c5-reddit-off-results.png](sprint-3-check/c5-reddit-off-results.png), [c4-live-e2e-db-off.txt](sprint-3-check/c4-live-e2e-db-off.txt) |

**US-21 Click any claim to see its source** ([c1-us-21.txt](sprint-3-check/c1-us-21.txt): 8 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Every factual claim on the results page has a source link and its quote | PASS | Tests. Live (R4): the known value shows its source link, and its drawer the quote | [c1-us-21.txt](sprint-3-check/c1-us-21.txt), [c5-reddit-off.txt](sprint-3-check/c5-reddit-off.txt) |
| 2 Clicking shows the source name, type, date and exact quote | PASS | Live (R4): "91mobiles.com", "Review site · Checked 15 Nov 2026", the quote "a Battery Rating of 8.8/10", a link to the page | [c5-reddit-off-drawer-known.png](sprint-3-check/c5-reddit-off-drawer-known.png), [c1-us-21.txt](sprint-3-check/c1-us-21.txt) |
| 3 A Disputed claim shows all its sources, not only the winner | PASS | Tests and the mock browser suite. No live Disputed value | [c1-us-21.txt](sprint-3-check/c1-us-21.txt), [c1-frontend.txt](sprint-3-check/c1-frontend.txt) |
| 4 The quote is stored at retrieval time, so it stays visible even if the page changes | PASS | Tests, and the saved-row test among the database tests (web quotes kept, Reddit quotes not, D-53) | [c1-us-21.txt](sprint-3-check/c1-us-21.txt), [c3-db-tests.txt](sprint-3-check/c3-db-tests.txt) |

**US-07 Explain unfamiliar terms on request** ([c1-us-07.txt](sprint-3-check/c1-us-07.txt): 6 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Every question containing a technical term has a "What does this mean?" link | **FAIL** (S3-F1) | Tests pass for the words in `config/technical_terms.yaml`. Live: 1 of 10 questions in R1 had the link; Sensor Size, Maximum Aperture, Low-Light SNR, Dynamic Range, Resolution (MP) and 35mm Equivalent Focal Length had none. R4: 5 of 10 | [c4-walk.txt](sprint-3-check/c4-walk.txt), [c5-reddit-off.txt](sprint-3-check/c5-reddit-off.txt) |
| 2 Explanations are at most 40 words, in plain language, and say why the term matters for this use | **FAIL** (S3-F4) | Tests check the 40-word cap only. Live: all six explanations shown were 15 to 18 words and said why the term matters for coding or photography. But most explain the term with more jargon: "single-core clock frequency", "volatile memory", "sequential read throughput", "stops of camera shake the OIS can compensate" | [c4-live-checks.txt](sprint-3-check/c4-live-checks.txt) (item 7), [c4-walk-04-what-does-this-mean.png](sprint-3-check/c4-walk-04-what-does-this-mean.png) |
| 3 Opening an explanation does not count as an answer | PASS | Tests, and the mock browser test `e2e/term-help.spec.ts:31` ("keyboard alone: … and nothing is answered"). Live: "2 of 10 settled" and "Answers so far (2)" before and after opening it | [c1-frontend.txt](sprint-3-check/c1-frontend.txt), [c4-walk.txt](sprint-3-check/c4-walk.txt) |
| 4 Definitions come from the saved criteria in US-03, so no extra LLM call is needed | PASS | Tests. Live: the text is the criterion's own saved definition, and the guard logged no LLM call from 10:29:13 to 10:33:15, while it was opened and closed (about 10:30) | [c1-us-07.txt](sprint-3-check/c1-us-07.txt), [c4-live-checks.txt](sprint-3-check/c4-live-checks.txt) (items 4 and 7) |

**US-38 Add YouTube and hard-to-read pages, page criteria only** ([c1-us-38.txt](sprint-3-check/c1-us-38.txt): 43 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 3 Pages that fail a simple fetch or need JavaScript are read through Jina Reader | PASS | Tests. No live run needed Jina | [c1-us-38.txt](sprint-3-check/c1-us-38.txt) |
| 4 Video and page text goes through the same extraction and quote rules as every other source | PASS for the page part | Tests, including the 33 hostile-page cases. Video: not built (Sprint 5) | [c1-us-38.txt](sprint-3-check/c1-us-38.txt) |
| 5 If YouTube or Jina fails, the search continues without them and the coverage note says so | PASS for the page part | Tests: Jina failing goes to Firecrawl, both failing skip the page with a note. YouTube: not built (Sprint 5) | [c1-us-38.txt](sprint-3-check/c1-us-38.txt) |

**US-46 additions in S3-1** (PR #91; [c1-us-46-s3-1.txt](sprint-3-check/c1-us-46-s3-1.txt): 28 cases pass). Rows a
to e are PR #91's description bullets, word for word: they are not in `backlog/stories.yaml`. Row f is US-46's own
criterion 4, run with DeepSeek in the chain.

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| a DeepSeek (D-63) is switched on for public text only, after Gemini and Mistral and before Groq, in the short, medium and long public routes. It takes no turn in bulk work's rotation. JSON mode is on, as its API docs list it. | PASS | Tests, including JSON mode (`test_deepseek_is_switched_on_for_public_text_only`). No live run reached DeepSeek. The short route has no Gemini (section 7) | [c1-us-46-s3-1.txt](sprint-3-check/c1-us-46-s3-1.txt) |
| b HTTP 402 (no balance) switches a provider off for the run with one warning and no retry, so DeepSeek never spends money that isn't a granted balance (D-11). A provider dead for the run is logged once, not on every call after. | PASS | Tests | [c1-us-46-s3-1.txt](sprint-3-check/c1-us-46-s3-1.txt) |
| c Public pages may use at most 25% of each Groq model's daily tokens (pool.public_daily_share). Past it they skip Groq, and private and Reddit work keep the rest. | PASS | Tests | [c1-us-46-s3-1.txt](sprint-3-check/c1-us-46-s3-1.txt) |
| d US-14: a public extraction prompt may carry a generated criterion as the fixed criterion it maps to (a synonyms table in config/extract.yaml), known spec keys from the hard rules, and price_inr when there is a budget. Only fixed keys and our own definitions are sent; claims come back under the search's own key. Spec keys only fill the room the criteria leave (10 lines in all, the price first), so a capped page still fits Groq whole. --claims prints the count. | PASS | Tests (and US-14's stand-ins, PR #112). Live, R5's CLI printed "Public prompts carried 6 of 10 criteria and 1 spec key (price_inr)." | [c1-us-46-s3-1.txt](sprint-3-check/c1-us-46-s3-1.txt), [c5-matcher.txt](sprint-3-check/c5-matcher.txt) |
| e smoke_apis checks DeepSeek with one call and reports "HTTP 402, no balance" plainly. docs/api-setup.md has a DeepSeek section with its URL, model, limits and terms. | PASS | Tests | [c1-us-46-s3-1.txt](sprint-3-check/c1-us-46-s3-1.txt) |
| f (US-46 criterion 4) Tests simulate a failing, a slow and a rate-limited provider | PASS | Tests, with DeepSeek in the chain | [c1-us-46-s3-1.txt](sprint-3-check/c1-us-46-s3-1.txt) |

### C2. B-2: core logic offline

| Item | Result | Proved by | File |
|---|---|---|---|
| US-32: "Filtering, scoring, ranking, weight mapping and mention counting run in tests with no network" | PASS | 759 cases across the decide, interview, recommender and match tests, 0 failures, with the network guard on. None of those files has a `live` mark | [c2-b2.txt](sprint-3-check/c2-b2.txt) |

### C3. CI and regression

| Check | Result | Detail | File |
|---|---|---|---|
| CI on `main` (`ce8090d`): all five jobs | PASS | secrets, frontend, backend, backend-windows, ml: all success | [c3-ci-on-main.txt](sprint-3-check/c3-ci-on-main.txt) |
| Database tests, `REQUIRE_DB_TESTS=1`, `postgres:17` | PASS | 37 test functions, 46 cases, 0 failures. In CI, 5,115 cases ran with none skipped, so these ran there too | [c3-db-tests.txt](sprint-3-check/c3-db-tests.txt) |
| Backend on 3.11, as the backend job | PASS | ruff and format clean; mypy clean (135 files); 5,115 cases, 0 failures, 46 skipped (no database here); coverage 97.87%; module gates pass; contracts unchanged | [c3-regression-backend-py311.txt](sprint-3-check/c3-regression-backend-py311.txt) |
| Backend on 3.13, as the Windows job | PASS | 5,115 cases, 0 failures, 46 skipped; contracts unchanged | [c3-regression-backend-py313.txt](sprint-3-check/c3-regression-backend-py313.txt) |
| Frontend, as the frontend job | PASS | lint and typecheck clean; 639 unit tests in 29 files pass | [c3-regression-frontend.txt](sprint-3-check/c3-regression-frontend.txt) |
| Frontend browser suite, mock mode (not in CI) | PASS | 185 passed, 2 skipped (the live test; a motion test the browser can't run), 0 failed | [c3-regression-frontend.txt](sprint-3-check/c3-regression-frontend.txt) |
| ML job | PASS | ruff, format and mypy `--strict` clean; 704 tests pass; labels check OK; kappa 0.859 | [c3-regression-ml.txt](sprint-3-check/c3-regression-ml.txt) |
| Fast suite under 60 s (US-32) | Accepted (F-1), still failing | 5,098 cases, 0 failures: **199.9 s** on 3.11 and **106.3 s** on 3.13. Sprint 2 measured 64.1 s and 58.4 s. `tests/unit/test_imports.py` alone takes 48 s on 3.11. Nothing else from this check ran during the 3.11 run. The verdict counts this as accepted only if the Product Owner confirms F-1 still stands | [c3-fast-suite.txt](sprint-3-check/c3-fast-suite.txt) |

### C4. The live flow, database on and off

| Check | Result | Detail | File |
|---|---|---|---|
| R1, every screen walked, database on | PASS | Request to results with no error; `check_request_saved.py` says saved. What it found is in C1 and section 7 | [c4-walk.txt](sprint-3-check/c4-walk.txt), `c4-walk-*.png` |
| R2, the live end-to-end test, database on | **FAIL** (S3-F2) | Reached the results, then failed: the best match card had no link | [c4-live-e2e-db-on.txt](sprint-3-check/c4-live-e2e-db-on.txt) |
| R3, the live end-to-end test, database off | PASS | 1 passed in 21 s; 11 products ranked | [c4-live-e2e-db-off.txt](sprint-3-check/c4-live-e2e-db-off.txt) |
| Database on: the request and the search are saved | PASS | R1 and R2: `check_request_saved.py` says saved, and Supabase holds a `requests` row and a `searches` row for each (section 6) | [c4-walk.txt](sprint-3-check/c4-walk.txt), [c4-live-e2e-db-on.txt](sprint-3-check/c4-live-e2e-db-on.txt), [c4-supabase.txt](sprint-3-check/c4-supabase.txt) |
| Database off: nothing saved, and the shopper is told | PASS | Health says `"database": "off"`; `request_saved` "off", `saved` false; the page shows "This result isn't saved. It stays at this address for up to 30 minutes, then it's gone." | [c4-live-e2e-db-off.txt](sprint-3-check/c4-live-e2e-db-off.txt), [c5-reddit-off.txt](sprint-3-check/c5-reddit-off.txt) |
| No example product in a live result | **FAIL** (S3-F3) | No example product is a card or in a group, and decide never fell back. But every live result holds the example explanations and offers, and the page shows their date | [c4-example-data.txt](sprint-3-check/c4-example-data.txt) |

### C5. Drills

| Drill | Result | Detail | File |
|---|---|---|---|
| Switch Reddit off: the result arrives with a lower coverage note | PASS | R4, `REDDIT_SOURCE=off`: results in about 15 s; the coverage note says "Reddit could not be read."; no Reddit call was made | [c5-reddit-off.txt](sprint-3-check/c5-reddit-off.txt), [c5-reddit-off-results.png](sprint-3-check/c5-reddit-off-results.png) |
| Force the matcher to fail and show the fuzzy fallback | PASS | R5: "Products after matching (14, method fuzzy):" and the log line "match: fuzzy matching ran instead of the model: MATCHER_FORCE_FAIL=1 made it fail on purpose"; the search finished | [c5-matcher.txt](sprint-3-check/c5-matcher.txt) |

### C6. Staging (S3-17)

| Check | Result | File |
|---|---|---|
| Health and one search on staging | **BLOCKED** (S3-B1): the services are not created (Om, 15 Nov) | [c6-staging.txt](sprint-3-check/c6-staging.txt) |

### C7. Sprint 2's accepted items

The Product Owner accepted these on 10 Nov ([sprint-2-check.md](sprint-2-check.md), "Accepted by the Product Owner").
They were not re-run and don't count in the verdict. F-1's time was measured again only because C3 asks for it.

| Item | First-run result | Reason | Evidence |
|---|---|---|---|
| F-1 The fast suite takes 64.1 s on Python 3.11; the limit is 60 s (US-32) | FAIL | Accepted as it stands | [sprint-2-check/c2-fast-suite.txt](sprint-2-check/c2-fast-suite.txt) |
| F-2 CI does not block a merge: `main` has no protection (US-32, US-48) | FAIL | Accepted as it stands | [sprint-2-check/c6-github.txt](sprint-2-check/c6-github.txt) |
| F-7 Pull requests were opened by Om's account, not by their owners | FAIL | Settled by D-05, as amended 10 Nov | [sprint-2-check/c6-github.txt](sprint-2-check/c6-github.txt) |
| F-8 PR #50's description names an AI tool | FAIL | Accepted as it stands | [sprint-2-check/c6-github.txt](sprint-2-check/c6-github.txt) |
| F-9 Two Sprint 1 branch names name an AI tool | FAIL | Accepted as it stands | [sprint-2-check/c6-github.txt](sprint-2-check/c6-github.txt) |
| F-10 Seven commit messages on `main`, all from before Sprint 2, name an AI tool | FAIL | Kept by D-10: `main`'s history is not rewritten | [sprint-2-check/c6-github.txt](sprint-2-check/c6-github.txt) |

## 4. Failures

**S3-F1. Most technical questions have no "What does this mean?" link.** C1, US-07 criterion 1.
- **Expected:** "Every question containing a technical term has a "What does this mean?" link".
- **What happened:**
  - **R1, a smartphone for photography:** the 10 generated criteria were nearly all camera terms. Only "Optical Image
    Stabilization" had the link. These had none: Sensor Size, Maximum Aperture, Low-Light SNR, Dynamic Range,
    Resolution (MP), 35mm Equivalent Focal Length.
  - **R4, a laptop for coding:** 5 of 10 had it. Keyboard Travel had none.
  - Every question also has "Why this question?", which shows the same definition. But that control is not the one
    the criterion names, and it is not limited to technical terms.
- **Steps:**
  1. Start the stack in live mode.
  2. Type "Phone with a good camera under ₹30,000 with at least 8 GB RAM, no Xiaomi".
  3. Click Looks right, then Start the questions, and read each question.
- **Cause:** a question gets the link only when its label or key holds a word in `config/technical_terms.yaml`. That
  list has no camera words, such as sensor, aperture, SNR, dynamic range, megapixels or focal length. Generated criteria
  bring technical words the list doesn't know. The tests check the list mechanism with known words, so they pass.
- **Suggested owner:** Manu (US-07).

**S3-F2. The live end-to-end test failed with the database on.** C4.
- **Expected:** `LIVE_E2E=1 npx playwright test -c e2e/live/playwright.live.config.ts` passes. The best match card
  holds an `https` link, and "Show all criteria" lists its values.
- **What happened:**
  - The flow reached the results, then failed at `full-flow.spec.ts:80`: "getByRole('list', { name: 'Best match'
    }).getByRole('link').first() … element(s) not found".
  - The result had two shown laptops, both "No score" with coverage 0%. Their only known values were a price on each,
    and a battery life ("8 Hrs") on one. One product's value can't be compared with another's, so nothing was scored
    ([c4-live-checks.txt](sprint-3-check/c4-live-checks.txt), item 3).
  - Both Reddit sources were dropped. The app's log says each Groq model was "skipped: no room before the time limit"
    (6 skips, logged as `rate_limited` with nothing sent), and 2 answers could not be used. Mistral timed out, and
    Gemini answered twice.
  - The same request passed in R3 a few minutes later, with 11 products ranked. R1 (smartphone) also scored nothing.
    So the live flow passes or fails depending on which sources answer.
- **Steps:**
  1. Run `python scripts/dev.py` with the database on.
  2. From `frontend/`, run `LIVE_E2E=1 npx playwright test -c e2e/live/playwright.live.config.ts`. This check ran it
     with `EXTRACT_MAX_SOURCES=4`.
- **Cause:**
  - **The check's own cap may be part of it.** Every live run here read at most 4 sources, against a default of 40 (a
    normal search reads about 34). With both Reddit sources dropped, R2 had 2 public pages left. The test was not run
    at the default, because that would use about 30 or more LLM calls, beyond this check's budget. Earlier runs at 8
    sources also scored nothing (PR #110, PR #112).
  - It is the demo risk named since S3-10b: few rubric values become scores, so a card often has no known value to link.
  - The card's other link would be a store offer, but commerce is still a stub (US-24). Its example offers never match
    a live product.
  - The Reddit extraction route has only Groq, and it runs out of room under load.
- **Suggested owners:**
  - Jhanvi: extraction yield and the Reddit route.
  - Vaishvi: scoring with few values (US-18).
  - Jenish: real offers (US-24, Sprint 4).
  - Yashkumar: whether the test should expect a store offer before US-24.

**S3-F3. Live results carry example data.** C4.
- **Expected:** no example product in a live result.
- **What happened:**
  - Every live result (R1 to R4) holds the explain stub's two example explanations, for `prd_nova_book_14` and
    `prd_kestrel_slim_15`. Their text names "Nova Book 14" and "Orion Play 15".
  - Every live result also holds the commerce stub's two example offers: "Example Store" and "Another Store", listing
    "Nova Book 14" and "Kestrel Slim 15".
  - None of these products is a card or in a group. But the results page shows "Prices checked 3 Oct, 1:30 pm.",
    which is the example offers' time (`2026-10-03T08:00Z`), on searches that found no store offer at all ("No price
    found yet" on every card).
  - The sample note, "These are sample results while live research is being built…", is on every live result.
- **Steps:**
  1. Run any live search.
  2. Read `GET /api/search/runs/<search ID>` and look at `explanations` and `commerce.offers`.
  3. On the results page, read the line under the groups.
- **Cause:** `app/explain/stage.py` and `app/commerce/stage.py` return `examples.explanations()` and
  `examples.commerce()`. `Pipeline.result()` copies their output into the live result as it is. Only decide's sample
  products are kept out of a live result.
- **Suggested owners:** Jhanvi (explain, US-20) and Jenish (commerce, US-24), or whoever owns the pipeline's handling
  of stubs.

**S3-F4. The explanations are not in plain language.** C1, US-07 criterion 2.
- **Expected:** "Explanations are at most 40 words, in plain language, and say why the term matters for this use".
- **What happened:**
  - All six explanations the live runs showed are 15 to 18 words, and each says why the term matters for coding or
    photography. Those two parts are met.
  - Most explain the term with more technical words: "the processor's single-core clock frequency in gigahertz",
    "total volatile memory in gigabytes", "sequential read throughput in megabytes per second", "how many stops of
    camera shake the OIS can compensate". A shopper who needs "RAM" explained won't know "volatile memory".
  - Display PPI ("Pixels per inch of the screen, determining how sharp text appears") is the plain one.
- **Steps:**
  1. In a live search for a laptop for coding, or a phone for photography, open "What does this mean?" on each question
     that has it.
  2. Or read the definitions in the category's saved criteria, which are the same text
     ([c4-live-checks.txt](sprint-3-check/c4-live-checks.txt), item 7).
- **Cause:**
  - The explanation is the criterion's saved definition (criterion 4), and the criteria stage generates those
    definitions with no plain-language rule.
  - The tests check only the 40-word cap.
- **Suggested owners:** Manu (US-07), with Jhanvi for the criteria stage's wording (US-03).

**S3-B1. Staging can't be checked.** C6.
- **Expected:** `/api/health` on the staging backend answers `"database": "ok"`, and one live search from the deployed
  page finishes (`docs/deploy.md` section 5).
- **Cause, why it could not run:** the Render backend and the Vercel frontend are not created (Om, 15 Nov).
  `docs/deploy.md` section 5 records no run, and section 6 records no session.
- **What exists:** `render.yaml` (`shopsense-backend-staging`, free plan, Singapore, health path `/api/health`),
  `frontend/vercel.json`, and the steps in `docs/deploy.md` sections 2 to 5.
- **Steps, once both exist:** run the timed health call, one live search from the deployed page, the memory reading,
  and health again after a spin-down (`docs/deploy.md` section 5).
- **Suggested owners:** Pal (Render) and Yashkumar (Vercel).

## 5. Live-call ledger

The budget was LLM 40, Serper 18, Reddit archive 20, Jina 20 pages and Firecrawl 10 credits.

**Totals: LLM 34, Serper 2, Reddit archive 7, Jina 0, Firecrawl 0. Also 16 review-page reads. No call was refused by
a cap.**

| Run | LLM calls sent | Serper | Reddit archive | Jina | Firecrawl | Review pages |
|---|---|---|---|---|---|---|
| R1: walk, database on, smartphone | 7 (Groq 5, Gemini 1, Mistral 1) | 0 | 1 | 0 | 0 | 9 |
| R2: live end-to-end, database on | 7 (Groq 4, Gemini 2, Mistral 1) | 2 | 6 | 0 | 0 | 7 |
| R3: live end-to-end, database off | 6 (Groq 3, Gemini 2, Mistral 1) | 0 | 0 | 0 | 0 | 0 |
| R4: Reddit-off drill, walked, database off | 7 (Gemini 4, Mistral 2, Groq 1) | 0 | 0 | 0 | 0 | 0 |
| R5: matcher drill, CLI | 7 (Groq 4, Gemini 2, Mistral 1) | 0 | 0 | 0 | 0 | 0 |

**By task and data class, from the app's log:**
- `detect_intent`, private: Groq only.
- `generate_criteria`, private: Groq, in R1. The laptop criteria came from the cache.
- `extract_claims`, Reddit: Groq only.
- `extract_claims`, public: Gemini and Mistral.

No private or Reddit call reached a provider that trains on inputs. R1 took its web searches from the 24-hour research
cache, and R3 to R5 took everything from it. The guard's counts agree with `usage.json`'s changes.

**Files:** [live-call-ledger.txt](sprint-3-check/live-call-ledger.txt), and the per-run evidence files.

## 6. Rows this check created in Supabase

Read back with read-only `select`s on `session_id` and `created_at`, and on `product_aliases`, before and after the
live runs ([c4-supabase.txt](sprint-3-check/c4-supabase.txt)). The counts went from 14 requests, 8 searches and 0
aliases to 16, 10 and 1.

| Table | Key | Created (UTC) | Run |
|---|---|---|---|
| requests | `sess_4f3e848d3bc34225870f86de` | 2026-11-15 10:29:06 | R1 |
| searches | `sess_4f3e848d3bc34225870f86de` | 2026-11-15 10:33:21 | R1 |
| product_aliases | alias `vivo t5x` → `prd_vivo_t5x_8gb_ram`, smartphone | 2026-11-15 10:33:21 | R1 |
| requests | `sess_43c7b936aa0a7a0a422a8334` | 2026-11-15 10:36:38 | R2 |
| searches | `sess_43c7b936aa0a7a0a422a8334` | 2026-11-15 10:37:09 | R2 |

Some runs had the database off and saved nothing:
- R3: `sess_9c118d93d80f6c57fa6842c5`
- R4: `sess_e55ee3807e48c81782e09c4b`
- R5: the CLI's session, not printed with `--no-db`.

These statements are for Om to review and run. They were not run here. The alias row is product data, not the shopper's
words. It is listed because it points the family name at one variant (section 7).

```sql
delete from public.searches where session_id in ('sess_4f3e848d3bc34225870f86de', 'sess_43c7b936aa0a7a0a422a8334');
delete from public.requests where session_id in ('sess_4f3e848d3bc34225870f86de', 'sess_43c7b936aa0a7a0a422a8334');
-- optional:
delete from public.product_aliases where alias = 'vivo t5x' and product_id = 'prd_vivo_t5x_8gb_ram';
```

## 7. Observations (not counted)

1. **Live scores are thin.**
   - Two of the four live searches scored nothing (R1, R2), under this check's cap of 4 sources a search.
   - In R3 and R4 every ranked product scored 88. One product had a battery rating, and the others took it as their
     estimate (DR-09).
   - So "Best match" against "Runner-up" was decided by order among equal scores, not by a gap. Decide doesn't mark
     ties yet (`tied_with` is always empty: ties and the separating criterion are Sprint 4's confidence work, US-19),
     so the page showed no "Too close to call".
   - Vaishvi and Jhanvi.
2. **The facts that decided a product are not on its card.**
   - In R1 the Motorola Edge 70 Fusion passed on a sourced price of ₹28,980 and 8 GB RAM.
   - Its card says "No price found yet" and lists all 10 criteria as "Not verified".
   - Yashkumar, with Jenish for prices.
3. **Variants and claims got mixed.**
   - "vivo T5x 8GB RAM" (its variant: 8 GB) was left out for "it has 6 GB". The claim is a 91mobiles list page's line
     for the phone, quoted "6 GB RAM".
   - The alias saved in Supabase points the bare name "vivo t5x" at the 8 GB variant's product ID.
   - "Samsung Galaxy M52 5G 8GB RAM" is "can't tell: We couldn't find its RAM", though its variant says 8 GB.
   - Om (US-15 variants) and Vaishvi (US-17). Evidence: [c4-live-checks.txt](sprint-3-check/c4-live-checks.txt),
     item 5.
4. **Brand and kind names became products.** "Dell", "Lenovo" and "Chromebook" (marked as a kind of product),
   "MacBook" and "t490" sit in "Couldn't check" (R3 and R5; R4, with Reddit off, had none). Om and Rushiraj (US-15),
   Vaishvi.
5. **"no Xiaomi" doesn't catch POCO,** Xiaomi's sub-brand. POCO X7 Pro was "can't tell", and POCO X8 was left out only
   for RAM. Redmi phones were caught because their names start with "Xiaomi". Jenish (US-10).
6. **A generated "Value for Money" shows "Lower is better"** on the criteria page (R1). Decide reads directions only
   from the fixed sets, so the label is cosmetic. But it reads backwards to a shopper. US-03's owner.
7. **R4's extract stage reached its time limit** (two Mistral time-outs and one Gemini time-out). The shopper read
   "Extract took too long, so a simpler fallback was used.", the wording Sprint 2's F-6 was about. Jhanvi.
8. **Proved by tests only this time:**
   - a live Disputed value (US-16 criterion 6, US-21 criterion 3);
   - a Jina read (US-38 criterion 3);
   - a DeepSeek call (S3-1);
   - the relaxation hint (US-17 criterion 4);
   - the model matcher in a normal search (US-15 criterion 2).
9. **The fast suite has tripled since F-1 was accepted.**
   - It went from 64.1 s to 199.9 s on 3.11, with 3,846 tests then and 5,098 now.
   - Nothing else from this check ran during the 3.11 run. Docker Desktop was running.
   - The 3.13 run took 106.3 s. The gap between the two versions is wider than Sprint 2's (64.1 s against 58.4 s),
     and `test_imports.py`'s new Python processes are the largest single part of it.
   - Harshkumar (F-1), or the Product Owner to confirm the acceptance still holds.
10. **S3-1's routes:** the PR says DeepSeek comes "after Gemini and Mistral", but the short public route has no Gemini
    (`test_deepseek.py::test_the_routes_are_the_decided_ones`). Jhanvi, to fix the wording.
11. **Node:** CI's frontend job uses Node 20, and `engines` allows `>=20.19`. P-09 (Node 24 in CI and `engines`) is
    not on `main` yet, so Vercel would build on the newest major. Palak (P-09).
12. **`docs/deploy.md` section 5 still says to switch Reddit off for the smoke search.** Om set the Reddit budget for
    that one search to 14 on 14 Nov. Pal.
13. **The CLI with `--no-db` prints "Saved: no, the database is off (no DATABASE_URL)"** ([c5-matcher.txt](sprint-3-check/c5-matcher.txt)).
    It was `--no-db` that turned it off: the same machine's backend had the database on in R1 and R2. Cosmetic.
14. **A results page opens only in the tab that ran the search.** Opening R2's results address in the walk's browser
    showed "We couldn't find this search", though R2's search was saved and the backend still held it
    ([c4-live-checks.txt](sprint-3-check/c4-live-checks.txt), item 6). That fits D-36, but it means a saved result can't
    be reopened yet.
