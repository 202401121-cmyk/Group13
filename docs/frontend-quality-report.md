# Frontend quality report, Sprint 1 (FE-4)

- **Owner:** Manu Pardhan (frontend engineer and testing)
- **Branch:** `chore/FE-4-quality-gate`, from `main` at `0c19db9`
- **Date:** 4 November 2026
- **What was tested:** the production build (`npm run build`, then `npm start`) in mock mode, on a Windows 11
  laptop with Node 22, Playwright 1.64.1 (Chromium 156) and Lighthouse 12.8.2.
- **How to run the checks again:** from `frontend/`, `npm run test:quality` (quality specs only) or
  `npm run test:e2e` (the flow test and the quality specs). Both build the app and start it on port 3100.

Every number below comes from the specs in `frontend/e2e/quality/` or from the Lighthouse runs described in
section 9. "Before" means `main` at `0c19db9`; "after" means this branch.

## Summary

| #   | Check                   | Target                                                                    | Before                                                             | After                                                                                 | Result                                  |
| --- | ----------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------- | --------------------------------------- |
| 1   | Automated accessibility | axe, WCAG 2.0 to 2.2 A and AA, zero violations, light and dark            | 0 violations in the 46 runs the first version of the specs reached | 0 violations in 124 runs (62 rows, light and dark)                                    | Pass                                    |
| 2   | Keyboard only           | Request to results by keyboard; focus visible, never hidden; Escape works | Focus hidden under the header on 4 screens; Escape failed 3 times  | Flow, focus walks at 3 sizes and Escape all pass                                      | Pass                                    |
| 3   | Screen readers          | VoiceOver, TalkBack, NVDA, by hand                                        | Not done                                                           | Not done                                                                              | Open: by hand, Manu Pardhan             |
| 4   | Screen sizes            | 7 widths and 2 landscape sizes: no overflow, no cut-off text, 44 px       | 26 controls under 44 px, on every page and size                    | No problems at any size                                                               | Pass                                    |
| 5   | Zoom and text spacing   | 200% and 400% zoom reflow; WCAG 1.4.12 text spacing                       | No problems in the 19 screens checked                              | No problems on any screen                                                             | Pass                                    |
| 6   | Motion and display      | Reduced motion, forced colours, dark mode, prefers-contrast               | Forced colours: 5 kinds of field had no focus outline              | Reduced motion, forced colours and dark mode pass                                     | Pass (prefers-contrast: backlog)        |
| 7   | Contrast                | Body text 4.5:1, large text and UI 3:1                                    | Token tests pass                                                   | Token tests pass; lowest text on the home wash 4.65:1 (large heading)                 | Pass                                    |
| 8   | Background              | Pauses off screen and in hidden tabs; works without WebGL                 | Not measured                                                       | Off screen and without WebGL pass; hidden tab can't be checked headless               | Pass; 2 rows open by hand               |
| 9a  | Lighthouse mobile       | Performance 90+, Accessibility 100, Best Practices 95+                    | Performance 46, 48, 62; Accessibility 100; Best Practices 100      | Performance 47, 84, 83; Accessibility 100; Best Practices 100                         | Performance open (O9)                   |
| 9b  | Core Web Vitals (lab)   | LCP 2.5 s or less, CLS 0.1 or less, INP 200 ms or less                    | CLS 0.38 in the questions, 0.31 when results arrive (Lighthouse)   | LCP 0.9 s on a fast network (3.5 to 4.4 s on slow 4G); CLS 0.000 to 0.046; INP 144 ms | CLS, INP pass; LCP on slow 4G open (O9) |
| 9c  | First-load JavaScript   | 170 KB gzipped (the F0 budget)                                            | Results page 175.4 KB                                              | Largest is the brief page, 164.7 KB                                                   | Pass                                    |
| 9d  | Feedback                | A response within 100 ms; a status message after 400 ms                   | "Thinking" status only after 700 ms (never shown in mock mode)     | Longest press to paint 48 ms; status at 400 ms                                        | Pass                                    |
| 10  | Copy                    | Sentence case, one name per action, plain errors, no technical words      | Reviewed by hand                                                   | 7 small issues, all severity 1 or 2                                                   | Backlog for Sprint 2                    |
| 11  | Heuristic review        | Nielsen's 10 and Microsoft's guidelines for human-AI interaction          | Reviewed by hand                                                   | 10 severity 3 issues: 9 fixed, 1 open (O9); 14 of severity 1 or 2 in backlog          | 1 open                                  |
| 12  | Usability test          | 5 people, each task done by 4 of 5, SUS average 80 or more                | Not done                                                           | Not done                                                                              | Open: by hand, Manu Pardhan             |

## Differences from the F4 prompt

