# Sprint 1 verification, part E: frontend in mock mode

- **Commit:** `72d0433` (`72d0433773f2e217ec96bd8d51f4466cbd71b6e8`), branch `chore/sprint-1-verification`, equal to `main`.
- **Date:** 4 November 2026, about 17:25 to 18:45 UTC.
- **Machine:** Windows 11 Home Single Language (10.0.26300), Git Bash, Node 22.18.0. Playwright 1.64.1 with Chromium
  156.0.8078.4 (`chromium-1248`), `@axe-core/playwright` 4.14.0 (the version in `package.json`), Lighthouse 13.5.0. The
  machine was running other verification work at the same time (the backend and live-mode checks, and the A5
  `npm run test:e2e` suite in this worktree), so timings and Lighthouse scores are from a busy laptop.
- **What was served, and how:** `npm run build` once in `frontend/` (mock mode, the default: `VITE_API_MODE` and
  `VITE_API_BASE_URL` were not set and there is no `.env.local`). Exit 0 ([build.txt](frontend/e1/build.txt)). Because
  A5 rebuilds `.output/` in this same worktree, the built `.output/` was copied to a scratch folder outside the
  repository and served from there with `PORT=3110 node .output/server/index.mjs`. Only port 3110 was used.
- **How it was driven:** scratch Playwright specs and a scratch config in `frontend/e2e/_s1v_tmp/` (deleted at the
  end), run against that server; a scratch Vitest config in the same folder for the `search_result.json` render;
  Lighthouse from a temporary folder outside the repository. No application code, test, configuration, contract,
  package or lock file was changed. Made-up requests only. `npm run test:e2e` and `e2e/quality` were not run here:
  they are reported under A5.
- **Evidence:** [`frontend/`](frontend/) next to this file. `e1/` walk screenshots and logs, `e2/` state screenshots
  and text, `e5/` axe, keyboard, target, overflow and contrast results, `e6/` Lighthouse numbers, `e7/` copy scan.

## Summary

| Check | PASS | FAIL | BLOCKED | N/A |
| --- | --- | --- | --- | --- |
| E1 Mock walk | 8 | 0 | 0 | 1 |
| E2 Every state renders | 12 | 7 | 0 | 0 |
| E5 Accessibility | 10 | 3 | 0 | 1 |
| E6 Performance | 3 | 2 | 0 | 2 |
| E7 Copy (including the privacy page) | 8 | 2 | 0 | 0 |

The flow works end to end at both widths and in both themes, and the four walks had no console or page errors (the
only console errors in any run were the deliberate 404 and a font host that the machine's DNS briefly could not
reach). The awkward result
states are where the gaps are: ties, estimates, stale prices, no-offer and can't-tell do not yet render as the
decision says, and live mode cannot show both sides of a disputed value. axe is clean once pages are drawn; the
keyboard has two small-screen problems; mobile LCP is still over 2.5 s on every page (open item O9).

## E1: mock walk of the whole flow

Run with `e2e/_s1v_tmp/e1-walk.spec.ts`. Motion was left at the browser default (no reduced motion). Before each
full-page screenshot the page was scrolled to the bottom and back, so on-scroll reveals had run. Steps: 01 home
(request typed in the box), 02 `/research` as it first opens, 03 "Here's what we understood", 04 criteria, 05 first
question, 06 every question answered, 07 brief, 08 research running (one stage ticked), 09 results, and 10
`/research` opened by its address afterwards.

