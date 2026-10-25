# ml/examples

> Note: these files are synthetic. They exist only to test the scripts in `ml/scripts/`. They are NOT training
> data: never add them to a training set or count them as an agreement run.

| File | What it is |
|---|---|
| `pairs_labeller_a.csv` | 50 made-up pairs (`p0001`–`p0050`) labelled by the placeholder labeller "Alex" |
| `pairs_labeller_b.csv` | The same 50 pairs with the same titles, labelled by the placeholder labeller "Sam" |

- The pairs are laptops, smartphones and earbuds from fictional brands only: Nova, Kestrel, Orion, Aurora and Vela.
- The two files disagree on purpose on 5 pairs, so the four-class kappa lands between 0.80 and 0.95.
- "Alex" and "Sam" are not team members.

Try it from the repository root:

```bash
python ml/scripts/check_labels.py ml/examples/pairs_labeller_a.csv ml/examples/pairs_labeller_b.csv
python ml/scripts/kappa.py ml/examples/pairs_labeller_a.csv ml/examples/pairs_labeller_b.csv --min 0.8
```