The F4 prompt was written for a Next.js app. The frontend was rebuilt on TanStack Start and Vite instead
(`docs/decisions/0001-frontend-tanstack-start.md`), so a few things in the prompt do not exist here:

- **No `/dev/styleguide` and no `npm run shots`.** Neither exists in this app, and FE-4 did not add them. The
  quality specs were written against the real screens instead. There are no before and after screenshots in
  `docs/quality/`: every finding is recorded as a spec result in this report.
- **No WebGL background.** The live, state-aware mesh background from the design direction was not built. The
  only moving background is a slow CSS colour wash behind the home page hero (`bg-ambient` and
  `animate-ambient` in `src/styles.css`). Check 8 was run against that wash.
- **Ports.** `npm run test:e2e` builds the app and serves it on port 3100, as `playwright.config.ts` already
  did. The audit runs and Lighthouse used copies of the builds on ports 3101 and 3102. Ports 3000 and 8000 were
  never used.
- **Routes** are TanStack file routes: `/`, `/research`, `/criteria` (an older link), `/about`, `/contact`,
  `/faq`, `/features`, `/privacy`, and `/s/$sessionId/criteria`, `/questions`, `/brief` and `/results`.

## 1. Audit matrix and axe results

Every row is one route in one state, reached through the screens in mock mode (nothing is faked in storage).
Each row was checked with axe in light and in dark mode, with the tags `wcag2a`, `wcag2aa`, `wcag21a`,
`wcag21aa` and `wcag22aa`. **No axe rule is disabled.** Short loading states were held on screen by pausing
the page clock while axe ran. Spec: `e2e/quality/a11y-matrix.spec.ts`. Results from the final
`npm run test:e2e` run:

| #   | Route              | State                           | Light | Dark |
| --- | ------------------ | ------------------------------- | ----- | ---- |
| 1   | `/`                | default                         | pass  | pass |
| 2   | `/`                | empty request error             | pass  | pass |
| 3   | `/`                | mobile menu open (390 px)       | pass  | pass |
| 4   | `/about`           | default                         | pass  | pass |
| 5   | `/contact`         | default                         | pass  | pass |
| 6   | `/faq`             | default                         | pass  | pass |
| 7   | `/features`        | default                         | pass  | pass |
| 8   | `/privacy`         | default                         | pass  | pass |
| 9   | `/criteria`        | no brief yet                    | pass  | pass |
| 10  | `/no-such-page`    | default                         | pass  | pass |
| 11  | `/research`        | empty                           | pass  | pass |
| 12  | `/research`        | empty request error             | pass  | pass |
| 13  | `/research`        | reading the request             | pass  | pass |
| 14  | `/research`        | understood                      | pass  | pass |
| 15  | `/research`        | editing the budget              | pass  | pass |
| 16  | `/research`        | budget error                    | pass  | pass |
| 17  | `/research`        | editing leave-out list          | pass  | pass |
| 18  | `/research`        | discard edits?                  | pass  | pass |
| 19  | `/research`        | understood by the backup reader | pass  | pass |
| 20  | `/research`        | follow-up question              | pass  | pass |
| 21  | `/research`        | follow-up, something else       | pass  | pass |
| 22  | `/research`        | examples                        | pass  | pass |
| 23  | `/research`        | network error                   | pass  | pass |
| 24  | `/s/$id/criteria`  | loading                         | pass  | pass |
| 25  | `/s/$id/criteria`  | ready                           | pass  | pass |
| 26  | `/s/$id/criteria`  | standard list                   | pass  | pass |
| 27  | `/s/$id/criteria`  | could not load                  | pass  | pass |
| 28  | `/s/$id/criteria`  | search not found                | pass  | pass |
| 29  | `/s/$id/questions` | first question                  | pass  | pass |
| 30  | `/s/$id/questions` | why this question open          | pass  | pass |
| 31  | `/s/$id/questions` | answer then repeat              | pass  | pass |
| 32  | `/s/$id/questions` | asked another way               | pass  | pass |
| 33  | `/s/$id/questions` | redirect                        | pass  | pass |
| 34  | `/s/$id/questions` | thinking                        | pass  | pass |
| 35  | `/s/$id/questions` | later questions updated         | pass  | pass |
| 36  | `/s/$id/questions` | finished                        | pass  | pass |
| 37  | `/s/$id/questions` | answers list open (390 px)      | pass  | pass |
| 38  | `/s/$id/questions` | search not found                | pass  | pass |
| 39  | `/s/$id/brief`     | ready                           | pass  | pass |
| 40  | `/s/$id/brief`     | research stopped message        | pass  | pass |
| 41  | `/s/$id/brief`     | part-way through the questions  | pass  | pass |
| 42  | `/s/$id/results`   | success                         | pass  | pass |
| 43  | `/s/$id/results`   | estimated                       | pass  | pass |
| 44  | `/s/$id/results`   | researching                     | pass  | pass |
| 45  | `/s/$id/results`   | brief summary open              | pass  | pass |
| 46  | `/s/$id/results`   | stale                           | pass  | pass |
| 47  | `/s/$id/results`   | leave page dialog               | pass  | pass |
| 48  | `/s/$id/results`   | no-offer                        | pass  | pass |
| 49  | `/s/$id/results`   | tie                             | pass  | pass |
| 50  | `/s/$id/results`   | left-out                        | pass  | pass |
| 51  | `/s/$id/results`   | disputed                        | pass  | pass |
| 52  | `/s/$id/results`   | left-out list open              | pass  | pass |
| 53  | `/s/$id/results`   | couldnt-check                   | pass  | pass |
| 54  | `/s/$id/results`   | network error                   | pass  | pass |
| 55  | `/s/$id/results`   | disputed, all criteria open     | pass  | pass |
| 56  | `/s/$id/results`   | partial                         | pass  | pass |
| 57  | `/s/$id/results`   | unknown                         | pass  | pass |
| 58  | `/s/$id/results`   | research timed out              | pass  | pass |
| 59  | `/s/$id/results`   | relaxation                      | pass  | pass |
| 60  | `/s/$id/results`   | gone after reload               | pass  | pass |
| 61  | `/s/$id/results`   | search not found                | pass  | pass |
| 62  | `/s/$id/results`   | empty                           | pass  | pass |

