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

**Serving (S3-15, 14 Nov 2026).** The match stage is `ModelMatchStage` (`backend/app/entities/stage.py`), with the fuzzy
stage as its baseline.

- **How it matches.** The rules decide first, as in S3-3. The model scores only the pairs they leave open, in batches
  of 32, on CPU (onnxruntime 1.31.1). A pair merges only at or above 0.9646. The chaining guard and the variant rules
  are unchanged. The model is given the normalised names the rules compare (`ParsedName.base`), not raw titles.
- **The switch.** `MATCHER=model` or `MATCHER=fuzzy`. The default is **fuzzy** (`backend/config/matcher.yaml`): on our
  test part the model called one pair a match, which can't show the 0.95 target.
- **Getting the model.** The first use downloads `model.onnx` and `tokenizer.json` at revision `6a2ce26b…` into
  `backend/.cache/matcher/` (git-ignored). No token is sent. A `model.onnx` whose sha256 differs from the one above is
  not used. Loading starts in the background when the stage is built, so it overlaps research.
- **Fallback (criterion 6).** Each of these gives fuzzy matching for the whole run, method "fuzzy", and one log line,
  `match: fuzzy matching ran instead of the model: <reason>`:
  - no revision;
  - a failed download or load, or a wrong checksum;
  - an error while scoring;
  - the model still loading, or scoring past its 3-second share of match's 5 seconds;
  - `MATCHER_FORCE_FAIL=1`, for drills.

  A failed load is tried again after 10 minutes.

**Measured** (`tests/live/test_matcher_live.py`, Windows laptop, 16 threads, Python 3.11.15). The hosted server has
fewer cores, so expect longer times there; they were not measured.

| What | Time | Method | Peak memory |
|---|---|---|---|
| Loading from the cache | 0.61 s | | 79 MB before, 255 MB after (+176 MB) |
| 1,000 pairs | 1.39 s | model | |
| S3-3's 1,000 realistic mentions | 0.08 s | model (the rules left no pair open) | |
| 1,000 mentions of 40 names sharing one word (about 780 pairs the model decides) | 0.96 s | model | |
| 1,000 names sharing one word | 3.21 s | fuzzy (the share ran out) | |
| A CLI search, model loaded | | | 254 MB |
| A CLI search, no model | | | 86 MB |

Both searches stay inside P-10's 512 MB.

**Live** (14 Nov 2026, `best wireless headphones under 30000`, `--no-db`)
- With `MATCHER=model`: 60 mentions of 28 names gave 28 products, method model.
- Fuzzy on the same 60 mentions: the same 28 groups.
- The rules settled every pair but one ("triggr trinity 2" against "audeze maxwell 2"), and both scorers kept it apart:
  model 0.004, fuzzy 0.19.
- `MATCHER_FORCE_FAIL=1` gave method fuzzy and the log line.

**What this says.** At 0.9646 the model changes little in real searches: the rules decide almost every pair, and the
model rarely calls a pair the same. On normalised names it scores even two identical names at 0.92 ("nova 14"; the rules
merge those before it is asked) and a one-letter typo at 0.81 ("kindle paperwhite" against "kindle paperwite"), which
stays apart where fuzzy merges it.
Fuzzy stays the default until a larger labelled test part shows the target.

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

