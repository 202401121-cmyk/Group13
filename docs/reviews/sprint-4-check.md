# Sprint 4 review check

- **Checked by:** Palak Patel (QA), task #60 (S4-21), on 20 November 2026, 16:50 to 18:20 UTC.
- **Commit:** `main` at **`434384d`** (Merge pull request #153). CI run 44775486511 passed all 6 jobs on it, the
  quality gate among them ([c4-ci-on-main.txt](sprint-4-check/c4-ci-on-main.txt)).
- **When:** every Sprint 4 pull request (#114 to #153) was merged, except #116, a closed test of the pull-request
  tool. None was open ([c4-pull-requests.txt](sprint-4-check/c4-pull-requests.txt)).
- **Scope:**
  - every acceptance criterion of US-04, US-39, US-38 (the YouTube half), US-19, US-20, US-24, US-30, US-31 and
    US-49, in backlog order;
  - Sprint 3's carried gap: does a live search at the normal cap show a ranked product with a score (S4-1, S4-2)?
  - the live drills: every LLM provider off, and the database off;
  - staging health (S4-20);
  - the demo items;
  - CI on `main`, TruffleHog, and AI tool names in Sprint 4's commits and pull requests.
- **Nothing changed:** no application code, test, configuration, workflow, contract or migration was touched. This
  report and its evidence folder, [sprint-4-check/](sprint-4-check/), are the only new files. A failure is reported
  here, never fixed.
- **Machine:** Windows 11, Python 3.11.15 in a new virtual environment, Node 24.22.3 (the version `engines` asks
  for; a first frontend run on Node 22.18 is kept too), Docker with `postgres:17` (17.11).

Each result is PASS, FAIL, BLOCKED or N/A, and names the evidence file that holds the command and its output. No
check was run again to get a pass. The one repeated check is staging's health (C2): a certificate check in curl
failed on this laptop before reaching the server, and the calls before and after are all kept.

## 1. Summary

**Verdict: Not yet: 6 failures (5 FAIL, 1 BLOCKED).**

| Check | PASS | FAIL | BLOCKED | N/A |
|---|---|---|---|---|
| C1 Acceptance criteria (52 rows, with Sprint 3's carried gap) | 47 | 3 | 1 | 1 |
| C2 Live drills and staging (3 rows) | 3 | 0 | 0 | 0 |
| C3 Demo items (5 rows) | 4 | 1, the same failure as S4-F1 | 0 | 0 |
| C4 CI, regression and scans (6 rows) | 4 | 2 | 0 | 0 |
| **Distinct failures** | | **5** | **1** | |

| ID | Failure | Owner |
|---|---|---|
| S4-F1 | US-04 criterion 4 (target): the smart interview is not shorter. The order shipped asks 8.0 questions, the same as the fixed one, at the same accuracy (0.60). The settings that ask 4.49 questions drop accuracy to 0.49. The demo's "shorter interview" can't be shown as shorter for the same reason. D-75 (17 Nov) shipped it like this and moved the product-aware stop to Sprint 5, so the Product Owner may accept it | Om Pandya (US-04 model, #129); Jenish Kavad (interview API, #130) |
| S4-F2 | US-19 criterion 4 (target): rank order is not stable enough on close products. The close brief's median Spearman is 0.8929, under 0.9, and its lowest is 0.7143. The five original briefs score 1.0 | Rushiraj Navar |
| S4-F3 | US-24 criterion 6: no live buy link opens the store's own page. All 6 offers shown live were "<store> via Google Shopping" links. None of their stores is in `backend/config/stores.yaml`, which lists 5 stores. A store not listed "keeps its Google link" by the team's choice (D-67), so the swap to a store's own page is never tried for them | The team's decision on the store list and Google Shopping links (Om Pandya, D-67); then Jenish Kavad |
| S4-F4 | AI tool names: 6 Sprint 4 pull requests (#119, #132, #140, #142, #145 and #150) each carry a review by GitHub's automated pull-request review bot. Its login and its message name an AI coding tool. Commit messages, files, titles, bodies and branch names are clean | Pal Jalodara, who opened all six (the record doesn't show who asked for the reviews) |
| S4-F5 | The mock browser suite on Node 24 (not in CI) had 1 failure: on the questions page, INP was 232 ms under a 4x slower CPU, against the 200 ms budget (D-45). The same test gave 136 ms in this check's first run on Node 22, and 40 to 328 ms in other runs on this laptop that day, so the measure swings with the machine's load | Manu Pardhan (the frontend quality gate, F4) |
| S4-B1 | US-30 criterion 2, the preferences half, is BLOCKED: there is no preferences table to check. #124: "there is no preferences table yet", it comes with #61 in Sprint 5. The past-searches half passes | Vaishvi Mehta (#61, Sprint 5), with US-30's lead Harshkumar Kodavala |

**Sprint 3's carried gap is closed.** With the app's normal settings, the live search (R1) ranked 4 laptops. This
check lowered no cap. Extraction's cap is 40 in `config/extract.yaml`, but `.env` could not be checked for an override
(decision 5). The best match was the ASUS Vivobook S14 Flip: score 90, high confidence, a 95% chance of ranking first,
and a tie flagged below it. Its score rests on one criterion, weight: decide found nothing to compare on the other
eight (section 7, O1 and O2).

## 2. How it was checked

- **A clean environment.** Every suite ran on `main` at `434384d`, from the branch `chore/sprint-4-check` (equal to
  `main` until this report).
  - The backend and ML ran in a new Python 3.11.15 venv: `pip install -e "backend[dev]" -r ml/requirements.txt`.
  - The frontend ran from `npm ci` on Node 24.22.3, as CI does (decision 13).
- **Criteria (C1).**
  - Each criterion is copied word for word from `backlog/stories.yaml`.
  - Each story's backend tests ran together with `-rA`, so every pass is listed. The network guard was on
    (`tests/conftest.py`).
  - The frontend's unit tests and the mock browser suite ran once each. The tests that show each story to the shopper
    are picked out in [c1-frontend.txt](sprint-4-check/c1-frontend.txt).
  - The evaluation commands ran offline: the gate, the floors, the label check, the stability check, and the reply
    samples ([c1-offline-evals.txt](sprint-4-check/c1-offline-evals.txt)). So did the interview simulator
    ([c3-interview-sim.txt](sprint-4-check/c3-interview-sim.txt)).
  - Where a shopper sees a criterion, a live run checked it too.
- **Database tests.** They ran on a throwaway `postgres:17` container with `REQUIRE_DB_TESTS=1`, on port 55432,
  because this machine's Windows PostgreSQL service holds 5432.
- **Live runs (R1 to R3).**
  - **The backend** ran from a launcher kept outside the repository. It:
    - installs the guard `evals/live_briefs.py` uses, so every call to an LLM, Serper, Jina, Firecrawl or a Reddit
      archive is counted where it leaves the process, and refused past the run's cap;
    - counts YouTube calls, which have no budget in this task;
    - keeps the check's budget in one ledger across the runs;
    - writes the app's INFO log.
  - **The frontend** ran with `npm run dev` in live mode, as `scripts/dev.py` starts it.
  - **The walks** were a headless Chromium page driven by Playwright, typing and clicking as a shopper does.
  - **Each run** used the made-up request "Laptop for coding under ₹70,000" and answered High to every question with
    the buttons.
  - **`.env` was never opened, printed or changed.** The app loaded its keys and settings from it as usual. Each run's
    own settings were set in the launcher's process only, where they win over `.env`.
  - **No Reddit text.** Evidence and screenshots hold no Reddit text, quote or username (D-53). The scan is in
    [c2-scan.txt](sprint-4-check/c2-scan.txt), and every screenshot was looked at.

  | Run | What it did | Database | Caps (LLM, Serper, Reddit archive, Jina, Firecrawl) |
  |---|---|---|---|
  | R1 | The live flow at the normal cap. After the first answer the walk typed a question ("what does that mean?"), and after the second an off-topic reply ("who won the cricket match yesterday?") | on | 22, 10, 14, 5, 2 |
  | R2 | The database-off drill, same request | off | 6, 2, 0, 1, 1 |
  | R3 | Every LLM provider off: each provider's key variable set to "" in the launcher's process, a copy of the environment with the keys emptied | off | 0 for all |

### Decisions I took

1. **The live runs ran at 17:19 UTC rather than after the UTC reset.** Serper's day cap is 80 (D-78), and 24 were left
   when the runs began, which is more than this check's 12.
2. **The budget was split by run** (the table above), so the two drills kept room after R1. R2's cap of 6 LLM calls is
   why R2 ranked nothing: 24 extraction calls were refused there, by this check, not by the app.
3. **R1's Serper cap cut enrich and commerce.** Research sent its 6 web searches. Enrich (US-41) sent 4 of its 6 price
   look-ups, and the guard refused 2 ("enrich: 2 of 6 price look-ups failed"). Commerce's 3 look-ups were all refused.
   That is why R1's note says "Commerce used a simpler fallback". It is a limit of this check's budget, not an app
   failure. Live offers were seen in R2 instead, from enrich's store listings.
4. **R3 had every cap at 0,** so with no LLM it could spend nothing paid. It also ran with the database off.
5. **The normal cap.** This check set no `EXTRACT_MAX_SOURCES`, so extraction's cap is `config/extract.yaml`'s 40,
   unless `.env` sets one. Whether `.env` sets one could not be checked, because this check never opens `.env`. The
   repository's guard refused even a look at the setting's name, and it was not tried another way. What R1 shows:
   - research found 57 sources;
   - at least 22 went to extraction and were dropped with no valid answer. That is more than the 4 that Sprint 3's
     check allowed ([sprint-3-check.md](sprint-3-check.md), decision 1) and the 8 of Sprint 3's live floors (#131);
   - the app made 96 LLM attempts in all, across intake, the interview, extraction and the explanation.
6. **Nobody signed in with Google in this check.** Entering an account's password is not something the checker does.
   US-30's sign-in was shown live by hand on 20 Nov (#151 and #152). Login isolation (A cannot read B) is judged on:
   - the API tests on a real Postgres with row-level security;
   - the live project's row-level security policies, read through the Supabase connector
     ([c3-supabase.txt](sprint-4-check/c3-supabase.txt)).
7. **US-39's 70-sample live check was not run again.** It takes about 78 LLM calls, against this check's 30. It is
   judged on its committed record (63 of 70, 16 Nov), and the code-only part ran offline.
8. **The stability check rewrites its record.** So `evals/results/us19-stability.json` was compared after the run and
   then restored. The simulator wrote into a folder outside the repository.
9. **US-38 criterion 3, and the page parts of 4 and 5, are N/A here.** Sprint 3's check passed them; this task names
   the YouTube half only.
10. **The demo's "shorter interview" is the same failure as US-04 criterion 4,** so it is counted once (S4-F1).
11. **The preferences half of US-30 criterion 2 is BLOCKED, not FAIL.** No preferences are stored anywhere yet, so
    there is nothing to check.
12. **The database drill ran with the database off from the start** (`DATABASE_URL` empty, as `scripts/dev.py
    --no-db` does). A database that goes down while a search runs (health "down", the save's time limit, the circuit
    breaker) was not caused live. Tests cover it, for example
    `tests/integration/test_results_in_postgres.py::test_a_locked_table_gives_saved_false_in_time_and_no_row_lands_later`.
13. **The frontend ran twice.** The first run used Node 22.18, while `frontend/package.json` asks for 24.x and CI
    uses 24. So the frontend was run again from `npm ci` on Node 24.22.3, and that run is the one counted
    ([c1-frontend-node24.txt](sprint-4-check/c1-frontend-node24.txt)).

## 3. Results by check

### C1. Acceptance criteria

**US-04 Smart interview that asks only what matters** ([c1-us-04.txt](sprint-4-check/c1-us-04.txt): 129 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Each criterion's weight is a distribution that starts from a default and updates after every answer | PASS | `tests/unit/interview/test_beliefs.py::test_one_answer_updates_the_belief_by_the_normal_normal_rule`, `::test_skipped_and_pending_questions_keep_the_prior`; `test_adaptive.py::test_every_step_says_information_gain_and_carries_the_beliefs_of_its_answers`. Not on screen: the beliefs are in the API response | [c1-us-04.txt](sprint-4-check/c1-us-04.txt) |
| 2 The next question is about the criterion the app is least sure of that also matters most | PASS | `test_adaptive.py::test_the_next_question_is_the_largest_mean_times_sd`, `::test_a_tie_goes_to_the_fixed_order`; `tests/unit/api/test_interview_order_api.py::test_each_smart_question_says_in_plain_words_why_it_comes_now`. Live (R1): the two questions whose page text was captured each said why, for example "Why this question: Memory Size: it usually matters a lot and we don't know your view yet, so we ask now." With D-75's tuned numbers the order is the usual one (#129) | [c1-us-04.txt](sprint-4-check/c1-us-04.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 3 The interview stops when the top criteria are settled, or after 10 questions at most | PASS | `test_adaptive.py::test_the_tuned_numbers_still_stop_once_the_top_3_are_good_enough_answers`, `::test_ten_criteria_end_after_ten_questions_when_nothing_settles`; `test_interview_order_api.py::test_the_shipped_numbers_stop_early_when_the_top_3_are_settled`. Live: 9 questions for the laptop's 9 criteria | [c1-us-04.txt](sprint-4-check/c1-us-04.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 4 On 50 simulated shoppers, it matches the fixed questionnaire's accuracy with 30% fewer questions (target) | **FAIL** (S4-F1) | `python -m evals.interview_sim --planners fixed,smart,d69` (300 shoppers, 50 per category) reproduces the committed records exactly. Fixed order: 8.0 questions, accuracy 0.6. The order shipped: 8.0 questions, accuracy 0.6. D-69's settings: 4.49 questions (44% fewer), accuracy 0.4933. #129: "Criterion 4 is not met." D-75 shipped it so | [c3-interview-sim.txt](sprint-4-check/c3-interview-sim.txt), [c1-us-04.txt](sprint-4-check/c1-us-04.txt) |
| 5 If the model errors, the interview continues in fixed order | PASS | `test_adaptive.py::test_a_planner_error_continues_the_turn_in_the_fixed_order`, `::test_a_planner_error_on_the_first_turn_asks_the_fixed_orders_first_question`; `tests/faults/interview/test_adaptive_fallback.py::test_run_raises_and_the_fixed_fallback_answers` | [c1-us-04.txt](sprint-4-check/c1-us-04.txt) |

**US-39 Understand questions, skips and off-topic replies in the interview**
([c1-us-39.txt](sprint-4-check/c1-us-39.txt): 146 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Each reply is classified as an answer, a question, an answer with a question, a skip, a correction, off-topic, or unclear | PASS | `tests/golden/test_reply_samples.py::test_the_set_is_70_made_up_replies_10_per_type`; `tests/unit/interview/test_reply_parsing.py`. Live: both typed replies were classified (`classify_reply`, 2 calls) | [c1-us-39.txt](sprint-4-check/c1-us-39.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 2 A question gets a short answer, then the same interview question is asked again; the answer comes from the criterion's saved definition first, and the LLM only if the definition doesn't cover it | PASS | `test_replies.py::test_a_question_is_answered_from_the_definition_then_asked_again_with_no_answer_call`, `::test_a_question_the_definition_does_not_cover_gets_one_short_llm_answer`. Live (R1): "what does that mean?" got "Amount of memory lets you keep more programs open without slowing down your computer." from the saved definition, then "Memory Size: how much does it matter to you?" again. The run's one LLM answer call came right after the next reply, the off-topic one | [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt), [c2-r1-question-mid-interview.png](sprint-4-check/c2-r1-question-mid-interview.png) |
| 3 An answer with a question records the answer, then answers the question | PASS | `test_replies.py::test_an_answer_with_a_question_records_the_answer_then_answers_it` | [c1-us-39.txt](sprint-4-check/c1-us-39.txt) |
| 4 A skip leaves that criterion at its default weight; a correction updates the earlier answer | PASS | `test_replies.py::test_a_skip_keeps_the_default_weight_and_moves_on`, `::test_a_correction_changes_the_earlier_answer_and_asks_the_current_question_again`; `test_interview_order_api.py::test_a_correction_of_an_earlier_answer_re_plans_the_rest` | [c1-us-39.txt](sprint-4-check/c1-us-39.txt) |
| 5 Off-topic text gets a one-line redirect back to the current question | PASS | `test_replies.py::test_off_topic_gets_a_one_line_redirect_back_to_the_question`; frontend "redirect: the one line back to the question above it". Live, the off-topic reply was read as a question (section 7, O5). It got one line, "I can only help with questions about the SSD Speed criterion and its definition.", then the same question | [c1-us-39.txt](sprint-4-check/c1-us-39.txt), [c1-frontend.txt](sprint-4-check/c1-frontend.txt), [c2-r1-off-topic-reply.png](sprint-4-check/c2-r1-off-topic-reply.png) |
| 6 An unclear reply gets one rephrased question with quick-reply buttons, never a guess | PASS | `test_replies.py::test_unclear_gets_one_rephrased_question_with_the_buttons`; frontend "a rephrase keeps the same buttons" | [c1-us-39.txt](sprint-4-check/c1-us-39.txt), [c1-frontend.txt](sprint-4-check/c1-frontend.txt) |
| 7 After two unclear or off-topic replies to the same question, the app skips it, keeps the default weight, and marks it "skipped" so the user can answer later | PASS | `test_replies.py::test_the_second_unclear_or_off_topic_reply_skips_and_marks_it_skipped`; `test_stage.py::test_unclear_rephrases_then_gives_up_after_two`; frontend "each has an Answer now button in the answers panel" | [c1-us-39.txt](sprint-4-check/c1-us-39.txt), [c1-frontend.txt](sprint-4-check/c1-frontend.txt) |
| 8 Questions, redirects and skips never count as answers, and the remaining questions continue from where the interview was | PASS | `test_replies.py::test_questions_redirects_and_rephrases_never_count_as_answers`. Live: "Answers so far (1)" and "1 of 9 settled" stayed after the question, and "(2)" and "2 of 9" after the off-topic reply | [c2-r1-question-mid-interview.png](sprint-4-check/c2-r1-question-mid-interview.png), [c2-r1-off-topic-reply.png](sprint-4-check/c2-r1-off-topic-reply.png) |
| 9 Reply text is data; nothing typed in a reply can instruct the AI | PASS | `test_replies.py::test_the_reply_reaches_the_classifier_only_as_data`, `::test_an_injection_can_not_give_a_weight_the_reply_does_not_state_or_touch_an_unasked_question`; `test_reply_privacy.py::test_a_reply_and_its_question_reach_only_providers_that_do_not_train_on_them` | [c1-us-39.txt](sprint-4-check/c1-us-39.txt) |
| 10 On 70 sample replies (10 per type), at least 90% are classified correctly | PASS | The committed live record, `tests/golden/interview/live_record.json` (16 Nov): 63 of 70 (0.90), exactly at the line. By type: off-topic 0.8, unclear 1.0, the others 0.9; 4 timed out and counted wrong. `test_the_live_record_is_of_this_set_and_meets_the_target` passes. Not run live again (decision 7). Code alone, offline: 10 of 70 | [c1-offline-evals.txt](sprint-4-check/c1-offline-evals.txt), [c1-us-39.txt](sprint-4-check/c1-us-39.txt) |
| 11 If the classifier fails, the reply is treated as an answer and the user can correct it | PASS | `test_replies.py::test_a_failed_or_refused_classification_is_an_answer_at_the_default_weight`, `::test_any_classifier_error_is_an_answer_the_shopper_can_change`; `tests/faults/interview/test_reply_faults.py::test_a_provider_that_hangs_ends_in_an_answer_inside_the_turn_never_a_skipped_question` | [c1-us-39.txt](sprint-4-check/c1-us-39.txt) |

**US-38 Add YouTube and hard-to-read pages, the YouTube half** ([c1-us-38.txt](sprint-4-check/c1-us-38.txt): 60 cases
pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 The YouTube Data API finds 3 to 5 relevant review videos per search | PASS | `tests/unit/research/test_youtube.py::test_five_found_make_five_sources_with_one_search_one_videos_call_and_comments_for_each`, `::test_more_than_five_found_keeps_the_first_five`. Live (R1): one search (relevance, region India, at most 5), one videos call for 5 ids, 5 comment calls; research read "youtube (5)" | [c1-us-38.txt](sprint-4-check/c1-us-38.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 2 Transcripts are used only if YouTube's terms allow the chosen method (checked in Sprint 1); otherwise title, description and top comments are used | PASS | `test_youtube.py::test_no_request_ever_asks_for_a_transcript_or_captions`, `::test_videos_and_comments_are_asked_for_their_text_only`; the terms verdict in `docs/api-setup.md` lines 510 to 518 ("no transcripts"). Live: only the search, videos and commentThreads endpoints were called | [c1-us-38.txt](sprint-4-check/c1-us-38.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 3 Pages that fail a simple fetch or need JavaScript are read through Jina Reader | N/A | The page half, passed in Sprint 3's check (decision 9). Live, R1 read 4 pages through Jina | [../sprint-3-check.md](sprint-3-check.md) |
| 4 Video and page text goes through the same extraction and quote rules as every other source | PASS | The video part. `tests/unit/extract/test_youtube_routing.py::test_a_quote_from_the_videos_title_description_or_one_comment_is_kept`, `::test_a_quote_not_in_the_text_or_run_across_two_comments_is_dropped`, `::test_viewers_comments_never_reach_a_provider_that_trains_on_them`. Live: the same rules dropped video claims and names ("claims dropped from a youtube source: {'criterion': 3}", "names dropped …: {'brand_only': 5}") | [c1-us-38.txt](sprint-4-check/c1-us-38.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 5 If YouTube or Jina fails, the search continues without them and the coverage note says so | PASS | The YouTube part. `tests/unit/research/test_youtube_stage.py::test_youtube_failing_leaves_the_search_going_and_the_note_says_so`, `::test_youtube_raising_or_hanging_never_stops_the_search`; `tests/faults/test_outside_failures.py::test_youtube` (timeout, 5xx, 429, bad key). Not caused live | [c1-us-38.txt](sprint-4-check/c1-us-38.txt), [c1-us-31.txt](sprint-4-check/c1-us-31.txt) |

**US-19 Honest confidence and ties from simulations** ([c1-us-19.txt](sprint-4-check/c1-us-19.txt): 99 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 1,000 simulations vary the weights and scores within their uncertainty | PASS | `tests/unit/decide/test_simulation.py::test_weights_stay_at_zero_or_more_and_scores_within_0_and_10`, `::test_an_estimated_score_varies_widest_and_a_confident_one_least`, `::test_a_disputed_stated_value_draws_both_sides_of_its_comparison` | [c1-us-19.txt](sprint-4-check/c1-us-19.txt) |
| 2 Each product shows its chance of being first and a high, medium or low confidence label | PASS | `test_simulation.py::test_a_ranked_result_comes_from_the_simulations`, `::test_confidence_is_high_medium_or_low`; frontend "each card says its chance as a whole percentage and its confidence in words". Live (R1): "95% chance of ranking first" with High confidence, then 2%, 2% and 0% with Low (but see O1) | [c1-us-19.txt](sprint-4-check/c1-us-19.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt), [c2-r1-results.png](sprint-4-check/c2-r1-results.png) |
| 3 Statistically tied products are marked "too close to call", with the criterion that separates them | PASS | `test_simulation.py::test_two_close_products_name_the_criterion_that_separates_them_most`, `::test_identical_products_share_the_first_place_and_are_all_too_close_to_call`; frontend "a tie of three names all three and what separates them". Live (R1): "Too close to call. GIGABYTE G5 MF5 and Lenovo IdeaPad Slim 3 are tied for 2nd. We can't say yet what separates them." Both had only the same estimated score (5 of 10, coverage 0%), so no criterion separated them | [c1-us-19.txt](sprint-4-check/c1-us-19.txt), [c2-r1-results.png](sprint-4-check/c2-r1-results.png) |
| 4 Rank order stays stable under random 10% weight changes (Spearman 0.9 or better, target) | **FAIL** (S4-F2) | `python -m evals.stability`: "median 1.0, lowest 1.0 (target 0.9)" on the five briefs; "close brief: median 0.8929, lowest 0.7143, target met: False". The run gives the committed record exactly. The close brief (D-74) is the only one that can fail (#125) | [c1-offline-evals.txt](sprint-4-check/c1-offline-evals.txt), [c1-us-19.txt](sprint-4-check/c1-us-19.txt) |
| 5 The same inputs and seed always give the same output | PASS | `test_simulation.py::test_the_same_inputs_and_seed_give_the_same_output`; `test_rank.py::test_same_inputs_same_output`; `tests/unit/evals/test_close_brief.py::test_the_same_seed_gives_the_same_numbers`; and the stability run reproducing its record | [c1-us-19.txt](sprint-4-check/c1-us-19.txt), [c1-offline-evals.txt](sprint-4-check/c1-offline-evals.txt) |

**US-20 Explain why a product fits** ([c1-us-20.txt](sprint-4-check/c1-us-20.txt): 127 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Each recommended product gets a short "why this fits you" paragraph with its top 3 criteria by contribution and one trade-off | PASS | `tests/unit/explain/test_facts.py::test_the_top_3_are_the_largest_contributions`, `::test_the_trade_off_is_the_weakest_criterion`; frontend "shows the trade-off on its own line and names what it couldn't verify". Live (R1): all 4 cards had a paragraph (60 to 72 words). The two cards with a known score had "One trade-off: …". The tied pair had none: their only score was an estimate, and "An estimate is never the trade-off" (`app/explain/facts.py` line 171; `test_facts.py::test_an_estimate_is_never_the_trade_off_and_with_nothing_else_there_is_none`). Only one criterion contributed (weight), so no card could name three. The best match's trade-off names that same criterion, its strength, as its weak point (O12) | [c1-us-20.txt](sprint-4-check/c1-us-20.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 2 The contribution numbers come from code; the LLM only writes the words | PASS | `tests/unit/explain/test_stage.py::test_the_facts_reach_the_llm_only_as_data_with_no_claims_words`; `test_templates.py::test_every_number_in_a_template_is_codes_own`. Live: "Weight adds 90 points", the same as the card's "+90 points" | [c1-us-20.txt](sprint-4-check/c1-us-20.txt), [c2-r1-results.png](sprint-4-check/c2-r1-results.png) |
| 3 Anything that could not be verified is named | PASS | `test_facts.py::test_unknown_and_estimated_criteria_are_named_by_weight`; `test_templates.py::test_several_unverified_criteria_are_all_named`. Live: "We could not check its battery life, cpu performance, …" and "Not verified: Battery Life, CPU Speed, …" | [c1-us-20.txt](sprint-4-check/c1-us-20.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 4 The language is plain, at about school Grade 8 | PASS | `test_templates.py::test_every_template_reads_at_grade_9_or_under`; `test_stage.py::test_a_paragraph_too_hard_to_read_is_rewritten_once`. Live: the app's own Flesch-Kincaid function (`app/explain/readability.py`) gives R1's paragraphs Grade 5.8 to 6.3 and its trade-offs 6.1. The code's bar is Grade 9. Some words are technical (O3) | [c1-us-20.txt](sprint-4-check/c1-us-20.txt), [c2-readability.txt](sprint-4-check/c2-readability.txt) |
| 5 If the LLM fails, a template sentence built from the same numbers is shown | PASS | `test_stage.py::test_when_the_llm_fails_every_product_gets_the_template_from_the_same_numbers`; `tests/faults/test_explain_fallback.py::test_an_error_hands_over_to_the_template`. Live: the LLM's paragraph for the best match failed a check and was replaced ("explain: prd_asus_vivobook_s14_flip gets the template: a compared score isn't called compared") | [c1-us-20.txt](sprint-4-check/c1-us-20.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |

**US-24 Current price and buy link for the exact model** ([c1-us-24.txt](sprint-4-check/c1-us-24.txt): 231 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 It finds price, store, availability and link for the top products using Serper shopping results, with retailer lookups run in parallel | PASS | `tests/unit/commerce/test_shopping_stage.py::test_the_top_3_are_looked_up_at_once_and_no_more`, `::test_an_accepted_listing_becomes_an_offer_with_its_price_time_and_variant`, `::test_availability_is_what_the_result_says`. Live: R1's commerce look-ups got no room in this check's budget (decision 3). R2 showed 6 offers from store listings (US-41), each with store, price, time and link | [c1-us-24.txt](sprint-4-check/c1-us-24.txt), [c2-live-r2-db-off.txt](sprint-4-check/c2-live-r2-db-off.txt), [c2-r2-db-off-results.png](sprint-4-check/c2-r2-db-off-results.png) |
| 2 A link is accepted only if its title matches the model name, and the variant where the data allows | PASS | `tests/unit/commerce/test_matching.py::test_the_exact_model_is_accepted`, `::test_anything_else_is_refused`; `tests/contract/test_contracts.py::test_offers_must_match_the_model_name`. Two live offers look doubtful (O6) | [c1-us-24.txt](sprint-4-check/c1-us-24.txt) |
| 3 [DR-04] Every price shows an as-of time, and prices older than 24 hours are labelled "may have changed" | PASS | `test_contracts.py::test_prices_go_stale_after_24_hours`; frontend "a stale offer shows its as-of time and says the price may have changed". Live (R2): "Price as of 10:56 pm" on each offer. None was 24 hours old | [c1-us-24.txt](sprint-4-check/c1-us-24.txt), [c1-frontend.txt](sprint-4-check/c1-frontend.txt), [c2-r2-db-off-results.png](sprint-4-check/c2-r2-db-off-results.png) |
| 4 [DR-01] Offers above the budget are flagged | PASS | `test_shopping_stage.py::test_a_price_above_the_budget_is_flagged`; frontend "an offer above the budget is flagged, and stock is said in words". No live offer was above ₹70,000 | [c1-us-24.txt](sprint-4-check/c1-us-24.txt), [c1-frontend.txt](sprint-4-check/c1-frontend.txt) |
| 5 If no price is found, the card says so and links to a store search | PASS | `test_shopping_stage.py::test_no_accepted_offer_gives_the_store_search`, `::test_serper_down_gives_store_searches_for_all_and_a_coverage_note`. Live (R1): "No store offer" with "Search stores", and the price a source gave ("Price seen in a source: 69990 ₹ on digit.in") | [c1-us-24.txt](sprint-4-check/c1-us-24.txt), [c2-r1-results.png](sprint-4-check/c2-r1-results.png) |
| 6 Links open the store's own page; the app does not check out for the user | **FAIL** (S4-F3) | `tests/unit/commerce/test_store_links.py::test_a_store_page_found_is_swapped_in_with_the_shopping_price_and_time` passes, and nothing checks out. But all 6 live links (R2) were "<store> via Google Shopping", not the store's own page. None of the six stores is in `backend/config/stores.yaml` (Flipkart, Amazon, Croma, Reliance Digital, Vijay Sales), and "A store not listed here keeps its Google link" (D-67). Whether the swap works for a listed store was not seen live, and this check's Serper caps also refused commerce's look-ups in R1 | [c2-live-r2-db-off.txt](sprint-4-check/c2-live-r2-db-off.txt), [c2-r2-db-off-results.png](sprint-4-check/c2-r2-db-off-results.png), [c1-us-24.txt](sprint-4-check/c1-us-24.txt) |

**US-30 Login and private preferences** ([c1-us-30.txt](sprint-4-check/c1-us-30.txt): 94 cases pass;
[c4-db-tests.txt](sprint-4-check/c4-db-tests.txt): 126 database cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Login uses Supabase Auth with Google sign-in | PASS | `tests/unit/auth/test_tokens.py` (the whole file); `tests/unit/api/test_account_routes.py::test_a_signed_in_user_is_named_and_their_profile_is_made_once`, `::test_a_bad_token_answers_401_on_the_user_routes`; frontend "signing in sends the shopper to Google and back to the page they were on". Shown live by hand on 20 Nov (#151: `GET /api/me` 200, `signed_in: true`); the live project holds 1 Google identity, last signed in on 20 Nov. Staging serves `/sign-in` | [c1-us-30.txt](sprint-4-check/c1-us-30.txt), [c1-frontend.txt](sprint-4-check/c1-frontend.txt), [c3-supabase.txt](sprint-4-check/c3-supabase.txt), [c2-staging.txt](sprint-4-check/c2-staging.txt) |
| 2 Preferences and past searches are visible only to their owner, enforced by database row-level security | Past searches: PASS. Preferences: **BLOCKED** (S4-B1) | Database tests: `tests/integration/test_searches_table.py::test_row_level_security_is_on_with_only_the_owners_read_policy`; `test_accounts_in_postgres.py::test_user_a_reads_only_their_own_searches_newest_first`. Live project: row-level security on for all 4 public tables, with owner-only read policies on requests, searches and profiles. There is no preferences table (#124: it comes with #61 in Sprint 5) | [c4-db-tests.txt](sprint-4-check/c4-db-tests.txt), [c3-supabase.txt](sprint-4-check/c3-supabase.txt) |
| 3 Test: user A cannot read user B's data through the API | PASS | `test_account_routes.py::test_user_a_cannot_read_user_bs_searches`, `::test_anyone_else_gets_404_as_for_a_run_that_never_existed`. Database tests: `test_accounts_in_postgres.py::test_through_the_api_a_cannot_read_bs_search`, `test_user_tables.py::test_user_b_cannot_read_user_as_rows`. Frontend "someone else's saved search shows not found" | [c1-us-30.txt](sprint-4-check/c1-us-30.txt), [c4-db-tests.txt](sprint-4-check/c4-db-tests.txt), [c1-frontend.txt](sprint-4-check/c1-frontend.txt) |
| 4 Guests can search without signing in; their requests and searches are stored only under an anonymous session ID, are never linked to an account, and are never used to remember preferences | PASS | `test_account_routes.py::test_a_guest_run_is_never_linked_whatever_the_body_says`, `::test_signing_in_later_never_links_the_guests_earlier_rows`. Database tests: `test_owner_fixed.py::test_a_guests_row_can_never_be_linked_to_an_account[requests]` and `[searches]`. Live (R1): a guest's search, saved as a requests row and a searches row, both with no owner. No preference is remembered for anyone yet | [c1-us-30.txt](sprint-4-check/c1-us-30.txt), [c4-db-tests.txt](sprint-4-check/c4-db-tests.txt), [c3-supabase.txt](sprint-4-check/c3-supabase.txt) |

**US-31 Handle outside failures gracefully** ([c1-us-31.txt](sprint-4-check/c1-us-31.txt): 195 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 Every external call has a timeout from the stage contract table and one retry | PASS | The stage table: `tests/contract/test_contracts.py::test_every_stage_has_a_time_limit` (in c1-us-24.txt). The retry: `tests/unit/llm/test_retry_rule.py::test_a_timeout_a_connection_error_or_a_5xx_is_retried_once_after_a_jitter`; `test_outside_failures.py::test_one_retry_only_however_often_it_fails`. Live (R1): "Jina: timeout; trying once more in 0.2s" and "llm gemini-3.5-flash-lite: error; trying it once more in 0.3s". The official Reddit path (PRAW) retries twice. #119 calls it a deviation, and no decision accepts it; the path is off while `REDDIT_SOURCE` is `archive` (D-53), as here (O13) | [c1-us-31.txt](sprint-4-check/c1-us-31.txt), [c1-us-24.txt](sprint-4-check/c1-us-24.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 2 If one source type fails, results still arrive with a lower-coverage note | PASS | `test_outside_failures.py::test_one_source_type_down_still_gives_results_with_a_note`. Live (R1): "Reddit hit a rate limit." in the note, and results arrived | [c1-us-31.txt](sprint-4-check/c1-us-31.txt), [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt) |
| 3 If every LLM provider fails, the user sees a clear message and a retry button, not a crash | PASS | `test_outside_failures.py::test_every_provider_failing_sets_llm_unavailable_and_says_so_first`, `::test_the_last_provider_failing_ends_in_llm_unavailable_never_a_crash`; frontend "says so plainly, as an alert, and Try again starts the search again". Live drill R3: see C2 | [c2-live-r3-llm-off.txt](sprint-4-check/c2-live-r3-llm-off.txt), [c2-r3-llm-off-after-try-again.png](sprint-4-check/c2-r3-llm-off-after-try-again.png) |
| 4 A site that keeps failing is skipped for 24 hours, then tried again; a rate limit counts less than a block | PASS | `tests/unit/research/test_site_memory.py::test_a_429_counts_half`, `::test_the_skip_ends_after_24_hours_and_the_site_is_tried_again`; `test_outside_failures.py::test_a_429_counts_half_against_its_site`. Not seen live | [c1-us-31.txt](sprint-4-check/c1-us-31.txt) |
| 5 Tests inject failures for each provider and source | PASS | `tests/faults/test_outside_failures.py`: Groq, Gemini, Mistral, DeepSeek and OpenRouter, each with 4 failure kinds; Serper, Serper shopping, YouTube, Firecrawl search and pages, Jina, plain pages, both Reddit archives. Cerebras is on no route, so it has none | [c1-us-31.txt](sprint-4-check/c1-us-31.txt) |

**US-49 Recorded-run replay and the CI quality gate** ([c1-us-49.txt](sprint-4-check/c1-us-49.txt): 195 cases pass)

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| 1 A capped recorder captures 5 real runs (sources, LLM outputs, final results), at most twice a sprint | PASS | `tests/unit/test_record_run.py::test_more_than_5_runs_is_refused`, `::test_a_third_session_in_a_sprint_is_refused_before_any_call`, `::test_the_real_log_holds_at_most_two_sessions_a_sprint`. Five recordings in `tests/fixtures/recorded/gate_*`; `evals/recordings_log.json` lists two Sprint 4 sessions | [c1-us-49.txt](sprint-4-check/c1-us-49.txt) |
| 2 CI replays them offline on every pull request - at least 90% of expected products survive extraction, every claim has a quote, and no product lacks a source | PASS | `python -m evals.gate`: expected survival 0.9922, claims without a quote 0, shown without a source 0. CI's `quality-gate` job passed on `main`. `test_gate.py::test_a_replay_calls_out_nowhere`. The base is 128 products, not 144: 4 of 23 labelled pages got no answer (#149) | [c1-offline-evals.txt](sprint-4-check/c1-offline-evals.txt), [c4-ci-on-main.txt](sprint-4-check/c4-ci-on-main.txt) |
| 3 thresholds.yaml holds a floor for every metric; CI fails if a metric drops below its floor or falls more than 10 points from main | PASS | `python -m evals.thresholds --check`: "69 floors, 0 problems". `test_gate.py::test_a_drop_of_more_than_10_points_from_main_fails`, `::test_a_floor_breach_fails`; `test_baselines.py::test_every_measured_value_has_exactly_one_floor`. The 10-point check covers the gate's own measures (#148) | [c1-offline-evals.txt](sprint-4-check/c1-offline-evals.txt), [c1-us-49.txt](sprint-4-check/c1-us-49.txt) |
| 4 A validator recomputes each test scenario's expected winner with the scoring code and fails if a hand label disagrees | PASS | `python -m evals.labels`: "6 briefs checked, 0 disagreements". `test_labels.py::test_a_wrong_label_is_caught` | [c1-offline-evals.txt](sprint-4-check/c1-offline-evals.txt), [c1-us-49.txt](sprint-4-check/c1-us-49.txt) |
| 5 The fast gate finishes in under 3 minutes; the full evaluation runs nightly | PASS | CI's `quality-gate` job on `main`: 29 s from start to end. Locally the gate took 1.3 s. `.github/workflows/nightly.yml` runs at 21:30 UTC daily. `test_workflows.py::test_the_nightly_runs_the_full_evaluation_offline_and_keeps_the_records`. No scheduled run had happened yet (merged that morning), so with Om's go-ahead it was started by hand on `main`: run 44790439296 passed every step in 45 s, from the baselines and the simulator to the gate, the floors and the records' upload | [c4-ci-on-main.txt](sprint-4-check/c4-ci-on-main.txt), [c1-offline-evals.txt](sprint-4-check/c1-offline-evals.txt), [c4-nightly.txt](sprint-4-check/c4-nightly.txt) |

**Sprint 3's carried gap (S4-1, S4-2)**

| Check | Result | Proved by | File |
|---|---|---|---|
| A live search at the normal cap shows a ranked product with a score | PASS | R1, database on, with the app's normal settings: this check lowered no cap, and config's cap is 40 (`.env` not checked, decision 5). "Results ready. 4 options, best match ASUS Vivobook S14 Flip." Score 90, High confidence, 95% chance of ranking first; then GIGABYTE G5 MF5 and Lenovo IdeaPad Slim 3 (50, tied), and HP Victus 15 (10). 18 couldn't be checked and 9 were left out. The scores rest on weight alone (O1, O2). #131 ranked none of its 5 briefs | [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt), [c2-r1-results.png](sprint-4-check/c2-r1-results.png) |

### C2. Live drills and staging

| Drill | Result | Detail | File |
|---|---|---|---|
| Every LLM provider off: a cached result, or the clear message with a retry, never a crash | PASS | R3. The launcher loaded the app's LLM client: "LLM providers with a key in this process: []". Intake fell back ("Our backup reader filled this in. Please check it."). After research the page said "We couldn't reach the AI services that read the sources, so this search found nothing. Please try again." with Try again. Pressing it gave the same message, with no crash and no console error. No cached result came back: the backup reader dropped "for coding", so the queries differed from R1's | [c2-live-r3-llm-off.txt](sprint-4-check/c2-live-r3-llm-off.txt), [c2-r3-llm-off-understood.png](sprint-4-check/c2-r3-llm-off-understood.png), [c2-r3-llm-off-after-try-again.png](sprint-4-check/c2-r3-llm-off-after-try-again.png) |
| The database off, live: a search finishes and says it was not saved | PASS | R2. Health said `"database": "off"`. The search finished, and the page said "This result isn't saved. It stays at this address for up to 30 minutes, then it's gone." The log said "search not saved: the database is off". Supabase holds no row for the session (checked at 17:50 UTC). The database was off from the start (decision 12) | [c2-live-r2-db-off.txt](sprint-4-check/c2-live-r2-db-off.txt), [c2-r2-db-off-results.png](sprint-4-check/c2-r2-db-off-results.png), [c3-supabase.txt](sprint-4-check/c3-supabase.txt) |
| Staging health (S4-20) | PASS | `GET /api/health` answered 200 with `{"status":"ok","schema_version":"1.9.0","database":"ok"}` twice, in 1.1 s and 0.7 s. A first call at 17:02 UTC gave the same body; two calls between them failed inside curl (error 60, a certificate check on this laptop) before reaching the server. CORS: 200 from the Vercel origin; 400 from a made-up one. The deployed frontend serves the Sprint 4 pages (`/sign-in`, `/my-searches`, `/account`), and 404 for an unknown address. The addresses stay within the team (D-65) | [c2-staging.txt](sprint-4-check/c2-staging.txt) |

### C3. Demo items

| Item | Result | Detail | File |
|---|---|---|---|
| The shorter interview against the fixed one, with the simulator's numbers | **FAIL** (S4-F1, counted once) | The simulator's numbers are shown and match the committed records. They show no saving: 8.0 questions in both, accuracy 0.6 in both. The shorter setting (D-69) asks 4.49 but scores 0.49 | [c3-interview-sim.txt](sprint-4-check/c3-interview-sim.txt) |
| A question asked mid-interview is handled | PASS | R1: "what does that mean?" got its definition, then the same question, and was not counted as an answer | [c2-live-r1-db-on.txt](sprint-4-check/c2-live-r1-db-on.txt), [c2-r1-question-mid-interview.png](sprint-4-check/c2-r1-question-mid-interview.png) |
| Ties flagged | PASS | R1: "Too close to call", 2 laptops tied for 2nd. The mock browser suite shows a tie of three, with what separates them | [c2-r1-results.png](sprint-4-check/c2-r1-results.png), [c1-frontend.txt](sprint-4-check/c1-frontend.txt) |
| Explanations | PASS | R1: every card has a "why it ranks here" paragraph and "Not verified". The two cards with a known score have a trade-off; the tied pair has none, by design, as their only score is an estimate. All four were the template (O4) | [c2-r1-results.png](sprint-4-check/c2-r1-results.png) |
| Login isolation (A cannot read B) | PASS | US-30 criterion 3: API tests on a real Postgres, and the live project's owner-only policies | [c4-db-tests.txt](sprint-4-check/c4-db-tests.txt), [c3-supabase.txt](sprint-4-check/c3-supabase.txt) |

### C4. CI, regression and scans

| Check | Result | Detail | File |
|---|---|---|---|
| CI on `main` (`434384d`): every job, the quality gate among them | PASS | secrets, frontend, backend-windows, backend, quality-gate, ml: all success | [c4-ci-on-main.txt](sprint-4-check/c4-ci-on-main.txt) |
| Database tests, `REQUIRE_DB_TESTS=1`, `postgres:17` | PASS | 126 cases, 0 failures, 0 skipped | [c4-db-tests.txt](sprint-4-check/c4-db-tests.txt) |
| Frontend unit tests, lint and typecheck | PASS | On Node 24.22.3 from `npm ci`: lint and typecheck clean, 822 tests in 39 files pass. The first run on Node 22.18 gave the same | [c1-frontend-node24.txt](sprint-4-check/c1-frontend-node24.txt), [c1-frontend.txt](sprint-4-check/c1-frontend.txt) |
| Frontend browser suite, mock mode (not in CI) | **FAIL** (S4-F5) | On Node 24.22.3: 220 passed, 1 failed, 4 skipped. The failure is the questions page's INP under a 4x slower CPU: 232 ms, budget 200. On Node 22.18: 221 passed, 4 skipped, 0 failed (INP 136 ms) | [c1-frontend-node24.txt](sprint-4-check/c1-frontend-node24.txt), [c1-frontend.txt](sprint-4-check/c1-frontend.txt) |
| TruffleHog | PASS | `main`'s full history: 0 verified, 0 unverified. Across every ref in this clone it finds 4 unverified hits: Sprint 1's made-up Postgres URLs on four old branches that exist only in this clone and were never on `main` | [c4-trufflehog.txt](sprint-4-check/c4-trufflehog.txt) |
| No AI tool names in any Sprint 4 commit or pull request | **FAIL** (S4-F4) | 78 commits: messages, authors and committers clean. Lines added: 238 matches, none a tool. They are one maker's name inside model ids and paths of services the app calls (allowed), and page text in a recording. Pull requests #114 to #153: titles, bodies, branches and comments clean; 6 reviews by the bot name a tool | [c4-ai-names.txt](sprint-4-check/c4-ai-names.txt) |

## 4. Failures

**S4-F1. US-04 criterion 4: the smart interview is not shorter.**
- **Expected:** on simulated shoppers, the fixed questionnaire's accuracy with 30% fewer questions.
- **Found** (`python -m evals.interview_sim --planners fixed,smart,d69`, 300 shoppers, seed 47, offline):

  | Planner | Questions | Accuracy | Weight error |
  |---|---|---|---|
  | Fixed order | 8.0 | 0.600 | 1.023 |
  | Smart, as shipped | 8.0 | 0.600 | 1.023 |
  | D-69's settings | 4.49 | 0.493 | 1.360 |

- **Why it stands:** D-75 (17 Nov) shipped the accuracy-first numbers with this criterion unmet, and moved the
  product-aware stop rule to Sprint 5. If the Product Owner accepts D-75 as it stands, the count drops by one.
- **Gap noted by #129:** the smart interview's accuracy has no floor in `evals/thresholds.yaml`.
- **Owner:** Om Pandya (#129), with Jenish Kavad (#130).

**S4-F2. US-19 criterion 4: rank order is not stable enough on close products.**
- **Expected:** a median Spearman of 0.9 or better under random 10% weight changes.
- **Found** (`python -m evals.stability`): the five original briefs give a median and lowest of 1.0. The close brief
  added by D-74 gives a median of 0.8929 and a lowest of 0.7143, and its top 3 reordered in 87 of 100 changes (#147).
- **Floor:** 0.8429, with the target marked "not met".
- **Owner:** Rushiraj Navar.

**S4-F3. US-24 criterion 6: no live link opens the store's own page.**
- **Expected:** buy links open the store's own page.
- **Found:** R2 showed 6 offers for 2 laptops. All six links were "View at <store> via Google Shopping": GameLoot,
  gadgetsclub.in, Gadgets Now, Desertcart.ae, Aajjo.com and desertcart.in.
- **Why:** Serper shopping gives only Google Shopping links for India (#120). #136 swaps in the store's own page, but
  only for the 5 stores in `backend/config/stores.yaml` (Flipkart, Amazon, Croma, Reliance Digital, Vijay Sales).
  "A store not listed here keeps its Google link. Team's choice" (D-67). None of the six live stores is listed, so no
  swap was tried. Whether it works live for a listed store is still unseen: #136 says "not yet live", and this
  check's Serper caps refused commerce's look-ups in R1.
- **Owner:** the team's decision on the store list and Google Shopping links (Om Pandya, D-67); then Jenish Kavad.

**S4-F4. AI tool names on 6 Sprint 4 pull requests.**
- **Where:**
  - #119 (16 Nov)
  - #132 (17 Nov)
  - #140 (18 Nov)
  - #142 (18 Nov)
  - #145 (18 Nov)
  - #150 (20 Nov)
- **What:** each has a review, state "commented", by GitHub's automated pull-request review bot. The bot's login and
  its message ("… was unable to review this pull request because the user who requested the review has insufficient"
  quota) name an AI coding tool.
- **Who requested it:** the record doesn't show who asked for the reviews. All six pull requests were opened by Pal
  Jalodara.
- **Clean:** commit messages, the lines Sprint 4 added, and every title, body, branch and comment.
- **To do:** stop the automatic review requests, and decide what to do with the six reviews already there.
- **Owner:** Pal Jalodara.

**S4-F5. The questions page's INP went over its budget once.**
- **Expected:** INP of 200 ms or less (D-45). `e2e/quality/vitals.spec.ts:120` measures it while typing and pressing
  quick replies, with Chrome's CPU slowed 4 times.
- **Found:** in the counted run (Node 24.22.3, the full mock suite), 232 ms. Every other test passed.
- **Context:**
  - this check's first run, on Node 22.18, measured 136 ms and passed;
  - earlier the same day on this laptop, during the US-30 redesign (code at or near main's), full suites gave 104,
    120, 328 and 208 ms, and the spec run alone gave 40 ms three times.
  So the measure swings with the machine's load. It was not run again for this report.
- **Owner:** Manu Pardhan (the frontend quality gate, F4): make the measure steady, for example by running the vitals
  spec on its own, or find what slows the page when the machine is busy.

**S4-B1. US-30 criterion 2, the preferences half: BLOCKED.**
- **Why:** there is no preferences table or stored preference to check. #124 says: "there is no preferences table yet";
  it arrives with #61 in Sprint 5.
- **The past-searches half passes:** row-level security on a real Postgres, and on the live project.
- **Owner:** Vaishvi Mehta (#61, Sprint 5), with US-30's lead Harshkumar Kodavala.

## 5. Live-call ledger

| Service | Budget | R1 | R2 | R3 | Used | Refused by this check, never sent |
|---|---|---|---|---|---|---|
| LLM | 30 | 21 | 6 | 0 | **27** | 24 (R2) |
| Serper | 12 | 10 | 2 | 0 | **12** | 15 (R1 5, R2 4, R3 6) |
| Reddit archive | 14 | 14 | 0 | 0 | **14** | 3 |
| Jina | 6 | 4 | 0 | 0 | **4** | 0 |
| Firecrawl | 2 | 0 | 0 | 0 | **0** | 6 (R3) |
| YouTube (no budget) | none | 7 | 0 | 7 | 14 | 0 |

The app's own counters (`backend/.cache/research/usage.json`) moved by the same amounts:
- Serper: 56 to 68.
- Jina: 1 to 5.
- Reddit archive: 0 to 14.
- YouTube searches: 1 to 3.

Serper's day cap is 80 (D-78), so 12 were left. The staging checks called no LLM and no paid search. Details:
[c2-live-call-ledger.txt](sprint-4-check/c2-live-call-ledger.txt).

## 6. Rows this check created in Supabase

| Session | Run | Rows |
|---|---|---|
| `sess_09c8d9254e072f3a0809c7bb` | R1, database on | One in `public.requests` and one in `public.searches`, both a guest's (no owner) |
| `sess_19cf8ac7ab1953c257d2c1f4` | R2, database off | None (checked at 17:50 UTC, [c3-supabase.txt](sprint-4-check/c3-supabase.txt)) |
| `sess_54b6b9a292cf618ea77152d5` | R3, database off | None (checked at 17:50 UTC) |

For Om, to remove R1's rows when the review is done. The check did not run this:

```sql
delete from public.searches where session_id = 'sess_09c8d9254e072f3a0809c7bb';
delete from public.requests where session_id = 'sess_09c8d9254e072f3a0809c7bb';
```

## 7. Observations (not counted)

- **O1. The confidence and coverage look stronger than the evidence.**
  - **What:** R1's best match shows "High confidence", "95% chance of ranking first" and "Data coverage 100%". But it
    says "Checked 3 of 9 criteria", and its score rests on one criterion, weight. Coverage counts only the criteria
    that were scored.
  - **Suggested owners:** Rushiraj Navar (US-19), Vaishvi Mehta (US-18).
- **O2. The note and the card disagree.**
  - **What:** R1's note says "We found no information on … RAM, … battery life … for any product". The best match's
    card shows "Memory Size: 16 GB DDR4 RAM, 512 GB PCIe SSD" and "Battery Life: about eight hours", each with a
    source. These were kept as text and not compared, because only one product had them.
  - **Suggested owners:** Vaishvi Mehta (decide), Jhanvi Barot (extract).
- **O3. The paragraphs use the criteria's long names.**
  - **What:** they say "keyboard tactile feedback", "thermal performance", "cpu" and "ram", in lower case. The cards
    say "Key Comfort", "Heat Management", "CPU Speed" and "Memory Size". "Weight adds 90 points" sits beside the reason
    row "Portability".
  - **Reading grade:** fine (5.8 to 6.3).
  - **Suggested owner:** Jhanvi Barot (US-20).
- **O4. No live paragraph from the LLM was seen.**
  - **What:** the one LLM paragraph (for the best match) failed the check that a compared score is called compared, so
    every card showed the template.
  - **Suggested owner:** Jhanvi Barot.
- **O5. The off-topic reply was read as a question.**
  - **What:** "who won the cricket match yesterday?" was read as a question. That is the same miss the committed
    70-sample record has: off-topic is 0.8 there. The shopper still got one line back to the question, and nothing was
    counted.
  - **Suggested owner:** Jhanvi Barot (US-39).
- **O6. Two live offers look doubtful.**
  - **What:** an "Acer Nitro 5" at ₹23,000 from GameLoot (very low for this model; possibly used or a part), and offers
    from Desertcart.ae, a UAE store, on a search for India.
  - **Suggested owner:** Jenish Kavad (US-24).
- **O7. Provider room.**
  - **What:** in R1, 75 of 96 LLM attempts found no room in the provider pool and were never sent. 22 sources got no
    valid answer.
  - **Suggested owner:** Jhanvi Barot (US-46).
- **O8. The decision register.**
  - **What:** D-69, D-74, D-75, D-78 and D-80 are cited in pull requests, code and docs, but the register copies
    (`.ref/02_DECISION_REGISTER.md` and the team's knowledge set) stop at D-60.
  - **Suggested owner:** Om Pandya.
- **O9. The nightly evaluation has not run on its schedule yet.**
  - **What:** `nightly.yml` merged on 20 Nov. Started by hand, it passed (US-49 criterion 5). Its first scheduled run
    is at 21:30 UTC that day: check that it runs on its own.
  - **Suggested owner:** Harsh Kodavala (US-49).
- **O10. Signing in and My searches were not exercised live by this check** (decision 6).
  - **What:** #152's re-run is still pending: one search with a longer save limit (`DB_TIMEOUT_SECONDS=15`), so a
    finished search shows in My searches. So is the Supabase redirect allow-list for `http://localhost:3000/**`.
  - **Suggested owner:** Om Pandya.
- **O12. The best match's trade-off names its strength.**
  - **What:** "One trade-off: Its weak point is weight, with a score of 9 out of 10". Weight is the only scored
    criterion and the reason it ranks first, and the same card shows "Weakest: Value". With one scored criterion,
    the weakest and the strongest are the same.
  - **Suggested owner:** Jhanvi Barot (US-20).
- **O13. The official Reddit path retries twice.**
  - **What:** PRAW's own retries make two, against US-31's one (#119 calls it a deviation). The path is off while
    `REDDIT_SOURCE` is `archive`.
  - **Suggested owner:** Pal Jalodara (US-31).
- **O11. A shopper has no "Always" memory yet.**
  - **What:** the brief's "Always" note still says leave-outs will be remembered "once you can sign in". Sign-in now
    exists, but the memory is US-06's (Sprint 5).
  - **Suggested owner:** whoever owns US-06.
