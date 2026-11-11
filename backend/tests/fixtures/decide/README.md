# Decide briefs

Five shopping briefs for the decide stage (US-17, US-18), read by `tests/unit/decide/test_briefs.py`. Every product,
brand, price and request in them is made up, so nobody mistakes them for real facts.

Each file holds:

- `about`: says it is made up;
- `request`: the made-up request the brief stands for;
- `input`: a `DecideInput` (the rubric, the products and their resolved values);
- `expect`: written by hand, not by the stage. It holds the products shown, in rank order, and the products filtered
  out and can't-tell, with their reasons. It also holds the criteria left out of the score, and the hint when nothing
  passes.

| Brief | What it covers |
|---|---|
| `laptop_coding_70k` | The recorded run's criterion keys; over budget, a price on the boundary, an unknown and a disputed price, too little RAM, unknown RAM, an excluded brand, the most-mentioned laptop fitting worst, a criterion no laptop has |
| `phone_camera_30k` | A true-or-false rule, a model exclusion (and the Pro model whose name holds it), a feature exclusion by a known value, a variant that keeps a product in |
| `headphones_anc_range` | A budget range (a price below the minimum), weights in kg, g and oz, "yes" read as true, a brand found from the name |
| `monitor_27_gaming` | Sizes in cm and inches, Hz and kHz, values on the boundary, a rule switched to a preference, a feature exclusion |
| `ereader_8k` | Nothing passes: the hint names the budget, the rule that lets the most products through |

When a brief changes, change its `expect` by hand and say why in the pull request.