The other specs cover these rows too:

| Spec                | What it covers                                                                                                                                                                                                            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `keyboard.spec.ts`  | Home to results by keyboard; Tab and Shift+Tab walks on home, privacy, "what we understood", questions (at 3 sizes) and results; Escape on 3 overlays                                                                     |
| `viewport.spec.ts`  | Home, privacy, about, 404, menu open, request, understood, editing the budget, criteria, questions, brief, researching, and results (success, disputed, old prices, no offers, relaxation, every section open) at 9 sizes |
| `zoom.spec.ts`      | Home, privacy, understood, questions, brief and results with every section open, at 200% and 400% zoom and with wider text spacing (1280 and 390 px)                                                                      |
| `motion.spec.ts`    | Home (wash, contrast, typing) and results (entrance) with and without reduced motion; forced colours on home, request, understood and results                                                                             |
| `webgl-off.spec.ts` | The whole flow from home to results with WebGL turned off                                                                                                                                                                 |
| `vitals.spec.ts`    | Home (LCP, CLS); questions (INP, CLS); research to results (CLS); every session page and research on a slow phone (CLS); press to paint; status at 400 ms                                                                 |
| `bundle.spec.ts`    | The first load of every route                                                                                                                                                                                             |

**Not reachable in mock mode:** "Finish the questions first" on the brief page (live mode only: mock mode works
out the weights from the answers so far). It is open item O7.

## 2. Keyboard only

Spec: `e2e/quality/keyboard.spec.ts`.

- **Request to results with keys only:** Tab to the home request box, type, Tab to "Start my research", Enter;
  Tab to "Looks right", Enter; Tab to "Start the questions", Enter; the number key 3 answers each question;
  Tab to "Review your brief", Enter; arrow keys in the scenario picker; Tab to "Start research", Space. The
  results heading gets focus. Pass.
- **Focus visible and never hidden** (WCAG 2.4.7, 2.4.11): every Tab and Shift+Tab stop on home, privacy, "what
  we understood", questions and results, at 1280 x 720, 390 x 844 and 844 x 390. Each stop must show an outline
  or ring, be on screen, and not be covered at all by the sticky header, the fixed answer box or a toast.
  Before: the home request box, "Edit region", "Criteria" and "Answers so far" slid under the header when
  tabbing backwards. After: pass.
- **No keyboard trap:** Tab and Shift+Tab always leave the page. Pass.
- **Escape closes every overlay and gives focus back:** the inline editors on "what we understood", the menu on
  small screens, and the "Stop research?" dialog (Tab also stays inside the dialog while it is open). Before:
  all three failed. After: pass.

## 3. Screen readers (open, by hand)

**Open: by hand, owner Manu Pardhan.** These need real devices and were not done. Record pass or fail per
screen (home, request, what we understood, criteria, questions, brief, researching, results, privacy):

| Reader and browser                   | Device              | Result |
| ------------------------------------ | ------------------- | ------ |
| VoiceOver with Safari                | iPhone              | Open   |
| TalkBack with Chrome                 | Android phone       | Open   |
| NVDA with Firefox or Chrome (if any) | Windows team laptop | Open   |

