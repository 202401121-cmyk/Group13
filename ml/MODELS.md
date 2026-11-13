# Model cards

One short card per model. Change a card in the same pull request that changes the model, and fill in every "to be
recorded" before the model is used. Each model replaces a simpler baseline, and that baseline stays as its fallback
("replace, never rewrite"). The datasets are described in
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
| Type | A cross-encoder, `cross-encoder/ms-marco-MiniLM-L6-v2` (22.7M parameters, Apache-2.0, which allows fine-tuning and this use), fine-tuned to one logit: the same product or not. Loaded with `sentence-transformers` 6.3.0 `CrossEncoder`, trained with a plain PyTorch 2.15.0 loop (`ml/training/train.py`), transformers 5.22.2. Exact versions: `ml/training/requirements.txt`. Fine-tuned weights: `ml/models/matcher_v1/epoch-001` (git-ignored; never committed). ONNX export (D-64): `ml/models/matcher_v1/onnx/` |
| Data | **Train:** Abt-Buy and Amazon-Google (CC BY 4.0), on the dev side of the Leipzig split only, and our own pairs (machine labels, [DATA.md](DATA.md) section 4.1) from about 55% of the dev-side families. The Leipzig records are split by a hash on both sides (left and right), so train and dev share no record; a match across the two parts is left out. Each match comes with 3 look-alike non-matches and one random one. **4,810 pairs, 745 matches** (our own: 48 pairs, 3 matches). **Dev, which picks the threshold:** 1,052 pairs, with 53 matches in all: 41 from the public sets (22 Abt-Buy, 19 Amazon-Google) and 12 of our own, so the threshold is noisy. **Test:** our own test families (57 pairs, 7 matches, the S3-5 split) and three WDC Products test sets (the 80, 50 and 20 percent corner-case sets with all products seen; the other six were not scored). WDC is never trained on (D-54), and no Reddit text is used (D-53). Counts per source: `ml/results/matcher_v1.json` |
| Baseline or fallback | RapidFuzz fuzzy matching (card 8). It stays the default |
| Target | Precision of 0.95 or more, and F1 above the fuzzy baseline (card 8) |
| Training | CPU, 16 cores, 11 Nov 2026, seed 20261111, batch 32, learning rate 4e-5, 4 epochs of 2.8 to 2.9 minutes each (`ml/results/matcher_v1_training.json`). **The checkpoint is epoch 1, chosen by dev only:** dev recall at the precision target was 4 of 53 matches (0.075) after epoch 1 and 2 of 53 (0.038) after epochs 2 to 4, while the training loss fell (0.35, 0.21, 0.16, 0.14). Two matches is too small a difference to show that more epochs generalise worse, so that is a hint, not a tested cause. A checkpoint each epoch; `train.py` resumes from the last |
| Threshold | **0.9646**, chosen on dev: the highest threshold with the best recall among those with precision of 0.95 or more. On dev: precision 1.000, recall 0.075 |
| Hugging Face | Uploaded on 11 Nov 2026 to [`Om-Pandya/shopsense-matcher`](https://huggingface.co/Om-Pandya/shopsense-matcher), revision `6a2ce26b0c4caf6fd1655ef88d27e6ee3954b296`. Files at that revision: `model.onnx` (91 MB), `tokenizer.json`, `tokenizer_config.json` and `README.md` (plus the `.gitattributes` Hugging Face adds). `model.onnx` sha256 `7fa622c2ec76d0f15d5559eec9368ed4adbf0dc89258865f966b17fa441737d4`, the same as the local export (checked by downloading it at that revision). Licence: Apache-2.0. `backend/config/matcher.yaml` holds the repository, revision and threshold for S3-15. Being available does not make it the default: fuzzy stays the default until S3-15 shows otherwise |
| Credits | The training and dev data include Abt-Buy and Amazon-Google from the University of Leipzig's entity resolution benchmarks, used under CC BY 4.0 |
| Sprint | 3 |

**Test results (S3-4, 11 Nov 2026, repository commit `3cebd05`, before this change's commit; the italic comparison-only rows were added in a follow-up commit).** Precision, recall and F1 with
95% bootstrap intervals (1,000 resamples, seed 20261111, as in card 8). The fuzzy baseline uses its own threshold from
card 8. Our own pairs are machine labels ([DATA.md](DATA.md) section 4.1). Numbers: `ml/results/matcher_v1.json`.

| Test set | Method | Threshold | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|
| Our own pairs (machine labels), 57 pairs, 7 matches | **Matcher** | 0.9646 | 1 | 0 | 6 | 1.000 [1.000, 1.000] | 0.143 [0.000, 0.500] | 0.250 [0.000, 0.667] |
| Our own pairs (machine labels) | Fuzzy `token_set_ratio` | 1.00 | 2 | 1 | 5 | 0.667 [0.000, 1.000] | 0.286 [0.000, 0.667] | 0.400 [0.000, 0.727] |
| Our own pairs (machine labels) | *Matcher at best-F1 dev threshold (comparison only)* | 0.5062 | 6 | 7 | 1 | 0.462 [0.200, 0.750] | 0.857 [0.500, 1.000] | 0.600 [0.286, 0.833] |
| WDC 80cc20rnd000un, 4,500 pairs | **Matcher** | 0.9646 | 9 | 8 | 491 | 0.529 [0.286, 0.800] | 0.018 [0.008, 0.030] | 0.035 [0.015, 0.058] |
| WDC 80cc20rnd000un | Fuzzy | 0.79 | 164 | 460 | 336 | 0.263 [0.228, 0.296] | 0.328 [0.286, 0.370] | 0.292 [0.257, 0.325] |
| WDC 80cc20rnd000un | *Matcher at best-F1 dev threshold (comparison only)* | 0.5062 | 225 | 399 | 275 | 0.361 [0.324, 0.396] | 0.450 [0.402, 0.493] | 0.400 [0.361, 0.434] |
| WDC 50cc50rnd000un | **Matcher** | 0.9646 | 18 | 4 | 482 | 0.818 [0.643, 0.958] | 0.036 [0.020, 0.054] | 0.069 [0.039, 0.103] |
| WDC 50cc50rnd000un | Fuzzy | 0.79 | 200 | 415 | 300 | 0.325 [0.289, 0.365] | 0.400 [0.356, 0.446] | 0.359 [0.322, 0.396] |
| WDC 50cc50rnd000un | *Matcher at best-F1 dev threshold (comparison only)* | 0.5062 | 260 | 275 | 240 | 0.486 [0.445, 0.532] | 0.520 [0.475, 0.563] | 0.502 [0.465, 0.539] |
| WDC 20cc80rnd000un | **Matcher** | 0.9646 | 26 | 0 | 474 | 1.000 [1.000, 1.000] | 0.052 [0.034, 0.073] | 0.099 [0.067, 0.136] |
| WDC 20cc80rnd000un | Fuzzy | 0.78 | 241 | 399 | 259 | 0.377 [0.338, 0.417] | 0.482 [0.436, 0.525] | 0.423 [0.384, 0.459] |
| WDC 20cc80rnd000un | *Matcher at best-F1 dev threshold (comparison only)* | 0.5062 | 299 | 245 | 201 | 0.550 [0.506, 0.593] | 0.598 [0.555, 0.640] | 0.573 [0.537, 0.609] |

*Comparison only.* The rows in italics score the same model, on the same test sets and the same bootstrap, at the
threshold with the best F1 on dev (**0.5062**; on dev: F1 0.688, precision 0.597,
recall 0.811, 72 pairs called). **The decided threshold stays 0.9646.**
Nothing is judged against the targets on these rows; they only show what the model does at a threshold that suits F1.

**What this says, plainly.**
- **The precision target is not shown on our own test part.** At the decided threshold the matcher called one pair a
  match there, and it was right (precision 1.0), but one pair cannot show 0.95: the interval is [1.0, 1.0] only because
  there is nothing to resample. It found 1 of 7 matches. At the comparison threshold its precision is
  0.46 on our own test part and 0.36 to
  0.55 on WDC, far below 0.95. Per DECIDED 9, **S3-15 keeps fuzzy as the
  default.** The target is not lowered.
- **At the decided threshold, F1 is below fuzzy's everywhere** (our own pairs and every WDC set), because recall is
  2 to 5% on WDC.
- **At the comparison threshold, F1 is above fuzzy's on all three WDC sets, with intervals that do not overlap:**
  80cc20 0.400 [0.361, 0.434] against 0.292 [0.257, 0.325];
  50cc50 0.502 [0.465, 0.539] against 0.359 [0.322, 0.396];
  20cc80 0.573 [0.537, 0.609] against 0.423 [0.384, 0.459].
  Precision is above fuzzy's there too, but at about 0.4 to 0.55. **On our own pairs the two cannot be told apart:**
  F1 0.600 [0.286, 0.833] against 0.400 [0.000, 0.727], from 7 matches.
- **The two goals were not reached at one threshold.** A precision of 0.95 holds only near 0.96, where recall is 2 to 5%.
  F1 above fuzzy holds only near 0.5062, where precision is about 0.5. Which trade suits the stage is for S3-15 to
  decide, with fuzzy still the default; this row does not change the decided threshold or the result.
- **Where it is precise, it is precise.** At the decided threshold on WDC, precision is higher than fuzzy's on the 50
  and 20 percent sets, and the intervals do not overlap. On the 80 percent set the intervals do overlap (0.29 to 0.80
  against 0.23 to 0.30, from 17 calls), so no difference can be claimed there.
- **Why the decided threshold finds almost nothing:** it sits at 0.96 because dev is hard and small, and at that score
  the model calls very few pairs a match.
- **What is not known.** On dev, the matches found at the precision target fell from 4 of 53 after epoch 1 to 2 of 53
  after epochs 2 to 4 while the training loss fell. Two matches is too small a difference to show that more epochs
  generalise worse, so it is a hint, not a tested cause. What to try next: a lower learning rate or fewer steps, more
  of our own labelled matches (only 3 in the train part), and a larger dev set (53 matches in all is too few to set a
  threshold well). No attempt was made to improve the numbers by changing the target, the dev data or the test data.
- **An earlier run was thrown away.** A first run let train and dev share right-hand records, which could inflate dev
  scores. It was found in review, fixed, and everything was retrained and re-evaluated; only the second run is reported.
- **WDC is a test set here.** It has different kinds of titles from ours, so it says little about how the matcher
  works on our shoppers' products.

**ONNX check (DECIDED 7).** 200 dev pairs from every source: the largest difference between PyTorch and ONNX
probabilities is 7.5e-07 (limit 1e-4), so it passes. It compares probabilities, not logits, so it is less strict near
1.0. Timing: onnxruntime 1.31.1 alone (`ml/training/measure_onnx.py`, its own process) on **2 made-up short titles
repeated 500 times**, batches of 32: **1.7 seconds and 186 MB peak memory** for 1,000 pairs; real, longer titles would
take longer. The model file is 91 MB. Numbers: `ml/results/matcher_v1_onnx.json`.

**Run.** `pip install -r ml/requirements.txt -r ml/training/requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu`,
then `python ml/training/train.py --epochs 4`, `python ml/training/evaluate.py --model ml/models/matcher_v1/epoch-001`
and `python ml/training/export_onnx.py --model ml/models/matcher_v1/epoch-001`. Candidate pairs for later labelling, and
the hard-negative miner: `python ml/scripts/candidates.py` (it never labels; the recorded searches hold page titles,
so its output is poor and is not committed).

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
| Data | WDC Products (9 test sets), Abt-Buy and Amazon-Google (every pair), and our own labelled pairs (machine labels, scored by L2 on 11 Nov 2026; [DATA.md](DATA.md) sections 2, 4.1 and 7) |
| Threshold choice | On each dev split, the scorer and threshold (0.00 to 1.00 in steps of 0.01) with the best F1. Ties go to the higher threshold. The test split is scored once, at that choice |
| Split rules | **WDC:** test = each `_gs` file; dev = the `valid_large` set of the same corner-case level and unseen share. **Abt-Buy, Amazon-Google:** every left record against every right record; left records go to dev when the first byte of sha256(id) is even, else test. **Our pairs:** split by product family, so no family is on both sides: a family goes to dev when the first byte of sha256(family) is even, else test (`pairsets.split_by_family`, which S3-4 uses too). A pair whose two families fall on different sides is left out. The dev side picks the scorer and threshold. The test side is scored once, and again at the WDC 80cc20rnd000un choice for comparison (D-56) |
| Versions | rapidfuzz 3.14.6, numpy 2.4.6, Python 3.11.15 (`ml/requirements.txt`) |
| Date | 26 Oct 2026 for WDC, Abt-Buy and Amazon-Google; 11 Nov 2026 for our own pairs, labels read at commit `28c4e9d`. The numbers are in `ml/results/matching_baseline.json` |
| Run | `python ml/scripts/match_baseline.py all --out ml/results/matching_baseline.json` (about 7 s for everything). Our own pairs: `python ml/scripts/own_eval.py --out ml/results/matching_baseline.json`, which merges its two `own_pairs` entries and keeps the rest; a rerun of `match_baseline.py` keeps them too |

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
| Our own pairs (machine labels) | family split, chosen on dev | title | token_set_ratio | 1.00 | 0.545 | 57 | 7 | 2 | 1 | 5 | 0.667 [0.000, 1.000] | 0.286 [0.000, 0.667] | 0.400 [0.000, 0.727] | 0 of 15 variants (0.0%) |
| Our own pairs (machine labels) | family split, at the WDC 80cc20rnd000un choice | title | token_set_ratio | 0.79 | 0.322 (WDC) | 57 | 7 | 5 | 19 | 2 | 0.208 [0.053, 0.381] | 0.714 [0.333, 1.000] | 0.323 [0.087, 0.514] | 2 of 15 variants (13.3%) |

**Our own pairs (L2, ML-1, 11 Nov 2026).** These are machine labels ([DATA.md](DATA.md) section 4.1), not people's.

- **The gate:** agree-1's four-class kappa is 0.933 (it needs 0.80), and 275 pairs are labelled (it needs 150).
- **Which pairs count:** every label file is combined by `pair_id`. 226 pairs are usable; the other 49 have an unsure
  label, and no pair has labels that disagree on same against not same. A variant is never a match.
- **The split by family:** dev holds 124 pairs in 17 families, and test 57 pairs in 14 families. 45 pairs are left
  out, because their two families fall on different sides.
- **The intervals:** the brackets are 95% bootstrap intervals (1,000 resamples of the test pairs, seed 20261111). For
  precision at the dev choice, 55 resamples held no pair called a match, so they are left out of its interval.
- **The test side is small:** 57 pairs, of which 7 are true matches, so the intervals are wide. Read them, not the
  point values.
- **Called a match on the test side**, at the dev choice and then at the WDC choice:
  - same: 2 of 7, then 5 of 7;
  - variant: 0 of 15, then 2 of 15;
  - different product: 1 of 35, then 17 of 35.
- **By stratum,** in the JSON: at the WDC choice, 6 of the 7 different products in `mention_vs_title` and 8 of the 15
  in `same_family_cross_source` are called a match.
- **What the numbers say:** the dev side chose 1.00. `token_set_ratio` gives 1.00 when one title's words are all among
  the other's, as for a short mention against a fuller title. That is likely the one different product called a match
  at the dev choice, which is in `mention_vs_title`. At the WDC 0.79, most of the true matches are found, but so are
  half of the different products. That is the fuzzy baseline's weakness, as on WDC.
- **What the intervals leave out:**
  - **They resample pairs, not families.** Pairs from one family tend to go together, so the real uncertainty is
    likely larger than the brackets show.
  - **Few hard cases on test.** Pairs whose two families fall on different sides are left out, and they are mostly
    `hard_different` and `random`. So the test side holds only one `hard_different` pair, and "different called a
    match" is barely measured on the hardest stratum.

Limits:

- Titles only. Descriptions and prices are not used, and the brand only as the variant above.
- No special handling of model numbers or sizes: "Nova Book 14" and "Nova Book 16" differ by one character, so they
  score as near matches. That is one likely reason why 10.6% to 17.2% of WDC's hard negatives are called a match.
- All-pairs precision on Abt-Buy and Amazon-Google is low because almost every pair is a non-match. For example,
  Amazon-Google's test split has 668 true matches among 2,280,782 pairs (0.03%), so even a few false matches per
  record add up.
- Each WDC level's 050un and 100un validation files are the same pairs (identical after decompressing; only the gzip
  headers differ), so those two test sets share one dev set and one threshold.
- **Our own pairs carry machine labels** ([DATA.md](DATA.md) section 4.1), and their test side is small (57 pairs, 7
  true matches). The record comes from `own_eval.py`.
- **`match_baseline.py own <file>.csv [<second file>.csv]`** is still there for a quick look at one or two files. It
  scores every pair as test, at the WDC choice.
