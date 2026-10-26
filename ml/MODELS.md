# Model cards

One short card per model. Change a card in the same pull request that changes the model, and fill in every "to be
recorded" before the model is used. Each model replaces a simpler baseline, and that baseline stays as its fallback
("replace, never rewrite", [backend/CLAUDE.md](../backend/CLAUDE.md)). The datasets are described in
[DATA.md](DATA.md).

## Rules for every model

- Every Hugging Face model is pinned by revision: an exact commit hash, never `main` or "latest". Record the name and
  revision in its card.
- Any personal text, such as a shopper's request, interview replies or saved preferences, stays on our own server.

## 1. Product matcher

| | |
|---|---|
| Story | US-15 |
| Owner | Om Pandya |
| Type | Fine-tuned small MiniLM-based cross-encoder. Exact checkpoint: to be recorded |
| Data | Our own labelled pairs (target about 250), split by `product_family` |
| Baseline or fallback | RapidFuzz fuzzy matching |
| Target | Precision of 0.95 or more, and F1 above the fuzzy baseline (card 8) |
| Sprint | 3 |

## 2. Source trust

| | |
|---|---|
| Story | US-16 |
| Owner | Rushiraj Navar |
| Type | Statistical truth discovery, not trained |
| Data | 30 seeded source conflicts |
| Baseline or fallback | Majority vote and fixed tier order |
| Target | More of the 30 seeded conflicts resolved correctly than majority vote |
| Sprint | 3 |

## 3. Smart interview

| | |
|---|---|
| Story | US-04 |
| Owner | Om Pandya |
| Type | Bayesian Normal–Normal updates, not trained |
| Data | 50 simulated shoppers |
| Baseline or fallback | Fixed question order |
| Target | Same accuracy with 30% fewer questions on the 50 simulated shoppers |
| Sprint | 4 |

## 4. Ranking with uncertainty

| | |
|---|---|
| Story | US-19 |
| Owner | Rushiraj Navar |
| Type | 1,000 seeded Monte Carlo simulations |
| Data | No training data. Works on each search's product scores and weights |
| Baseline or fallback | Plain weighted score |
| Target | Spearman of 0.9 or more under 10% weight changes, and the same seed gives the same output |
| Sprint | 4 |

## 5. Preference memory

| | |
|---|---|
| Story | US-06 |
| Owner | Om Pandya |
| Type | Pre-trained sentence embeddings used as-is (384 dimensions, run locally). Exact model: to be recorded |
| Data | 20 seeded returning-user sessions |
| Baseline or fallback | No pre-filled answers |
| Target | 50% fewer repeated questions |
| Sprint | 5 |

## 6. Citation check

| | |
|---|---|
| Story | US-22 |
| Owner | Rushiraj Navar |
| Type | Pre-trained NLI cross-encoder used as-is. Exact model: to be recorded |
| Data | 100 claim and evidence pairs, half deliberately wrong: the threshold is tuned on 40 and tested on 60 |
| Baseline or fallback | Only sentences that quote their evidence |
| Target | Catches 80% or more of wrong claims and rejects at most 15% of right ones |
| Sprint | 5 |

## 7. Promotional comment classifier (stretch)

| | |
|---|---|
| Story | US-36 (stretch) |
| Owner | Om Pandya with Rushiraj Navar |
| Type | A simple classifier trained from scratch |
| Data | 300 hand-labelled comments |
| Baseline or fallback | The US-13 rules |
| Target | Higher F1 than the rules |
| Sprint | 6, only if the earlier targets are met |

## 8. Fuzzy title baseline (Phase 0)