What to check on each screen: the page title is read; there is one heading level 1; the landmarks (banner,
navigation, main, contentinfo) are listed; the interview announces each new question and "Thinking about your
answer"; "Results ready. 3 options, best match ..." is read when results arrive; badges are read as words
("Sources disagree", "Not verified", "Estimated"); form errors are read out as soon as they appear (FE-4 added
`role="alert"` for this; a real screen reader still has to confirm it).

## 4. Screen sizes

Spec: `e2e/quality/viewport.spec.ts`. Sizes: 320 x 640, 360 x 740, 390 x 844, 768 x 1024, 1024 x 1366,
1440 x 900, 1920 x 1080, 844 x 390 (390 in landscape) and 1024 x 768 (768 in landscape). Each state is reached
once and then resized, so every size sees the same state.

- **No sideways overflow:** the page width and every visible element are checked against the viewport edge
  (`body` has `overflow-x: hidden`, so a page can look fine while text is cut off at the edge). Pass before and
  after.
- **No cut-off text:** every box that hides overflow is checked for text wider or taller than the box. Pass
  before and after.
- **Touch targets at least 44 x 44 px** (WCAG 2.5.8 asks for 24 px; ShopSense asks for 44). Links inside a
  sentence are left out, as WCAG allows. Before: 26 controls were smaller (the header links, theme switch, menu
  button and logo link; every footer link; the example chips; Edit and Add on "what we understood"; Save,
  Cancel, "Get started", "Change my request" and "Go home"), so no page passed at any size. After: pass.
- **Sticky elements never cover the focused element:** see section 2.

## 5. Zoom and text spacing

Spec: `e2e/quality/zoom.spec.ts`. Browser zoom changes the CSS viewport and the pixel ratio, so it is emulated
that way: 200% of 1280 x 720 is 640 x 360 (WCAG 1.4.4), and 400% of 1280 x 1024 is 320 x 256 (WCAG 1.4.10
reflow). Text spacing (WCAG 1.4.12) injects line height 1.5, paragraph spacing 2em, letter spacing 0.12em and
word spacing 0.16em, at 1280 and 390 px wide. Screens: home, privacy, what we understood, questions, brief,
and results with every section open. Result: no sideways scrolling and no cut-off text anywhere.

At 400% (320 x 256) the sticky header and, on the questions page, the fixed answer box take up much of the
screen. WCAG allows this, but it is tight: backlog B13.

## 6. Motion and display preferences

Specs: `e2e/quality/motion.spec.ts`, `e2e/quality/webgl-off.spec.ts`.

- **Reduced motion:** the home wash has no animation, typed text is complete at once, every section that fades
  in is fully visible, no endless animation runs, and the results appear without movement. Pass.
- **Motion allowed:** the wash runs while the hero is on screen and pauses when it scrolls away. Pass.
- **Forced colours** (`forced-colors: active`): every Tab stop on home, request, what we understood and results
  keeps a real outline, filled buttons keep an edge, and badges keep visible text. Before: the request boxes,
  the edit fields, the "Something else" field and the question card only had a box-shadow ring, which forced
  colours removes, and filled buttons lost their edge. After: pass.
- **Dark mode:** every matrix row in section 1 was checked in dark mode. Pass.
- **prefers-contrast: more:** the app has no styles for it. Severity 1, backlog B12.

## 7. Contrast

- **Token tests** (`src/test/contrast.test.ts`, part of `npm test`): muted text is at least 4.5:1 on every
  surface, in light and dark mode. Pass.
- **Text on the moving wash:** all hero text is hidden, the area behind each text block is photographed at the
  wash's first and last animation frame, and the text colour is compared with the darkest and the lightest
  pixel. The lowest of the two ratios:

| Text                              | Needs | Light, first frame | Light, last frame | Dark, first frame | Dark, last frame |
| --------------------------------- | ----- | ------------------ | ----------------- | ----------------- | ---------------- |
| Heading "Find the right product," | 3:1   | 10.61:1            | 10.71:1           | 13.58:1           | 13.58:1          |
| Heading accent "with evidence."   | 3:1   | 4.65:1             | 4.65:1            | 6.91:1            | 6.91:1           |
| Intro paragraph                   | 4.5:1 | 5.90:1             | 5.90:1            | 6.98:1            | 6.98:1           |
| "See how it works" link           | 4.5:1 | 10.83:1            | 10.83:1           | 13.61:1           | 13.61:1          |

Every other screen has solid backgrounds, which axe's colour-contrast rule checks in every matrix row.

## 8. The background

- **Pauses off screen:** pass (section 6).
- **Pauses when the tab is hidden:** headless Chromium keeps background pages visible, so this can't be checked
  here (the spec skips itself and says why). The wash is CSS only, and browsers stop drawing hidden tabs.
  Open: check by hand on a phone, owner Manu Pardhan.