| ID | What was checked | Result | Evidence | Evidence file |
| --- | --- | --- | --- | --- |
| E1.1 | Production build in mock mode | PASS | `npm run build` exit 0; client, SSR and server builds finished | [e1/build.txt](frontend/e1/build.txt) |
| E1.2 | Whole flow at 390 px, light | PASS | All 10 steps reached; results show "Best match" Sample laptop A with the preview label | [e1/390-light/](frontend/e1/390-light/), [walk-390-light.json](frontend/e1/walk-390-light.json) |
| E1.3 | Whole flow at 390 px, dark | PASS | All 10 steps reached | [e1/390-dark/](frontend/e1/390-dark/), [walk-390-dark.json](frontend/e1/walk-390-dark.json) |
| E1.4 | Whole flow at 1440 px, light | PASS | All 10 steps reached | [e1/1440-light/](frontend/e1/1440-light/), [walk-1440-light.json](frontend/e1/walk-1440-light.json) |
| E1.5 | Whole flow at 1440 px, dark | PASS | All 10 steps reached | [e1/1440-dark/](frontend/e1/1440-dark/), [walk-1440-dark.json](frontend/e1/walk-1440-dark.json) |
| E1.6 | Dark mode set with the app's own header toggle, and the screenshots really differ | PASS | The visible "Switch to dark mode" button was clicked on the home page. On every dark step `<html>` has the `dark` class, the stored theme is `dark` and the body background is `oklch(0.245 0.022 158)` (light: `oklch(0.973 0.007 97.4)`). All 20 light and dark screenshot pairs differ (checksums) | `theme` in each walk JSON |
| E1.7 | One JPEG per step, each under 300 KB | PASS | 40 screenshots, 18 to 151 KB, JPEG quality 70. Full page, except step 02, which is the first screen only | e1/*/ |
| E1.8 | Console errors and page errors in the four walks | PASS | 0 console errors, 0 console warnings, 0 page errors, 0 failed requests and 0 HTTP responses of 400 or more | `log` in each walk JSON |
| E1.9 | Console errors and page errors in every other run (E2, E5, E7 specs), recorded | N/A (record) | 0 page errors in any run. Console errors: 12 "Failed to load resource: ... 404" from opening `/no-such-page` on purpose; 68 "Failed to load resource: net::ERR_NAME_NOT_RESOLVED", 1 `ERR_CONNECTION_CLOSED` and 1 `ERR_QUIC_PROTOCOL_ERROR`, all for the Google Fonts stylesheet (72 failed requests) during four of the E5 matrix runs (reduced 1440 dark, 1440 light and 390 light; settled 1440 dark), when the machine's DNS failed for `fonts.googleapis.com`. Each of those failures came with a console warning "Couldn't load preload assets: ProgressEvent" (44), or "... Preload assets timed out" (2). The pages kept working with fallback fonts. The Lighthouse runs loaded the stylesheet and fonts normally (HTTP 200) | `log` in [e5/matrix-*.json](frontend/e5/), [e2/*.json](frontend/e2/), [e7/rupee-lakh.json](frontend/e7/rupee-lakh.json) |

Notes, not failures:

- `/research` opened by its address in the same tab after a finished search shows the last "Here's what we
  understood" again (the page restores its last state from the tab), not an empty request box (step 10).
- The questions page has no `h1`; its question is an `h2`. axe's `page-has-heading-one` is a best-practice rule,
  outside the four WCAG tags asked for, so it is not counted in E5.

## E2: every state renders

Reached through the screens: the brief page's "Preview scenario" picker for the results states, typed answers for
the interview kinds. Run with `e2e/_s1v_tmp/e2-states.spec.ts` at 1440 px, light, reduced motion (so the full-page
screenshots are fully drawn), with research fast-forwarded by the page clock. The shared example
`shared/contracts/examples/search_result.json` cannot be shown by any route in mock mode, so it was put through the
live adapter (`resultFromSearch`) and the real `ResultCard`, rendered in jsdom with a scratch Vitest config (rows
marked "example"). The page clock read 4 Nov 2026, 17:46 UTC during the run.

| ID | What was checked | Where seen | Result | Evidence | Evidence file |
| --- | --- | --- | --- | --- | --- |
| E2.1 | Disputed: both values, each with its source | Scenario "Sources disagree" (`disputed`), Sample laptop A, Memory | PASS | "Sources disagree", then "Sample Store One listing: 16 GB" and "Sample maker spec sheet: 8 GB" | [el-disputed-row.jpg](frontend/e2/el-disputed-row.jpg), [results-states.json](frontend/e2/results-states.json) |
| E2.1x | Disputed, example through the live adapter | Example, Kestrel Slim 15, Battery life | FAIL | Only "Sources disagree"; neither value nor its source. `SearchResult` carries claim IDs only, so the adapter leaves the sources empty | [search-result-example-render.json](frontend/e2/search-result-example-render.json) |
| E2.2 | Unknown: "Not verified", never blank, never 0 | Scenario "Unverified specs" (`unknown`), all three products, Value for money | PASS | "Not verified" on all 3 rows. Across all 12 result scenarios no criterion row is blank or shows 0 | [el-unknown-row.jpg](frontend/e2/el-unknown-row.jpg), results-states.json |
| E2.3 | Estimated: labelled, with the coverage percentage | Scenario "Estimated values" (`estimated`), Sample laptop A, Battery life | FAIL (known follow-up) | "About 9 hours (sample estimate)" with an "Estimated" badge, but no coverage percentage. The card shows "Checked 6 of 6 criteria" (a count, elsewhere on the card). The example through the adapter is the same: Orion Play 15, Build durability, "7.25 / 10" and "Estimated" | [el-estimated-row.jpg](frontend/e2/el-estimated-row.jpg) |
| E2.4 | Tie: "Too close to call" and the separating criterion | Scenario "Tie between products" (`tie`), Sample laptops B and C | FAIL (known follow-up) | Both show "#=2", "Score 79" and "Tied with Sample laptop C" / "...B". No "Too close to call" and no separating criterion anywhere on the page | [el-tie-card-b.jpg](frontend/e2/el-tie-card-b.jpg), [el-tie-card-c.jpg](frontend/e2/el-tie-card-c.jpg) |
| E2.4x | Tie, example through the live adapter, and the "#=1" badge | Example, Nova Book 14 and Kestrel Slim 15 | FAIL (known follow-up) | Both cards show "#=1" and "Tied with ...", the first one under "Best match". The contract gives `separating_criterion: "battery_life"` on both, but the adapter drops it, so nothing names it | search-result-example-render.json |
| E2.5 | Filtered out: the reason | Scenario "Products left out" (`left-out`) | PASS | "Left out (2)": "Sample laptop D — Over your budget at every store checked.", "Sample laptop E — Matches something you asked to leave out." Example: "Aurora Air 13 — ₹84,990 is above your ₹70,000 budget" | [el-left-out-left-out.jpg](frontend/e2/el-left-out-left-out.jpg) |
| E2.6 | Can't tell: its own group, with a link to research it | Scenario "Couldn't check some" (`couldnt-check`) | FAIL | Its own group, "Couldn't check (1)": "Sample laptop F — Store pages didn't list its specs." The group has no link or button (0) to research it. Example: "Vela Pro 14 — We couldn't confirm it has at least 16 GB RAM", same component, no link | [el-couldnt-check.jpg](frontend/e2/el-couldnt-check.jpg) |
| E2.7 | Partial or degraded: `coverage_note` shown prominently | Scenario "Partial results" (`partial`) | PASS | A warning notice at the top, above "Best match": "Sample note: only two of the stores could be checked, so some options may be missing." In the first screen at 1440 px (306 to 326 px from the top) and at 390 px (340 to 380 of 844 px). The example's note is mapped too | [results-partial-top-1440.jpg](frontend/e2/results-partial-top-1440.jpg), [results-partial-top-390.jpg](frontend/e2/results-partial-top-390.jpg) |
| E2.8 | Stale price: older than 24 h says "may have changed" | Scenario "Old prices" (`stale`), Sample laptops A and B | FAIL | "Old price, checked 19 Oct, 7:35 pm", with no "may have changed" anywhere. Also, mock prices are marked old by a fixture flag, not by the 24-hour rule: every other mock price is from 27 Oct (8 days before the run) and shows "Price checked 27 Oct, 3:10 pm" with no warning. Example (adapter, which does apply 24 h): "Old price, checked 2 Oct, 9:30 am" and "Check at Another Store", still no "may have changed" | [el-stale-offers-a.jpg](frontend/e2/el-stale-offers-a.jpg), [el-stale-offers-b.jpg](frontend/e2/el-stale-offers-b.jpg) |
| E2.9 | No offer: a store-search link | Scenario "No store offers" (`no-offer`), Sample laptop B | FAIL (known follow-up) | "Store offers: No price found" and "Value not worked out: No price was found, so value can't be worked out." 0 links in the offers section. Example: Orion Play 15, same | [el-no-offer-b.jpg](frontend/e2/el-no-offer-b.jpg) |
| E2.10 | Not saved: "results won't be saved" | Every mock result (`saved: false`) | PASS | An info notice at the top of every result: "This result isn't saved. It will be gone if you close this page." Same meaning, different words from the decision. The example has `saved: true` and shows no notice, as it should | [el-success-notices.jpg](frontend/e2/el-success-notices.jpg) |
| E2.11 | Interview: question | First question | PASS | "How much do you care about CPU performance?", quick replies 1 Low, 2 Medium, 3 High, "Why this question?" and "Skip this question" | [interview-question.jpg](frontend/e2/interview-question.jpg), [interview-kinds.json](frontend/e2/interview-kinds.json) |
| E2.12 | Interview: answer, then repeat | Typed "Is this about compile speed?" | PASS | "Good question. How quickly it compiles code and runs tools like Docker." and the same question again with its replies | [interview-answer-then-repeat.jpg](frontend/e2/interview-answer-then-repeat.jpg) |
| E2.13 | Interview: rephrase | Typed "not sure" | PASS | "Asked another way: Put simply: would a worse CPU performance bother you?" with "Yes, a lot", "A little", "Not really" | [interview-rephrase.jpg](frontend/e2/interview-rephrase.jpg) |
| E2.14 | Interview: redirect | Typed "what about the price" | PASS | "We'll use the budget from your brief when comparing prices. For now, let's stick to this one." and the same question | [interview-redirect.jpg](frontend/e2/interview-redirect.jpg) |
| E2.15 | Interview: skipped | "Skip this question" on CPU performance | PASS | Next question (memory); "Answers so far" lists "CPU performance: Skipped" | [interview-after-skip.jpg](frontend/e2/interview-after-skip.jpg) |
| E2.16 | Interview: finished | Every question answered | PASS | "Your brief is ready", Answered 4, Skipped 1, Remembered 1, "Review your brief" | [interview-finished.jpg](frontend/e2/interview-finished.jpg) |
| E2.17 | Other result states | Scenarios `relaxation`, `empty`, `network-retry`, `timeout`; reload; a missing search | PASS | Relaxation hint with "Must-have: Budget: Up to ₹70,000" and "Change must-haves"; "No option matched your brief this time."; "We lost the connection while researching. Your brief is safe." then "Try again" works; "Research took too long. Your brief is safe."; after a reload "These results aren't here any more"; "We couldn't find this search" | [results-*.jpg](frontend/e2/), results-states.json |

Known follow-ups, with their status today:

| Follow-up | Status on `72d0433` |
| --- | --- |
| No-offer store link | Not done. "No price found" only, in mock mode and through the adapter (E2.9) |
| Separating criterion for ties | Not done. The contract now carries `separating_criterion` (the example has `battery_life`), but the live adapter drops it and the card has no place for it (E2.4, E2.4x) |
| Coverage percentage next to "estimated" | Not done. Only an "Estimated" badge, in mock mode and through the adapter (E2.3) |
| "#=1" tie badge | Still shown. With the example, both tied products read "#=1", including the one under "Best match". Mock mode can only make a tie at rank 2 ("#=2") (E2.4x) |

## E5: accessibility

Every route and every state reached in E1 and E2 (50 states at 1440 px, 52 at 390 px, which add the open
small-screen menu and the open answers list), at 390 and 1440 px, light and dark, dark set with the header toggle.
Run with `e2e/_s1v_tmp/e5-matrix.spec.ts`, axe tags exactly `wcag2a`, `wcag2aa`, `wcag21aa`, `wcag22aa`, no rule
turned off. Because the app animates (sections fade in when they scroll into view, the results cards rise in, the
dialog zooms in, toasts slide in), axe was run under three declared conditions, and every result is kept:

- **default:** motion on, axe as soon as the state shows;
- **settled:** motion on, the page scrolled to the bottom and back and 0.8 s left for every fade to end, then axe;
- **reduced:** `prefers-reduced-motion: reduce`, the setting the FE-4 quality gate uses, so pages are fully drawn.

No check was repeated to get a pass. In the final run each condition ran once, and every result is reported. The
earlier probe runs that were corrected are described in the notes below.

| ID | What was checked | Result | Evidence | Evidence file |
| --- | --- | --- | --- | --- |
| E5.1 | axe, reduced motion (pages fully drawn): 204 runs, every state at 390 and 1440 px, light and dark | PASS | 0 violations of any impact | [e5/matrix-reduce-*.json](frontend/e5/), [axe-violations.txt](frontend/e5/axe-violations.txt) |
| E5.2 | axe, motion on, settled: the same 204 states after scrolling through and letting every fade end | PASS | 0 violations of any impact | [e5/matrix-settled-*.json](frontend/e5/) |
| E5.3 | axe, motion on, run as soon as each state shows: the same 204 states | FAIL | 52 `color-contrast` (serious) violations, 1,210 nodes, on 12 to 14 states per configuration (table below). axe measured 1.1:1 to 4.3:1 on text that measures 4.5:1 or more once drawn; 59 of the nodes were still at opacity 0 inside a pending reveal when read. None of them appears in E5.1 or E5.2 on the same states | [axe-violations.txt](frontend/e5/axe-violations.txt), [color-contrast-nodes.txt](frontend/e5/color-contrast-nodes.txt) |
| E5.4 | Theme really applied in every state | PASS | `<html>` had the `dark` class exactly in the dark configurations, in all 612 states | [summary.json](frontend/e5/summary.json) (`themeMismatch`) |
| E5.5 | Keyboard-only walk of the whole flow (Tab, Shift+Tab, Enter, Space, Escape, arrow keys), 390 and 1440 px, light and dark | PASS | Home request typed and sent with Enter; "Looks right", "Start the questions" and "Review your brief" with Enter; every question answered with Tab and Space on "High"; "Why this question?" with Enter; the scenario picker with ArrowDown ("tie") and ArrowUp (back to "success"); "Start research" with Space; "Show all criteria" opened with Enter and closed with Space. The h1 gets focus on "what we understood" and on the results. Every page walked forwards and backwards to the end | [e5/keyboard-*.json](frontend/e5/), `keyboard-*-stops.txt` |
| E5.6 | Focus always visible | PASS | 1,652 focus stops (420, 420, 406, 406). 1,612 show the app's 2 px outline. The other 40 are the home request box (16) and the two "Stop research?" buttons (24), which use a ring instead, so they were measured directly: the home request box turns its 1 px border primary (5.12:1 on its background, 3.6:1 against the unfocused border; dark 6.15:1 and 3.6:1) with a 4 px ring at 10% opacity; the "Stop research?" buttons get a 1 px ring. Visible, but the thinnest indicators in the app (note below) | [focus-look-light.json](frontend/e5/focus-look-light.json), [focus-light-request-box-focused.jpg](frontend/e5/focus-light-request-box-focused.jpg) |
| E5.7 | Focus never under a sticky element | FAIL | At 390 px with the small-screen menu open, Shift+Tab back up the home page puts focus on "Start my research", which is completely hidden behind the sticky header and its open menu panel (light and dark). All other 1,650 stops: not covered at all (hit-tested at five points each, after scrolling stopped) | [keyboard-390-light-problem-1.jpg](frontend/e5/keyboard-390-light-problem-1.jpg), [keyboard-390-dark-problem-1.jpg](frontend/e5/keyboard-390-dark-problem-1.jpg) |
| E5.8 | No keyboard trap | PASS | Tab and Shift+Tab leave every page; while "Stop research?" is open, Tab and Shift+Tab stay inside it | keyboard-*.json (`traps` empty) |
| E5.9 | Escape closes every overlay and gives focus back | FAIL | Budget editor: closes, focus back on "Edit budget" (all four). Small-screen menu: closes, focus back on "Open menu". "Stop research?" at 1440 px: closes, research goes on, focus back on the Home link. At 390 px, where the dialog is reached from the menu's Home link: it closes and research goes on, but focus drops to the page body | [keyboard-390-light-after-dialog-escape.jpg](frontend/e5/keyboard-390-light-after-dialog-escape.jpg) |
| E5.10 | Every interactive target at least 44 x 44 px, on every page reached | PASS | 0 targets under 44 px in the 612 matrix states, in the 320 px checks of the 390 configurations, and in the 1,652 keyboard stops. (The dialog buttons are 147.6 x 44 and 125.5 x 44 px once their zoom-in ends) | [small-targets.txt](frontend/e5/small-targets.txt) |
| E5.11 | Token contrast test (`src/test/contrast.test.ts`, part of `npm test`) | PASS | 11 passed | [contrast-token-test.txt](frontend/e5/contrast-token-test.txt) |
| E5.12 | Rendered text contrast at least 4.5:1 (every state at 1440 px, light and dark) | PASS | 19,053 text elements measured (colour composited over the backgrounds behind it). The only text under 4.5:1 is on disabled controls drawn at 40 to 50% opacity ("Edit" and "Add" while another editor is open, "Looks right" while editing, "Start the questions" when criteria failed to load, "Send" with an empty answer box): 1.73:1 to 2.63:1. WCAG 1.4.3 does not apply to inactive controls | [contrast-scan.txt](frontend/e5/contrast-scan.txt) |
| E5.13 | At 320 px nothing scrolls sideways | PASS | No state scrolls sideways at 320, 390 or 1440 px. With motion on, the home hero's decorative wash (`pointer-events: none`) reaches up to 10 px past the edge at 320 px, hidden by the body's `overflow-x: hidden`; with reduced motion it does not | [sideways.txt](frontend/e5/sideways.txt) |
| E5.14 | The existing quality specs (`e2e/quality`) | N/A | Not run here, because A5 runs `npm run test:e2e` in this worktree. See A5 | none |

Every axe violation (E5.3, condition "default"; E5.1 and E5.2 had none). All are `color-contrast`, impact serious:

| State (rule `color-contrast`, impact serious) | Nodes at 390 light | 390 dark | 1440 light | 1440 dark |
| --- | --- | --- | --- | --- |
| results, tie (every section open) | 35 | 34 | 35 | 34 |
| results, disputed (every section open) | 34 | 34 | 34 | 33 |
| results, stale (every section open) | 34 | 34 | 34 | 33 |
| results, no-offer (every section open) | 34 | 34 | 34 | 33 |
| results, success (every section open) | 34 | 33 | 34 | 33 |
| results, unknown (every section open) | 34 | 33 | 34 | 33 |
| results, estimated (every section open) | 34 | 33 | 34 | 11 |
| results, left-out (every section open) | 33 | 33 | 33 | 11 |
| results, couldnt-check (every section open) | 33 | 33 | 11 | 11 |
| home | 16 | 11 | 16 | 16 |
| home, empty request error | 5 | 5 | 5 | 5 |
| brief, research stopped message (toast) | 1 | 1 | 1 | 1 |
| home, menu open | 2 | 2 | not a 1440 state | not a 1440 state |
| results, leave page dialog | 1 | none | 1 | none |

Per configuration: 390 light 14 states (330 nodes), 390 dark 13 (320), 1440 light 13 (306), 1440 dark 12 (254), 1,210
in all. Every other state had none, including the results states with fewer or no product cards (partial, relaxation,
empty).

Notes on E5:

- **How the probes were corrected.** The first keyboard run read positions while the page was still scrolling
  smoothly (`scroll-behavior: smooth`) and ignored stacking order, so it reported many false "off screen" and
  "covered" stops; it is kept as `e5/unsettled-keyboard-*`. A second run read focus after Escape while the page clock
  was paused (the dialog returns focus in a zero-delay timer), and it stopped some forward walks early at the footer
  because "Home" and "ShopSense home" appear twice. The final run waits until scrolling stops, hit-tests five points,
  identifies elements by their place in the page, and resumes the clock before reading focus. The 1440 px light
  default-motion axe run was also done once before the three-condition run, with the same findings.
- **Weak indicators.** The request box and the dialog buttons pass, but on a 1 px change. The FE-4 keyboard spec
  counts any box shadow as an indicator, and the request box always has one (`shadow-soft`), so that spec cannot
  tell whether this box shows focus.
- The questions page has no `h1` (best-practice rule, outside these tags).
- During parts of four matrix runs the Google Fonts stylesheet did not load (the machine's DNS failed, see E1.9), so
  those states were checked with fallback fonts. Colour contrast does not depend on the font; their results match the
  other runs of the same condition.

## E6: performance (open item O9)

Lighthouse 13.5.0 (`npx lighthouse@13.5.0`, installed in a temporary folder outside the repository), with the
Playwright Chromium (`chromium-1248`, `CHROME_PATH`, `--chrome-flags="--headless=new"`), default settings: mobile form
factor, simulated slow 4G and 4x CPU slowdown. Three rounds; in each round the home page, `/research` and the results
address were measured one after the other, so a busy moment hits all three pages alike. The CPU load and the number of
other node and browser processes before each run are in [e6/runs.log](frontend/e6/runs.log). Nothing was changed.

The results screen exists only after research in the same tab, and every Lighthouse CLI run opens a fresh tab. So the
CLI's "results page" is a cold load of a results address (`/s/sess_0123456789abcdef01234567/results`), which shows "We
couldn't find this search". To measure the real results screen, a Lighthouse 13.5.0 user flow (its Node API, same
folder, same Chromium) also ran three times: a search set up through the screens without measuring, then a timespan
from "Start research" to the results, a snapshot of the results screen, and a reload of the results address (the
results are then gone and the brief is still in the tab).

| ID | What was checked | Result | Evidence | Evidence file |
| --- | --- | --- | --- | --- |
| E6.1 | Performance 90 or more (FE-4 target), mobile | FAIL | Medians: home 72, `/research` 79, results address 80; results address reloaded in the tab 87 | [e6/lighthouse-numbers.txt](frontend/e6/lighthouse-numbers.txt) |
| E6.2 | Accessibility 100 | PASS | 100 on every run, every page and every flow step | lighthouse-numbers.txt |
| E6.3 | Best Practices 95 or more | PASS | 100 on every run | lighthouse-numbers.txt |
| E6.4 | LCP 2.5 s or less on mobile (quality budget) | FAIL | Medians: home 4.50 s, `/research` 3.86 s, results address 3.55 s, reload 3.54 s | lighthouse-numbers.txt |
| E6.5 | CLS 0.1 or less | PASS | Home 0.0003, `/research` 0.0001, research to results 0.0007, reload 0. The results address cold load is 0.094 in all three runs, just under the limit: the footer moves when "We couldn't find this search" replaces the loading state | lighthouse-numbers.txt |
| E6.6 | Total Blocking Time | N/A (no target) | Home 0 ms, `/research` 50 ms, results address 12 ms, reload 70 ms; "Start research" to results shown 636 ms (618, 636, 642). In that timespan the largest long task (403 to 449 ms, when the results arrive) is attributed by Lighthouse to the measuring script's frame, so 636 ms is an upper bound for the app | lighthouse-summary.json |
| E6.7 | What delays LCP on the home page | N/A (finding) | LCP equals FCP in all three runs, so the LCP element is painted with the first paint. First paint waits on two render-blocking stylesheets, the Google Fonts CSS and the app's CSS. The typing animation does not delay LCP: TBT is 0 ms and the LCP element is the server-rendered hero paragraph. Details below | [e6/home-lcp-evidence.txt](frontend/e6/home-lcp-evidence.txt) |

## E7: copy

The rendered text of every state in the E5 matrix (all four configurations) was scanned
([e7/copy-scan.json](frontend/e7/copy-scan.json); the 1440 px light text of every state is in
[e7/page-text-1440-light.txt](frontend/e7/page-text-1440-light.txt)). Amounts of a lakh and more, which the mock
results never show, were pushed through the request screen, the budget editor, the brief and the results with
made-up requests ([e7/rupee-lakh.json](frontend/e7/rupee-lakh.json)).

| ID | What was checked | Result | Evidence | Evidence file |
| --- | --- | --- | --- | --- |
| E7.1 | No criterion title lower-cased | PASS | Every title shown as a title or label keeps its case: "CPU performance", "Memory", "Battery life", "Weight", "Display quality", "Value for money" on criteria, brief, answers list and results; "Build quality", "Features" and the rest of the standard list for the TV request. Inside a question sentence titles are lower-cased on purpose, keeping acronyms ("How much do you care about memory?", "Put simply: would a worse CPU performance bother you?"), the same rule as the backend's `in_sentence` (`src/lib/text.ts`). No lower-case "cpu", "tv" or "ram" anywhere. If the decision means titles are never lower-cased even mid-sentence, the questions would fail | copy-scan.json (`lowerCasedTitleLines`) |
| E7.2 | No "undefined", "null", "NaN", "[object Object]", "API", "error 500" or similar | FAIL (known, backlog B5) | Across the 612 rendered states (4 configurations, 3 conditions): no "undefined", "null", "NaN", "[object Object]", "API", "JSON", "payload", "error 500" or other 5xx code, no session ID and no JavaScript error name. The one hit: the not-found page's heading is the error code "404" (above "Page not found") | copy-scan.json (`forbidden`) |
| E7.3 | Rupee amounts use Indian grouping | PASS | Every ₹ amount in every state is grouped the Indian way (₹3,000, ₹30,000, ₹70,000, ₹68,490 to ₹76,990). With larger made-up budgets: "Up to ₹1,50,000" (from "1.5 lakh"), "Up to ₹2,50,000", "Up to ₹1,25,000" (from "125000"); the budget editor regroups 100000 and 1000000 as "1,00,000" and "10,00,000" while typing; the brief and the results' brief show "₹1,00,000 – ₹10,00,000". No amount grouped the Western way | copy-scan.json, rupee-lakh.json, [understood-lakh-budget.jpg](frontend/e7/understood-lakh-budget.jpg) |

Privacy page (`/privacy`, rendered text in [e7/page-text-1440-light.txt](frontend/e7/page-text-1440-light.txt)) against
`docs/database.md`, `supabase/migrations/20261026170303_create_requests.sql`, `backend/app/api/sessions.py` and the
browser code:

| ID | What the page says | Compared with | Result | Evidence |
| --- | --- | --- | --- | --- |
| E7.P1 | The server saves one record per request: what you typed, a session ID, what we understood (once clear enough to research), whether "our AI or our simple keyword rules" worked it out, and when it was saved | `public.requests` columns `request_text`, `session_id`, `intent` (only when ready for research), `intent_source` (`llm` or `rules`), `created_at` | PASS | Matches the table. Note: it is saved only when the database is set and answering; with it off or down nothing is saved (docs/database.md, "off" and "down"), which the page does not say. In mock mode nothing is sent anywhere |
| E7.P2 | "We use these records to learn what people really ask for and to test ShopSense." | "Why" in docs/database.md | PASS | True today. The documented later use, remembering returning shoppers (US-06), is not on the page yet; it must be added before US-06 ships |
| E7.P3 | "Only the ShopSense server can read them." | docs/database.md: row level security and the revoke stop the Data API; "Only the backend's own database login can" | FAIL | Not true as written. The same document records people reading the table: the 3 Nov live check counted rows, and the 4 Nov follow-up read `session_id` and `created_at` and deleted five rows. The project's members (Om and Jenish are in the Supabase organisation) can read it through the dashboard or the database login |
| E7.P4 | No account; never asks for name, email or password; does not save the IP address or browser | "Not stored: no IP address, user agent, name, email or login" | PASS | Matches |
| E7.P5 | Answers are kept in the server's memory, not the database, and deleted 2 hours after the last step | `backend/app/api/sessions.py`: in-memory store, `SESSION_TTL_S = 2 * 60 * 60` | PASS | Matches (the store also keeps at most 1,000 sessions, so one can go sooner) |
| E7.P6 | The request may go to an AI service that does not train on it, never to one that might | Build rule 8 (personal tasks are private; providers that train on inputs get public text only) | PASS | Matches the rule. Whether the backend keeps it is checked in the backend part, not here |
| E7.P7 | Brief and answers are kept in this browser tab and are gone when it closes; the theme choice is kept in this browser | `sessionStorage` in `src/lib/session/store.ts` and `src/routes/research.tsx`; `localStorage` key `shopsense.theme` | PASS | Matches. Results are not kept at all (in memory only), which is consistent |

Other copy notes, not failures: the footer is in title case ("How It Works", "Privacy Policy", backlog B4); the
question counter reads "Question 1 up to 10"; the home badge says "AI-powered shopping research" (backlog B7); "Score
86" has no scale (backlog B10).

## Failures

### F1. Disputed values lose both values in live mode (E2.1x)

- **Expected:** a disputed value shows both values, each with its source (the team's decision, "Every screen must
  render every state"; build rule 4).
- **What happened:** mock mode does this (E2.1). The shared example, put through the live adapter, shows only
  "Sources disagree" for Kestrel Slim 15's battery life: no values, no sources. Its explanation text says "the maker
  says 10 hours, an owner reports about 6", so the information exists in the search but not on the criterion row.
- **Steps:** map `shared/contracts/examples/search_result.json` with `resultFromSearch` and render the `ResultCard`
  for Kestrel Slim 15, or run a live search that has a disputed value and open "Show all criteria".
- **Cause (confirmed):** `SearchResult` carries `supporting_claim_ids` and `conflicting_claim_ids` but not the
  claims (value, quote, source), so `src/lib/results/liveAdapter.ts` leaves `sources` empty on purpose.
- **Story:** US-52 (the results page) and FE-0 (the frontend rule). **Suggested owner:** a contract change for the
  claims, decided by the team (backend: Jenish Kavad), then the adapter and card (frontend: Yashkumar Patel).

### F2. No coverage percentage next to "Estimated" (E2.3), known follow-up

- **Expected:** an estimated value is labelled "estimated", with the data-coverage percentage.
- **What happened:** "About 9 hours (sample estimate)" with an "Estimated" badge and no percentage. The card says
  "Checked 6 of 6 criteria" elsewhere, a count and not next to the label. Same through the adapter ("7.25 / 10",
  "Estimated").
- **Steps:** brief page, "Preview scenario" = "Estimated values", "Start research", open "Show all criteria" on
  Sample laptop A.
- **Cause (confirmed):** `CriterionRow` in `src/components/results/ResultCard.tsx` renders only the badge, and
  `CriterionValue` has no coverage field.
- **Story:** US-52 / FE-0. **Suggested owner:** frontend, Manu Pardhan.

### F3. Ties: no "Too close to call", no separating criterion, "#=1" badge (E2.4, E2.4x), known follow-up

- **Expected:** tied products are marked "Too close to call", with the criterion that separates them (also US-19's
  criterion, Sprint 4).
- **What happened:** mock tie: "#=2" and "Tied with Sample laptop C". Example through the adapter: both products
  "#=1" and "Tied with ...", one of them under "Best match". The contract's `separating_criterion`
  (`battery_life` in the example) is not shown anywhere.
- **Steps:** "Preview scenario" = "Tie between products"; or render the example (E2.4x).
- **Cause (confirmed):** `resultFromSearch` does not read `separating_criterion`, `ProductResult` has no field for
  it, and `ResultCard` prints "Tied with" and `#=rank`. (Open item O1 in the FE-4 report said the contract did not
  carry the criterion; it does now.)
- **Story:** US-52 / FE-0 (full wording US-19). **Suggested owner:** frontend, Yashkumar Patel.

### F4. "Couldn't check" has no link to research it (E2.6)

- **Expected:** "Can't tell" is its own labelled group, with a link to research it.
- **What happened:** the group "Couldn't check (1)" lists "Sample laptop F" and its reason, with no link or button.
  Same for the example's Vela Pro 14.
- **Steps:** "Preview scenario" = "Couldn't check some", open "Couldn't check (1)".
- **Cause (confirmed):** the `couldntCheck` list in `src/routes/s.$sessionId.results.tsx` renders name and reason
  only; `ExcludedProduct` has no link.
- **Story:** US-52 / FE-0. **Suggested owner:** frontend, Manu Pardhan.

### F5. Old prices do not say "may have changed", and mock prices ignore the 24-hour rule (E2.8)

- **Expected:** a price older than 24 hours says "may have changed" (the team's decision; DR-04 in US-24).
- **What happened:** old offers read "Old price, checked 19 Oct, 7:35 pm"; "may have changed" appears nowhere. In
  mock mode the label comes from a fixture flag: offers checked 27 Oct (8 days before this run) show "Price
  checked 27 Oct, 3:10 pm" with no warning in every scenario. Through the adapter (which applies 24 hours) the
  wording is the same "Old price, checked ...".
- **Steps:** "Preview scenario" = "Old prices"; compare with "Ranked products".
- **Cause (confirmed):** `OfferRow` in `ResultCard.tsx` prints "Old price, checked {time}"; `mockService.ts` sets
  `stale` by hand and its `CHECKED` time is fixed at 27 Oct.
- **Story:** US-52 / FE-0 (full criterion DR-04, US-24). **Suggested owner:** frontend, Yashkumar Patel.

### F6. No store-search link when there is no offer (E2.9), known follow-up

- **Expected:** no offer, then a store search link.
- **What happened:** "Store offers: No price found", no link (0 in the section), mock and example.
- **Steps:** "Preview scenario" = "No store offers", Sample laptop B.
- **Cause (confirmed):** the card renders only the badge. The contract does carry a store-search link per product
  (`CommerceResult.no_offer`), and the shared example has one, but the live adapter never reads it and the mock has
  none. (Open item O2 said the contract had no link; it does now. Corrected on 6 November 2026.)
- **Story:** US-52 / FE-0. **Suggested owner:** frontend, Yashkumar Patel.

### F7. axe finds serious contrast failures while content is still fading in (E5.3)

- **Expected:** zero serious or critical axe violations on every state (FE-4 quality gate).
- **What happened:** with motion on and axe run as soon as a state shows, `color-contrast` (serious) on 12 to 14
  states per configuration, 1,210 nodes in all: the results pages with product cards, the home page, the home empty
  error, the open small-screen menu, the "Stop research?" dialog and the "Research stopped" toast. The same states
  have no violation once fades have ended (E5.2) or with reduced motion (E5.1).
- **Steps:** any result scenario with cards, motion not reduced; run axe right after "Results for your laptop"
  appears.
- **Cause (confirmed):** reveal and entrance animations. `.reveal-pending` holds sections at opacity 0 until they
  scroll into view (`src/styles.css`, `useVisualMotion`), and the cards, dialog and toast fade in, so for about half a
  second text sits at partial opacity. axe measured 1.1:1 to 4.3:1 on text that is 4.5:1 or more once drawn. Not a
  barrier once the animation ends, but the gate only tests the reduced-motion case.
- **Story:** FE-4. **Suggested owner:** Manu Pardhan: decide whether the gate also runs axe with motion on after
  settling, and whether content already in the first screen should start visible.

### F8. Focus can sit completely under the open small-screen menu (E5.7)

- **Expected:** focus is never hidden under a sticky element (WCAG 2.4.11).
- **What happened:** at 390 px with the menu open, Shift+Tab back up the home page lands on "Start my research",
  which is fully covered by the sticky header and its open menu panel. Light and dark.
- **Steps:** 390 px, `/`, open the menu, Tab to the end of the page, then Shift+Tab until "Start my research".
- **Cause (suspected):** the menu stays open while focus moves through the page, and `scroll-padding-top: 6rem` is
  shorter than the header with the open menu panel. Closing the menu when focus leaves it would avoid it.
- **Story:** FE-4 (keyboard), on the FE-0 header. **Suggested owner:** Yashkumar Patel.

### F9. After Escape on "Stop research?" at 390 px, focus is lost (E5.9)

- **Expected:** Escape closes every overlay and gives focus back (FE-4).
- **What happened:** at 1440 px focus goes back to the Home link. At 390 px, where the dialog is reached from the
  menu's Home link, the dialog closes and research goes on, but focus drops to the page body.
- **Steps:** 390 px, start research, open the menu, press Enter on "Home", press Escape.
- **Cause (confirmed):** pressing the menu link closes the menu first (`onClick={() => setOpen(false)}` in
  `Navbar.tsx`), so the link is gone. `StopResearchDialog.tsx` only returns focus when `returnTo?.isConnected` and
  has no fallback (for example the "Open menu" button). The FE-4 Escape test runs at 1280 px only.
- **Story:** FE-4. **Suggested owner:** Manu Pardhan.

### F10. Mobile Performance under 90 and LCP over 2.5 s on every page (E6.1, E6.4), open item O9

- **Expected:** Performance 90 or more and LCP 2.5 s or less on mobile.
- **What happened:** Performance medians 72 (home), 79 (`/research`), 80 (results address), 87 (results reload);
  LCP 4.50, 3.86, 3.55 and 3.54 s.
- **Steps:** build in mock mode, serve, then `npx lighthouse@13.5.0 <url> --chrome-flags="--headless=new"` with
  default settings.
- **Cause (confirmed by Lighthouse):** LCP equals first paint on the home page, and first paint waits on the
  render-blocking Google Fonts stylesheet (1,059 to 1,331 ms) and the app's CSS (about 154 ms), estimated savings
  2,070 to 2,250 ms (backlog B8). Also 45 KiB of unused JavaScript in the main bundle, and the HTML is sent without
  compression (about 19 KiB). The typing effect does not delay LCP in these runs (TBT 0 ms). Lighthouse warned that
  the machine's CPU was slower than expected (benchmark index 790 to 920), so scores on a quiet machine may be
  higher.
- **Story:** FE-4 (O9). **Suggested owner:** Manu Pardhan, with Yashkumar Patel for the font loading.

### F11. The not-found page shows the error code "404" as its heading (E7.2), backlog B5

- **Expected:** no error codes or technical words shown to a shopper.
- **What happened:** `/no-such-page` has the `h1` "404", then "Page not found".
- **Steps:** open any unknown address.
- **Cause (confirmed):** `NotFoundComponent` in `src/routes/__root.tsx`.
- **Story:** FE-0. **Suggested owner:** Yashkumar Patel.

### F12. The privacy page says only the server can read saved requests (E7.P3)

- **Expected:** the privacy page says only what is true.
- **What happened:** "Only the ShopSense server can read them." `docs/database.md` itself records people reading
  the table (row counts on 3 Nov; `session_id` and `created_at` read, then rows deleted, on 4 Nov), and the
  project's members can open it in the Supabase dashboard.
- **Steps:** open `/privacy`, section "What we save"; compare with docs/database.md, "Live check" and "Follow-up".
- **Cause (confirmed):** wording. Row level security and the revoke stop the browser keys (the Data API), not the
  project's own database users. Something like "Only the ShopSense server and the project team can read them"
  would be true.
- **Story:** US-01 ("Each request is saved with a session ID"). **Suggested owner:** frontend, Manu Pardhan, with
  Om Pandya to agree the wording.

## E6 numbers

Lighthouse 13.5.0, mobile defaults, medians of three runs (each run is in
[e6/lighthouse-numbers.txt](frontend/e6/lighthouse-numbers.txt)). The results screen itself was measured with the
user flow; its snapshot has no load metrics.

| Page | Performance | Accessibility | Best Practices | LCP | CLS | TBT | LCP element |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/` | 72 (70, 72, 73) | 100 | 100 | 4.50 s | 0.0003 | 0 ms | `p.mt-7`, the hero paragraph "Stop jumping between reviews, product pages and comparison videos…" |
| `/research` | 79 (79, 79, 80) | 100 | 100 | 3.86 s | 0.0001 | 50 ms | `h1` "What are you shopping for?" |
| Results address, cold load (shows "We couldn't find this search") | 80 (80, 79, 81) | 100 | 100 | 3.55 s | 0.094 | 12 ms | `h1` "We couldn't find this search" |
| Results: "Start research" to results shown (user flow, timespan) | 71 (timespan score; 71, 71, 72) | not measured | 100 | none | 0.0007 | 636 ms | none |
| Results screen (user flow, snapshot) | none (snapshot) | 100 | 100 | none | none | none | none |
| Results address reloaded in the tab (shows "These results aren't here any more") | 87 (88, 87, 87) | 100 | 100 | 3.54 s | 0 | 70 ms | not extracted |

For comparison only: the FE-4 report measured the home page at 47 with Lighthouse 12.8.2 on 4 Nov. The tool version
and the machine's load both differ, so the two numbers cannot be compared directly.

**What delays LCP on the home page** ([e6/home-lcp-evidence.txt](frontend/e6/home-lcp-evidence.txt)):

- **LCP element:** `section.relative > div.relative > div.max-w-3xl > p.mt-7`, the hero paragraph ("Stop jumping
  between reviews, product pages and comparison videos. ShopSense re…"), the same in all three runs.
- **LCP equals FCP** in every run (4,595, 4,503 and 4,410 ms), so the element is painted with the first paint, by the
  server-rendered HTML.
- **LCP breakdown** (observed trace): time to first byte 12 to 14 ms, element render delay 2,135 to 2,784 ms. The
  delay is in rendering, not in the server.
- **Render-blocking requests** (estimated savings 2,070 to 2,250 ms): the Google Fonts stylesheet
  (`fonts.googleapis.com/css2?family=Fraunces…&family=Geist…&family=Geist+Mono…&display=swap`, 1,443 bytes, 1,059
  to 1,331 ms) and the app's `styles-DdlihkKO.css` (9,011 bytes, 153 to 155 ms). The font files use `display=swap`
  (font-display insight passes), so it is the stylesheet that blocks, not the fonts.
- **Main-thread work** 3.4 to 3.6 s: Other about 1.1 s, Script Evaluation about 1.05 s, Style & Layout about 1.0 s,
  Rendering about 0.2 s. Main bundle `index-B3Jsvrjg.js` about 1.1 s (0.9 s scripting); 45 KiB of it unused.
- **Long tasks:** 8 to 9, the largest 313 to 321 ms (the document's own task). Total Blocking Time 0 ms in all runs.
- **Conclusion:** on Lighthouse's slow 4G, the home page's LCP is held back by the render-blocking Google Fonts
  stylesheet and the app CSS before the first paint. The letter-by-letter typing does not move LCP here (the hero
  text is drawn by the server first and LCP happens at first paint). Whatever it costs is in the main-thread work
  after first paint (Style & Layout about 1.0 s in total, not split by cause here), not in LCP or TBT.