**Pages on sites no list names (US-41, D-102, 27 Nov 2026).** Enrich's fact look-ups may now read a public page on a site
that `config/trust.yaml` doesn't list, when it names the exact model and holds the quote word for word. Such a page is
typed a web page with the lowest tier (5), and starts at web search's default trust, 55: the same trust research already
gives any unknown page. So in verify it ranks below review sites (70) and makers, but above Reddit (45) and stores (40).
Why: it is a page about the product written by a site, not an owner's post or a shop's listing, and nothing is known
about the site either way. A lower trust for these pages would be a change to verify, which the team kept out of this
story.

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
| Code | `backend/app/interview/beliefs.py` and `adaptive.py`; the live interview since US-04 |
| Tuned numbers | Prior sd 2.5, exact-answer sd 1.5, settled sd 0.7 (D-69 started at 2.5, 1.5 and 1.2). Tuned by `python -m evals.interview_tune` on seed 4747, accuracy first (Om, 17 Nov 2026): `backend/evals/results/interview-tune.json` |
| Result | Seed 47, 300 shoppers (`interview-sim-smart.json`): 8.0 questions, accuracy 0.60, weight error 1.02, the same as the fixed order. **Target not met:** no setting kept the accuracy with fewer questions. D-69's own numbers (`interview-sim-d69.json`) ask 4.49 questions (44% fewer) but drop accuracy to 0.49 |
| Why it matches the fixed order | The beliefs are independent, so an unasked criterion stays at its default with the prior sd, and mean × sd orders them as the fixed order does. Only the stop can differ. At settled sd 0.7 only "good enough" answers settle (sd 0.49), so it stops early only when the 3 highest means are all "good enough" |
| Product-aware stop (D-75, S5-14) | Tried on the simulator only, by `python -m evals.interview_stop`. After at least 3 answers, decide's seeded simulations (1,000) score the brief's products, with each weight drawn from its belief. The interview stops when one product is first in at least a set share of them. The share was tuned on seed 4747, accuracy first: 0.61 (7.19 questions, accuracy 0.607). D-75's starting share, 0.9, asked 7.96. Record: `backend/evals/results/interview-stop-tune.json` |
| Its result | Seed 47, 300 shoppers (`interview-sim-stop.json`): 7.02 questions, 12.3% fewer than the fixed order's 8.0, at accuracy 0.607 against 0.600. **Target not met:** 30% fewer means about 5.6 questions. On the tuning seed no share gets there: the lowest, 0.51, still asks 6.20, and its accuracy falls to 0.587. The share stays above one half, because at a half or less two products tied at the top would stop the interview. Nothing ships in `app/` (D-75 #2) |
| Why it can't run live | The live interview has no products: research runs after it. A product-aware stop could only run live if research started during the interview |

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
| How it's checked | `python -m evals.stability` makes 100 random weight changes of up to 10% (seed 19). It compares each changed order with the order before the change. It checks the five decide briefs and, beside them, a made-up brief with close earbuds (D-74). Record: `backend/evals/results/us19-stability.json`. Two numbers come from the same changes (P-45, S5-15) |
| What the two numbers count | Today's number compares the places of every shown product, each in its own position. The second number gives each group of products the app marks "too close to call" one shared place, the average of its members' places, so a reorder inside a group moves nothing and any other move counts. |
| Results | **The five briefs:** median and lowest 1.0 both ways. Their shown products are far apart, and none are tied. **The close brief, today's number:** median 0.8929, lowest 0.7143. **Target not met** (floor 0.8429). **The close brief, second number:** median 1.0 and lowest 1.0, over 3 positions: the four closest products, Fern Talk, then Kite Lite and Opal Mini. With only 3 positions the number is coarse (Moss Quiet slipping one place, below Fern Talk, would still score 0.97, above the target), so this 1.0 is coarse evidence; the claim it supports is that no change moved a product out of its "too close to call" group. Target met (floor 0.95). In all 100 changes, every product that moved stayed inside a group the app already shows as too close to call |

## 5. Preference memory