- **Typing stays smooth:** no key press is lost; at normal CPU speed the page drew 36 to 47 frames a second over four runs (at least 30 is required), with no frame longer than 50 ms. With a 4x slower CPU the figure varied too much between runs to compare (44 to 80 frames a second). The wash is never on screen at the same time as a
  text box, so it can't slow typing.
- **WebGL turned off** (`--disable-webgl --disable-webgl2 --disable-3d-apis`): WebGL is really off, the wash
  still shows (it is a CSS gradient), and the whole flow from home to results runs with no errors. Pass.
- **Mid-range Android phone over USB debugging:** open, by hand, owner Manu Pardhan. Record the phone model,
  CPU use in Chrome DevTools during a 2-minute research wait, and whether the phone gets warm or the battery
  drops noticeably.

## 9. Speed

### Lighthouse (mobile)

Lighthouse 12.8.2 with its default mobile settings (emulated Moto G Power, simulated slow 4G, 4x slower CPU),
run through its Node API with Playwright's Chromium 156 against the production build in mock mode. The
questions page was opened with a session in the tab, the way a shopper reaches it. The results screen only
exists after research in the same tab, so it was measured two ways: a cold load of its address (it then shows
"These results aren't here any more", as a reload does) and a Lighthouse user flow that starts research on the
brief page and takes a snapshot once the results are shown (Lighthouse gives no performance score for that).

The laptop was also running other work during the final runs, so the two builds were measured side by side:
`main` at `0c19db9` and this branch, each served by `npm start`, one run of each in turn, three rounds. The
table shows the median of the three.

| Page                             | Build  | Performance | Accessibility | Best Practices | FCP   | LCP   | TBT      | CLS   | INP    |
| -------------------------------- | ------ | ----------- | ------------- | -------------- | ----- | ----- | -------- | ----- | ------ |
| `/`                              | before | 46          | 100           | 100            | 5.4 s | 5.4 s | 833 ms   | 0     | n/a    |
| `/`                              | after  | 47          | 100           | 100            | 4.3 s | 4.4 s | 2,029 ms | 0     | n/a    |
| `/s/[id]/questions`              | before | 48          | 100           | 100            | 5.0 s | 5.3 s | 78 ms    | 0.487 | n/a    |
| `/s/[id]/questions`              | after  | 84          | 100           | 100            | 3.3 s | 3.5 s | 101 ms   | 0     | n/a    |
| `/s/[id]/results`, cold load     | before | 62          | 100           | 100            | 5.4 s | 5.7 s | 223 ms   | 0.095 | n/a    |
| `/s/[id]/results`, cold load     | after  | 83          | 100           | 100            | 3.4 s | 3.6 s | 0 ms     | 0     | n/a    |
| `/s/[id]/results`, results shown | before | n/a         | 100           | 100            | n/a   | n/a   | 895 ms   | 0.308 | 298 ms |
| `/s/[id]/results`, results shown | after  | n/a         | 100           | 100            | n/a   | n/a   | 805 ms   | 0.001 | 193 ms |

Every run: `/` before 40, 57, 46 and after 42, 47, 47; questions before 48, 47, 67 and after 84, 84, 81;
results (cold) before 65, 62, 59 and after 82, 83, 84. On a quiet laptop earlier the same day, `main` scored
66, 67 and 67 (medians of three).

**Result:** Accessibility 100 and Best Practices 100 on every page: pass. Performance is below 90 on every
page, before and after: open item O9. What Lighthouse and the specs point to:

- On `/`, total blocking time is about 2 s. With motion reduced (no typing effect, no reveals), the home page
  uses about 3.0 s of main-thread time in its first 7 seconds on a 4x slower CPU, against about 6.7 s with
  motion on. The letter-by-letter typing effect, which re-renders on every frame, is most of the difference.
  TBT also looks higher after the fix only because the first paint now comes earlier, so more of this work
  falls after it.
- Files that block the first paint: Lighthouse estimates up to about 1.8 s; on the home page about 0.9 s of
  that is the Google Fonts stylesheet, which every page loads.
- About 45 KiB of JavaScript loaded on first paint is not used by the page.

### Core Web Vitals (lab)

Measured with the web-vitals library (6.2.3) in `e2e/quality/vitals.spec.ts` on a 412 px phone viewport, from
the final `npm run test:e2e` run. These are lab numbers from a local server, not field (p75) data.