The numbers the product matcher (card 1) must beat, from `ml/scripts/match_baseline.py` (#13). Nothing is trained, and
everything runs on our own machines: no model service is called, so WDC data never leaves them.

| | |
|---|---|
| Story | #13 (matching evaluation scaffold), the baseline for US-15 |
| Owner | Rushiraj Navar |
| Type | Not trained. Three rapidfuzz scorers on the listing text: `fuzz.ratio`, `fuzz.token_sort_ratio`, `fuzz.token_set_ratio`, each divided by 100. Text is normalised with Unicode NFKC, then `rapidfuzz.utils.default_process`. A pair is called a match when its score is at or above the threshold |
| Data | WDC Products (9 test sets), Abt-Buy and Amazon-Google (every pair), and our own labelled pairs once they exist ([DATA.md](DATA.md) sections 2 and 7) |
| Threshold choice | On each dev split, the scorer and threshold (0.00 to 1.00 in steps of 0.01) with the best F1. Ties go to the higher threshold. The test split is scored once, at that choice |
| Split rules | **WDC:** test = each `_gs` file; dev = the `valid_large` set of the same corner-case level and unseen share. **Abt-Buy, Amazon-Google:** every left record against every right record; left records go to dev when the first byte of sha256(id) is even, else test. **Our pairs:** all test (no dev split yet), scored at the choice made on WDC 80cc20rnd000un |
| Versions | rapidfuzz 3.14.6, numpy 2.4.6, Python 3.11.15 (`ml/requirements.txt`) |
| Date | 26 Oct 2026; the numbers are in `ml/results/matching_baseline.json` |
| Run | `python ml/scripts/match_baseline.py all --out ml/results/matching_baseline.json` (about 7 s for everything) |

Test-split results. The "brand + title" rows appear only where putting the brand in front of the title gave a better
dev F1 than the title alone. "Hard negatives called a match" counts WDC's hard non-matches, often variants of the same
model, that the baseline wrongly calls the same product: our "variant" problem in numbers.

| Dataset | Subset | Text | Scorer | Threshold | Dev F1 | Test pairs | True matches | TP | FP | FN | Precision | Recall | F1 | Hard negatives called a match |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WDC Products | 80cc20rnd000un | title | token_set_ratio | 0.79 | 0.322 | 4,500 | 500 | 164 | 460 | 336 | 0.263 | 0.328 | 0.292 | 460 of 3,000 (15.3%) |
| WDC Products | 80cc20rnd050un | title | token_set_ratio | 0.82 | 0.385 | 4,500 | 500 | 206 | 414 | 294 | 0.332 | 0.412 | 0.368 | 414 of 3,000 (13.8%) |
| WDC Products | 80cc20rnd050un | brand + title | token_set_ratio | 0.82 | 0.386 | 4,500 | 500 | 208 | 420 | 292 | 0.331 | 0.416 | 0.369 | 420 of 3,000 (14.0%) |
| WDC Products | 80cc20rnd100un | title | token_set_ratio | 0.82 | 0.385 | 4,500 | 500 | 190 | 389 | 310 | 0.328 | 0.380 | 0.352 | 389 of 3,000 (13.0%) |
| WDC Products | 80cc20rnd100un | brand + title | token_set_ratio | 0.82 | 0.386 | 4,500 | 500 | 195 | 408 | 305 | 0.323 | 0.390 | 0.354 | 408 of 3,000 (13.6%) |
| WDC Products | 50cc50rnd000un | title | token_set_ratio | 0.79 | 0.426 | 4,500 | 500 | 200 | 415 | 300 | 0.325 | 0.400 | 0.359 | 415 of 3,000 (13.8%) |
| WDC Products | 50cc50rnd050un | title | token_set_ratio | 0.81 | 0.407 | 4,500 | 500 | 245 | 347 | 255 | 0.414 | 0.490 | 0.449 | 347 of 3,000 (11.6%) |
| WDC Products | 50cc50rnd050un | brand + title | token_set_ratio | 0.78 | 0.411 | 4,500 | 500 | 280 | 471 | 220 | 0.373 | 0.560 | 0.448 | 471 of 3,000 (15.7%) |
| WDC Products | 50cc50rnd100un | title | token_set_ratio | 0.81 | 0.407 | 4,500 | 500 | 217 | 318 | 283 | 0.406 | 0.434 | 0.419 | 318 of 3,000 (10.6%) |
| WDC Products | 50cc50rnd100un | brand + title | token_set_ratio | 0.78 | 0.411 | 4,500 | 500 | 248 | 451 | 252 | 0.355 | 0.496 | 0.414 | 451 of 3,000 (15.0%) |
| WDC Products | 20cc80rnd000un | title | token_set_ratio | 0.78 | 0.445 | 4,500 | 500 | 241 | 399 | 259 | 0.377 | 0.482 | 0.423 | 399 of 3,000 (13.3%) |
| WDC Products | 20cc80rnd000un | brand + title | token_set_ratio | 0.78 | 0.446 | 4,500 | 500 | 243 | 401 | 257 | 0.377 | 0.486 | 0.425 | 401 of 3,000 (13.4%) |
| WDC Products | 20cc80rnd050un | title | token_set_ratio | 0.75 | 0.469 | 4,500 | 500 | 291 | 441 | 209 | 0.398 | 0.582 | 0.472 | 440 of 3,000 (14.7%) |
| WDC Products | 20cc80rnd100un | title | token_set_ratio | 0.75 | 0.469 | 4,500 | 500 | 298 | 516 | 202 | 0.366 | 0.596 | 0.454 | 516 of 3,000 (17.2%) |
| Abt-Buy | all pairs | name / name | token_set_ratio | 0.83 | 0.476 | 585,312 | 545 | 272 | 384 | 273 | 0.415 | 0.499 | 0.453 | n/a |
| Amazon-Google | all pairs | title / name | token_set_ratio | 0.91 | 0.476 | 2,280,782 | 668 | 419 | 626 | 249 | 0.401 | 0.627 | 0.489 | n/a |

Limits:

- Titles only. Descriptions and prices are not used, and the brand only as the variant above.
- No special handling of model numbers or sizes: "Nova Book 14" and "Nova Book 16" differ by one character, so they
  score as near matches. That is one likely reason why 10.6% to 17.2% of WDC's hard negatives are called a match.
- All-pairs precision on Abt-Buy and Amazon-Google is low because almost every pair is a non-match. For example,
  Amazon-Google's test split has 668 true matches among 2,280,782 pairs (0.03%), so even a few false matches per
  record add up.
- Each WDC level's 050un and 100un validation files are the same pairs (identical after decompressing; only the gzip
  headers differ), so those two test sets share one dev set and one threshold.
- Our own pairs have not been scored yet: there are no real labelled files in `ml/labelling/`. Run
  `python ml/scripts/match_baseline.py own <file>.csv [<second file>.csv]` once there are.