| | |
|---|---|
| Story | US-06 |
| Owner | Om Pandya |
| Type | Pre-trained sentence embeddings, used as they are: [`sentence-transformers/all-MiniLM-L6-v2`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) (Apache-2.0, 384 dimensions). It runs on our own server through onnxruntime 1.31.1 and tokenizers 0.23.3, with mean pooling and L2 normalising (`backend/app/memory/embedder.py`). Nothing a shopper typed or the app remembers goes to any provider (P-40, D-51, D-52) |
| Hugging Face | Revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, read on 21 Nov 2026 (the repository's last change was on 1 Jun 2026). The file is `onnx/model_O4.onnx` (45,212,349 bytes), sha256 `1667d7f3ba669048b13a96ee3a44456d5e42c8f44588ae8b603430e16160c485`. `tokenizer.json` has sha256 `be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037`. Both are pinned in `backend/config/memory.yaml`, downloaded once into `backend/.cache/memory/` (git-ignored), and never committed |
| Which file | The rule (S5-3 DECIDED 1): the smallest file in `onnx/` whose vectors agree with `onnx/model.onnx` at a lowest cosine of 0.99 or more, over 200 sentences made by code from our own criteria. The numbers below are from an AVX2 machine (`backend/evals/results/memory-model-check-avx2.json`) |
| Check on each machine | Before memory uses the model on a machine, it embeds 6 reference sentences and compares them with the full model's vectors (`backend/config/memory_reference.json`). Under 0.99, memory stays off on that machine, with one log line. So a machine where the file's vectors differ never uses them |
| Data | No training. Measured on 20 seeded returning shoppers, made by code from the S4-3 interview simulator (`backend/evals/memory_sim.py`). No hand labels, and no Reddit text |
| Baseline or fallback | No pre-filled answers: the interview as it was before US-06. Any failure gives that, with one log line (the model, its sha256, the check above, the database, or recall past its 1.5 s share of the interview turn). `MEMORY=off` switches it off |
| Target | 50% fewer repeated questions, with accuracy no lower (both in `backend/evals/thresholds.yaml`) |
| Sprint | 5 |

**Which file (S5-3, 21 Nov 2026).** The machine was an AMD Ryzen 7 5800HS (AVX2, no AVX-512), running onnxruntime
1.31.1. Each file embedded the same 200 sentences, and each was compared with `onnx/model.onnx`:

| File | Bytes | Lowest cosine | Mean cosine | Under 0.99 | ms per sentence |
|---|---|---|---|---|---|
| `onnx/model_qint8_avx512.onnx` (byte for byte the same as `model_qint8_avx512_vnni` and `model_qint8_arm64`) | 23,026,053 | 0.985214 | 0.991257 | 41 of 200 | 2.81 |
| `onnx/model_quint8_avx2.onnx` | 23,046,789 | 0.984958 | 0.991955 | 29 of 200 | 3.47 |
| **`onnx/model_O4.onnx`** (chosen) | 45,212,349 | 0.999997 | 0.999999 | 0 | 5.39 |
| `onnx/model.onnx` (the reference) | 90,405,214 | 1.000000 | 1.000000 | 0 | 5.15 |

Both 23 MB int8 files fall short of 0.99. The next smallest, `model_O4.onnx`, agrees at 0.999997, so it is the one
used.

**Not yet measured on an AVX-512 machine.** No AVX-512 machine was at hand. To measure it there, run
`python -m evals.memory_model_check` from `backend/`; it writes `memory-model-check-avx512.json`. Until then, the
check on each machine (above) stands in for that measurement: a machine where `model_O4.onnx` drifts never uses it.

**Results (S5-3, 21 Nov 2026; `backend/evals/results/memory-sim.json`).** 20 returning shoppers, in the 6 categories
of the simulator. Each did a first session, then 7 days later a second one in the same category with the same hidden
weights:

| | Without memory | With memory |
|---|---|---|
| Questions in the second session (mean) | 8.0 | 3.0 |
| Repeated questions (answered in the first session, asked again) | 160 | 60 |
| Accuracy (best match the one the true weights give) | 0.60 | 0.60 |

- **The target is met:** 62.5% fewer repeated questions (target 50%), with accuracy the same.
- **Why the 60 are left:** 5 of each shopper's 8 answers were remembered (the most memory keeps), and the other 3 were
  asked again.
- **Where it is easy:** the simulator's criteria are worded the same in both sessions, so each remembered weight met its
  criterion at cosine 1.0. Live criteria from the LLM are worded differently each time, and there the 0.7 threshold
  decides.

**Cost on CPU (P-10, `backend/evals/results/memory-cost.json`; same machine).**
- **Memory:** with the matcher and this model both loaded, the process held about 179 MB (96 MB with the matcher
  alone), and 193 MB after embedding. Its peak was 225 MB, during the matcher's load.
- **Load:** the model loads from the cache in 0.33 s.
- **Speed:** embedding 10 criteria takes 31 ms (median of 30 runs), and 34 ms at worst.

## 6. Citation check

| | |
|---|---|
| Story | US-22 |
| Owner | Rushiraj Navar |
| Type | Pre-trained NLI cross-encoder, used as it is: [`cross-encoder/nli-deberta-v3-xsmall`](https://huggingface.co/cross-encoder/nli-deberta-v3-xsmall) (Apache-2.0). Its outputs, from `config.json` read on 21 Nov 2026: 0 contradiction, 1 entailment, 2 neutral. It runs on our own server through onnxruntime 1.31.1 and tokenizers 0.23.3 (`backend/app/explain/nli.py`). No quote and no shopper's words reach any provider (P-41) |
| Hugging Face | Revision `a150876415327c80daeff35ca6f68f5ed8cf5c24`, read on 21 Nov 2026. The file is `onnx/model_O4.onnx` (151,285,017 bytes), sha256 `7f3e118f33944fe2a2bfa50ae889098f12914e4fd5194ab13dbaf82a5bee02c5`. `tokenizer.json` has sha256 `5124ef2ead1a10a717703bc436de7f353da76d6340e4587719b42b1693707964`. All are pinned in `backend/config/nli.yaml`, downloaded once into `backend/.cache/nli/` (git-ignored), and never committed |
| What it checks | Since D-91 (S5-4b): evidence sentences. Code writes every paragraph's personal part (its rank and score, each top-3 criterion and why it matters to the shopper, the trade-off and what could not be verified). For each of the top 3 products, one public-route call (`write_evidence`: Mistral, then Gemini, then Groq as the last resort) gets only the product's name and its public quotes, in groups named t1, t2 ... and never by criterion (a generated criterion's name can hold the shopper's words, D-51), and writes at most one short sentence per group, citing the quotes it used. Code's rules come first (refs it was given, one criterion, numbers in its quotes or code's own, a score, points or rank stated as its own kind's value, no community post's words, no instruction, Grade 9 or under) |
| The check | Code's own numbers in a sentence (a score, points, the rank, the 0-100 score) pass by the number rule alone, so the phrase stating them is taken out first (A). Then each cited quote in turn is the premise (a quote over 400 tokens is split into chunks that overlap by 50) and the sentence is the hypothesis; the highest entailment probability counts. It passes at or above the threshold, **0.9608**. A failed evidence sentence is dropped; code's sentences always stay, and a paragraph with no evidence sentence left is the template. Every product's pairs go in one batch, within 5 s, through one small interface (`nli.sentence_probabilities`: sentences and quotes in, probabilities out), so P-49 can move the model to its own service |
| Which file | The rule (S5-4 DECIDED 1): the smallest file in `onnx/` whose entailment probabilities agree with `onnx/model.onnx` within 0.02 on the 40 tuning pairs. The numbers below are from an AVX2 machine (`backend/evals/results/citation-model-check-avx2.json`) |
| Check on each machine | Before the check uses the model on a machine, it reads 6 reference pairs and compares its probabilities with the full model's (`reference` in `backend/config/nli.yaml`). A difference over 0.02 means that machine never uses the model, with one log line |
| Data | 100 pairs written by people, by hand (by-hand job 2, D-55): Rushiraj Navar wrote c001 to c050 and Jhanvi Barot c051 to c100 (`backend/evals/citation/pairs.csv`). Each quote was drawn by a seeded script from the recorded runs' public web sources, never Reddit or YouTube comments. Half the sentences are supported and half deliberately wrong. A fixed seed split them 40 to tune (20 and 20) and 60 to test (30 and 30) (`split.json`). No training |
| Baseline or fallback | **On by default since S5-4b** (DECIDED 7 met, below); `NLI=off` switches it off, and staging commits `NLI=off` in `render.yaml` (512 MB, P-10). Without the model (off, or with `NLI=on` the model missing, a wrong sha256, the check above, an error, or the 5 s limit, each with one log line), an evidence sentence stays only when it holds at least 6 words of a cited quote in a row, word for word (`llm_unchecked`). No public provider answering: code's sentences only, the template |
| Target | Catches 80% or more of wrong claims and rejects at most 15% of right ones, on the 60 held out (both in `backend/evals/thresholds.yaml`) |
| Sprint | 5 |

**Which file (S5-4, 21 Nov 2026).** The machine was an AMD Ryzen 7 5800HS (AVX2, no AVX-512), running onnxruntime
1.31.1. Each file read the same 40 tuning pairs, and each was compared with `onnx/model.onnx`:

| File | Bytes | Largest difference | Mean difference | Over 0.02 | ms per pair |
|---|---|---|---|---|---|
| `onnx/model_qint8_avx512.onnx` (byte for byte the same as `model_qint8_avx512_vnni` and `model_qint8_arm64`) | 87,377,068 | 0.9186 | 0.4870 | 40 of 40 | 18.2 |
| `onnx/model_quint8_avx2.onnx` | 87,377,068 | 0.1031 | 0.0097 | 4 of 40 | 18.0 |
| **`onnx/model_O4.onnx`** (chosen) | 151,285,017 | 0.0049 | 0.0003 | 0 | 23.2 |
| `onnx/model.onnx` (the reference) | 284,200,859 | 0 | 0 | 0 | 22.6 |

Both 87 MB int8 files fall outside 0.02. The next smallest, `model_O4.onnx`, agrees within 0.0049, so it is the one
used.

**Not yet measured on an AVX-512 machine.** No AVX-512 machine was at hand. To measure it there, run
`python -m evals.citation.model_check` from `backend/`; it writes `citation-model-check-avx512.json`. Until then, the
check on each machine (above) stands in for that measurement: a machine where `model_O4.onnx` drifts never uses it.

**Results (S5-4, 21 Nov 2026; `backend/evals/results/citation.json`).** The threshold was tuned on the 40 tuning pairs
only: the most wrong sentences caught with at most 15% of the right ones rejected. On them it caught 90% and rejected
15%. The 60 test pairs were measured once, at that threshold:

| On the 60 held out (30 wrong, 30 right) | Measured | 95% bootstrap interval | Target | |
|---|---|---|---|---|
| Wrong sentences caught | 100% (30 of 30) | 100% to 100% | 80% or more | met |
| Right sentences wrongly rejected | 16.7% (5 of 30) | 3.8% to 30.8% | at most 15% | **not met** |

- **Why it is not met:** one right sentence too many was rejected: 4 of 30 would have met it. All 5 had probabilities
  from 0.63 to 0.94, under the threshold (`misjudged` in the record). The number rule rejected none of them, so the
  model's probability alone gives the same result. No wrong sentence passed.
- **The threshold was not moved to meet it.** The test pairs are for measuring only. The interval is wide on 30 right
  sentences, and it includes 15%.
- **The floors** in `backend/evals/thresholds.yaml` hold this result (caught at least 95%, wrongly rejected at most
  21.7%), with the 80% and 15% targets beside them.

**LLM-style sentences (S5-4):** 0 of 20 sentences written the way US-20's private LLM wrote them (code's scores in
words, never a quote) passed at 0.9608. That is why D-91 (S5-4b) changed what is checked: code now writes the
personal part, and the LLM writes evidence sentences from public quotes.