| Measure | Where                                                                                    | After                                    | Before                              | Target | Result |
| ------- | ---------------------------------------------------------------------------------------- | ---------------------------------------- | ----------------------------------- | ------ | ------ |
| LCP     | Home, 4x slower CPU, local network                                                       | 932 ms                                   | 928 ms                              | 2.5 s  | Pass   |
| CLS     | Home                                                                                     | 0.000                                    | 0.000                               | 0.1    | Pass   |
| INP     | Questions: typing in the answer box and pressing quick replies, 4x slower CPU            | 144 ms (other runs: 104, 112 and 200 ms) | 112 ms                              | 200 ms | Pass   |
| CLS     | Questions, from the first question on                                                    | 0.046                                    | 0.380 for the whole visit           | 0.1    | Pass   |
| CLS     | From "Start research" to the results                                                     | 0.001                                    | 0.308 (Lighthouse)                  | 0.1    | Pass   |
| CLS     | Slow 4G and 4x slower CPU: loading criteria, questions, brief and results; then research | 0.000, 0.000, 0.000, 0.000; 0.001        | up to 0.487 (Lighthouse, questions) | 0.1    | Pass   |

INP varies with how busy the laptop is: four runs gave 104, 112, 200 and 144 ms, so it sits close to the
budget on a 4x slower CPU. LCP passes here because the local network is fast; with Lighthouse's slow 4G it is
3.5 to 4.4 s (see above), which is open item O9.

### First-load JavaScript

What the browser downloads to show each route when its address is opened directly, gzipped at level 9 by the
spec (`e2e/quality/bundle.spec.ts`), against the F0 budget of 170 KB for the home page, used for every route.
The live-mode schema checker (AJV) is not part of any first load in mock mode.

| Route (opened directly) | Files | Raw      | Gzipped, after | Gzipped, before | Budget 170 KB   |
| ----------------------- | ----- | -------- | -------------- | --------------- | --------------- |
| `/`                     | 19    | 455.0 KB | 147.3 KB       | 146.3 KB        | Pass            |
| `/research`             | 23    | 479.4 KB | 155.9 KB       | 154.8 KB        | Pass            |
| `/privacy`              | 15    | 444.4 KB | 143.0 KB       | 142.0 KB        | Pass            |
| `/s/[id]/criteria`      | 22    | 466.3 KB | 152.5 KB       | 151.5 KB        | Pass            |
| `/s/[id]/questions`     | 22    | 476.5 KB | 155.7 KB       | 154.7 KB        | Pass            |
| `/s/[id]/brief`         | 27    | 500.7 KB | 164.7 KB       | 157.7 KB        | Pass            |
| `/s/[id]/results`       | 24    | 496.9 KB | 162.8 KB       | 175.4 KB        | Pass (was over) |

The brief page grew by 7 KB because it now fetches the results page's code in advance (`fe98ecd`); it stays
within the budget. Every other route grew by about 1 KB (the new dialog loader and the small fixes).

Before, the results page loaded the library behind "Stop research?" on every visit (175.4 KB). The dialog now
lives in `src/components/results/StopResearchDialog.tsx` and is fetched while research runs.

### Feedback

- Every press paints a response within 100 ms: the longest input-to-paint in the request and interview flow
  was 48 ms. Pass.
- Waits over 400 ms show a status: "Reading your request" appears at once; "Thinking about your answer"
  appeared only after 700 ms before, and mock replies take 600 ms, so it never showed. It now appears at
  400 ms. Pass.

## 10. Copy

Every string on every screen was read against the copy rules: sentence case, one name for one action, errors
that say what happened and what to do, and no technical words. No screen says "API", "payload", "null" or
"undefined"; the only error code a shopper can see is the "404" heading on the page-not-found screen (B5). The
issues found are small and are in the Sprint 2 backlog (B1 to B7).

## 11. Heuristic review

Severity: 0 not a problem, 1 cosmetic, 2 minor, 3 major (fix now), 4 catastrophic.

