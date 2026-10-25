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
| Target | Precision of 0.95 or more, and F1 above the fuzzy baseline |
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