**Since D-91 (S5-4b, 22 Nov 2026).**
- **(a) The 60 test pairs again:** the same as above, at the same threshold (no worse than S5-4).
- **Live, on the five recorded runs' rankings** (replayed offline, live public calls, `NLI=on`): 6 calls wrote 8
  evidence sentences. Code's rules dropped 1 (above Grade 9: a processor's long name), and the model kept 6 of the
  other 7; 6 of the 13 top products had public quotes and got `llm_checked` paragraphs, the rest the template.
- **(b) 20 evidence sentences this pipeline wrote from the five recorded runs, labelled by people** (D-55): Rushiraj
  Navar labelled e001 to e010 and Jhanvi Barot e011 to e020, each alone and by hand (`evidence_sheet.csv`): 16
  supported, 4 unsupported. The recorded runs' top 3 hold public quotes for only 8 criteria, so the sheet went on down
  each ranking with the same call (17 public calls). Against those labels (`backend/evals/results/citation-evidence.json`):

| On the 20 labelled evidence sentences | The model (threshold 0.9608) | 95% bootstrap interval | The 6-word rule alone | Target (DECIDED 7) |
|---|---|---|---|---|
| Supported sentences kept | **93.75%** (15 of 16) | 81.25% to 100% | 25% (4 of 16) | 80% or more: met |
| Unsupported sentences caught | **100%** (4 of 4) | 100% to 100% | 100% (4 of 4) | 80% or more: met |