| #   | Heuristic or guideline                             | Screen                         | Issue                                                                                                                   | Sev | Fix                              |
| --- | -------------------------------------------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------- | --- | -------------------------------- |
| H1  | Fitts's law; WCAG 2.5.8                            | Every page                     | 26 controls smaller than 44 px (header, footer, chips, Edit and Add, small buttons)                                     | 3   | Fixed: 3632741, 8387547          |
| H2  | Visibility of system status; WCAG 2.4.11           | Home, understood, questions    | Tabbing backwards hid the focused control under the sticky header                                                       | 3   | Fixed: 55ff219                   |
| H3  | User control and freedom                           | Understood, menu, results      | Escape did not close the menu or "Stop research?"; closing an editor or the dialog dropped focus                        | 3   | Fixed: d70f657, f9815f3          |
| H4  | WCAG 2.4.7 and 1.4.11 in forced colours            | Request, understood, questions | Text fields lost their focus indicator; filled buttons lost their edge                                                  | 3   | Fixed: 1f925e7                   |
| H5  | Help users recognise and recover from errors       | Home, request, understood      | Form errors were not read out by screen readers                                                                         | 3   | Fixed: 6bbcfb7                   |
| H6  | Visibility of system status (Doherty threshold)    | Questions                      | "Thinking about your answer" waited 700 ms, longer than the 600 ms mock reply, so it never showed                       | 3   | Fixed: 436c53c                   |
| H7  | Speed budget                                       | Every page                     | Static files were served uncompressed, so first paint took over 5 s on a simulated phone                                | 3   | Fixed: 578139d                   |
| H8  | Speed budget                                       | Results                        | First-load JavaScript 175.4 KB, over the 170 KB budget                                                                  | 3   | Fixed: f9815f3                   |
| H9  | Aesthetic and minimalist design (stable layout)    | Questions, results             | The brief panel and footer jumped as questions changed, results arrived or a session page loaded (CLS 0.31 to 0.49)     | 3   | Fixed: 64aad81, fe98ecd, daf64dd |
| H10 | Visibility of system status (speed)                | Home                           | Lighthouse performance 47 and LCP 4.4 s on slow 4G: the typing effect and the Google Fonts stylesheet                   | 3   | Open: O9                         |
| H11 | Consistency and standards                          | Home                           | The hero text is drawn by the server, then hidden and typed again once the page loads                                   | 2   | Backlog B9                       |
| H12 | Consistency and standards                          | Home, request, results         | One action, several names: "Start researching", "Start my research", "Get started", "Continue"; "Retry" and "Try again" | 2   | Backlog B1                       |
| H13 | Help users recognise and recover from errors       | Request, results               | "Something went wrong" headings don't say what happened                                                                 | 2   | Backlog B2                       |
| H14 | Make clear why the system did what it did (HAX 11) | Results                        | "Score 86" has no scale or meaning; ties say "Tied with ..." but not "Too close to call" and the deciding criterion     | 2   | Backlog B10; tie data: O1        |
| H15 | Provide global controls (HAX 17)                   | Header                         | No one-click switch for background motion (only the system reduced-motion setting)                                      | 2   | Backlog B11                      |
| H16 | Match between system and the real world            | Results                        | "Researching skincares for you": the category is made plural by adding "s"                                              | 2   | Backlog B3                       |
| H17 | Recognition rather than recall                     | Results, no offers             | "No price found" with no store search link to try                                                                       | 2   | O2                               |
| H18 | Visibility of system status (speed)                | Every page                     | Google Fonts stylesheet blocks the first paint                                                                          | 2   | Backlog B8 (part of O9)          |
| H19 | Consistency and standards                          | Footer, home                   | Title case ("How It Works", "Privacy Policy") and labels typed in capitals                                              | 1   | Backlog B4                       |
| H20 | Match between system and the real world            | 404 page                       | The main heading is "404"                                                                                               | 1   | Backlog B5                       |
| H21 | Consistency and standards                          | Results tab title              | "Researching... \| ShopSense" uses a different pattern from "Your results — ShopSense"                                  | 1   | Backlog B6                       |
| H22 | Flexibility and efficiency of use                  | Every page                     | No styles for `prefers-contrast: more`; the wash ignores data saver                                                     | 1   | Backlog B12                      |
| H23 | Flexibility and efficiency of use                  | Questions at 400% zoom         | The sticky header and fixed answer box take much of a 320 x 256 view                                                    | 1   | Backlog B13                      |
| H24 | Match between system and the real world            | Page description               | "AI-powered personalized shopping research" uses US spelling and jargon                                                 | 1   | Backlog B7                       |

## 12. Usability test (open, by hand)

**Open: by hand, owner Manu Pardhan.** Not done: it needs 5 classmates who did not build ShopSense, 15 minutes
each, think-aloud, on their own phones. Notes use P1 to P5, never names, and no recordings are kept.

| Task                                                   | P1   | P2   | P3   | P4   | P5   | Target             |
| ------------------------------------------------------ | ---- | ---- | ---- | ---- | ---- | ------------------ |
| (a) Find a laptop for coding under ₹80,000             | Open | Open | Open | Open | Open | 4 of 5 succeed     |
| (b) Change an earlier answer                           | Open | Open | Open | Open | Open | 4 of 5 succeed     |
| (c) Say which result is best and how sure ShopSense is | Open | Open | Open | Open | Open | 4 of 5 succeed     |
| System Usability Scale score                           | Open | Open | Open | Open | Open | Average 80 or more |

For each person and task, record: success or not, the time taken, where they hesitated, and the SUS score at
the end.

## Fixes made in this branch