- **So NLI is on by default** (DECIDED 7). The one sentence the model dropped though it was labelled supported is
  e017; the 6-word rule kept it. Only 4 sentences were unsupported, so the caught share rests on few cases.
- **Without the model** (NLI off, or the model missing), the 6-word rule keeps only a quarter of the good sentences.

**Cost on CPU (P-10; same machine).**
- **Memory (`citation.json`, measured again for S5-4b):** with the matcher and this model both loaded, the process held
  511 MB (98 MB with the matcher alone), and 550 MB after the timing below. In a one-off check on the same machine (each file loaded alone in a fresh process,
  not in a committed record), `model_O4.onnx` added about 400 MB to the process and the 87 MB int8 file about 300 MB.
- **So it does not fit the hosted backend's 512 MB.** By P-10's rule, staging runs with `NLI=off`, written in
  `render.yaml`, and the check runs only on a machine with enough memory (`docs/deploy.md`).
- **A service of its own (P-49, S5-4b; `backend/evals/results/citation-memory.json`):** a fresh process holding only
  `model_O4.onnx` and its tokenizer, checking the recorded runs' largest batch, peaked at 363.4 MB with onnxruntime's
  defaults and 363.5 MB with its memory arena and memory pattern off, both under 450 MB, with the same probabilities.
  That batch is small (3 pairs); a product can send up to 4 criteria with 3 quotes each. No smaller or quantised file
  was tried: quality must not drop.
- **Load:** the model loads from the cache in 1.8 to 4.5 s (two runs), in the background while the search runs.
- **Speed (S5-4b):** checking one product's evidence sentences took 0.044 s (median) and 0.081 s at worst, against
  its 5 s limit: the top 3 products with public quotes of the 3 recorded runs that have them (6 products; 1 pair at the
  median and 3 at most), replayed offline.

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