| Commit    | What was fixed                                                                                                  |
| --------- | --------------------------------------------------------------------------------------------------------------- |
| `3632741` | Touch targets: every control at least 44 x 44 px                                                                |
| `8387547` | Touch targets: short footer links at least 44 px wide                                                           |
| `55ff219` | Focus never hidden under the sticky header or the fixed answer box (scroll padding)                             |
| `1f925e7` | Forced colours: focus outlines on text fields and the question card, edges on filled buttons                    |
| `6bbcfb7` | Form errors announced to screen readers (`role="alert"`)                                                        |
| `436c53c` | "Thinking about your answer" after 400 ms instead of 700 ms                                                     |
| `578139d` | Static files served compressed (gzip and brotli, built by nitro)                                                |
| `d70f657` | Escape closes the small-screen menu; focus goes back after an inline edit                                       |
| `f9815f3` | "Stop research?": Escape keeps researching, focus goes back, and the dialog is loaded only when needed (bundle) |
| `64aad81` | Layout shift: the questions and results columns are at least one screen tall                                    |
| `fe98ecd` | Layout shift on slow loads: one-screen loading block, results page code fetched from the brief page             |
| `daf64dd` | INP: the brief page paints the busy button before opening the results page                                      |

The specs themselves are in `295e2a7`, `0b701c6`, `2443d1c`, `842cfb6`, `81d4ab4`, `93c92e7` and `9a71323`.

## Open issues with owners

| #   | Issue                                                                                                                                                                                                                                                                                                                                                                                                                                | Owner                         |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------- |
| O1  | Ties: the results contract does not say which criterion separates tied products, so the screen can't show "Too close to call" and why                                                                                                                                                                                                                                                                                                | Om Pandya (ranking, results)  |
| O2  | No store offers: the contract has no store search link to show when no offer is found                                                                                                                                                                                                                                                                                                                                                | Jenish Kavad (backend)        |
| O3  | Live progress: the backend has no progress stream yet, so live research shows no stage ticks (the screen says so honestly)                                                                                                                                                                                                                                                                                                           | Jenish Kavad (backend)        |
| O4  | Screen readers (VoiceOver, TalkBack, NVDA): section 3                                                                                                                                                                                                                                                                                                                                                                                | Manu Pardhan, by hand         |
| O5  | Usability test with 5 people and SUS: section 12                                                                                                                                                                                                                                                                                                                                                                                     | Manu Pardhan, by hand         |
| O6  | Mid-range Android phone during a 2-minute research wait, and the wash in a hidden tab: section 8                                                                                                                                                                                                                                                                                                                                     | Manu Pardhan, by hand         |
| O7  | "Finish the questions first" on the brief page is live mode only: run axe on it in a live-mode session                                                                                                                                                                                                                                                                                                                               | Manu Pardhan                  |
| O8  | A CI job for these specs: `docs/ci-frontend-e2e-proposal.md`                                                                                                                                                                                                                                                                                                                                                                         | Palak Patel (CI)              |
| O9  | Lighthouse performance below 90 (home 47, questions 84, results 83) and LCP over 2.5 s on slow 4G. Next steps: decide with Yash Patel whether the home page keeps the letter-by-letter typing (it is most of the main-thread work), serve the fonts from our own server (B8), and run Lighthouse again on a quiet machine. Not fixed in FE-4 because the typing effect is a design decision and the font change needs new font files | Manu Pardhan, with Yash Patel |

## Sprint 2 backlog (severity 1 and 2)

| #   | Item                                                                                                                                                   | Sev |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | --- |
| B1  | One name per action: pick one of "Start researching", "Start my research", "Get started" and "Continue"; use "Try again" everywhere instead of "Retry" | 2   |
| B2  | Replace the "Something went wrong" headings with what happened and what to do                                                                          | 2   |
| B3  | Plural categories ("Researching skincares for you")                                                                                                    | 2   |
| B4  | Sentence case in the footer ("How it works", "Privacy policy"); write upper-case labels in sentence case and let CSS make the capitals                 | 1   |
| B5  | 404 page: lead with "Page not found", not "404"                                                                                                        | 1   |
| B6  | One tab title pattern, such as "Researching — ShopSense" and "Results ready — ShopSense"                                                               | 1   |
| B7  | The page description "AI-powered personalized shopping research": plain words, Indian English spelling                                                 | 1   |
| B8  | Serve the three fonts from our own server, or load the Google Fonts stylesheet without blocking the first paint                                        | 2   |
| B9  | Home hero: don't hide text the server already drew just to type it again                                                                               | 2   |
| B10 | Explain the score next to it (for example "86 out of 100, from your weights")                                                                          | 2   |
| B11 | A header switch to stop background motion, as the design asks                                                                                          | 2   |
| B12 | Styles for `prefers-contrast: more`; turn the wash off for data saver                                                                                  | 1   |
| B13 | At 400% zoom, let the header scroll away on very short screens                                                                                         | 1   |
