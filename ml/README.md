# ml/

The ML track of ShopSense: labelled data, labelling tools and model cards for the ML stories (US-15 product
matching first, then US-16, US-04, US-19, US-06, US-22 and the stretch US-36).

The backend never imports anything from here. Trained models reach the app through the stage code in `backend/app/`,
behind the same contracts and fallbacks as every other stage. `check_labels.py` and `kappa.py` use the Python standard
library only (Python 3.11 or newer, Windows included). The matching baseline also needs `ml/requirements.txt`
(rapidfuzz and numpy, pinned).

The matching evaluation (#13) lives in `ml/scripts/` and `ml/tests/`, not in the sprint plan's `evals/matching/`
folder, because the CI `ml` job already lints, type-checks and tests those folders, with no new setup. The trust
evaluation (#30) lives there too, for the same reason, and also uses the standard library only.

> Note: `examples/pairs_labeller_a.csv`, `examples/pairs_labeller_b.csv` and `examples/conflicts_synthetic.csv` are
> synthetic, made up only to test the scripts. They are NOT training data.

## Folder map

```
ml/
├── README.md            # this file
├── DATA.md              # datasets, the labelling CSV format and guide, the agreement rule, the seeded conflicts guide
├── MODELS.md            # one card per model: story, owner, type, data, baseline, target, sprint
├── labelling/
│   ├── pairs_template.csv     # header row and 3 example rows; copy it to start labelling
│   ├── listings_template.csv  # header row and 3 example rows for hand-collected listings (DATA.md section 8)
│   └── conflicts.csv          # real seeded conflicts, one row per claim (DATA.md section 9)
├── requirements.txt     # rapidfuzz and numpy for the matching baseline (pinned)
├── scripts/
│   ├── check_labels.py  # checks labelling CSV files and prints a summary
│   ├── kappa.py         # Cohen's kappa between two labellers on the same pairs
│   ├── check_listings.py, make_pairs.py, label_pairs.py, labelling_status.py
│   │                    # a labelling session: check listings, make a batch, label it, see where you stand
│   ├── listings.py      # the listings format and URL clean-up those four share
│   ├── pairsets.py      # loads our pairs, WDC Products, Abt-Buy and Amazon-Google into one Pair type
│   ├── match_metrics.py # precision, recall, F1 at every threshold; the threshold choice on dev
│   ├── match_baseline.py  # the fuzzy title baseline: python ml/scripts/match_baseline.py all
│   ├── check_conflicts.py # checks seeded conflicts files; every error names its line
│   └── trust_baseline.py  # majority vote and tier order on the seeded conflicts
├── results/             # small result files, such as matching_baseline.json and trust_baseline.json
├── examples/            # synthetic files for testing the scripts only (see the note above)
├── tests/               # pytest tests for the scripts; tests/fixtures/ holds tiny made-up files
├── data/                # local data; raw/ and interim/ are git-ignored (see data/README.md)
└── models/              # local model checkpoints; git-ignored, never committed
```

## Commands

Run these from the repository root.

```bash
# A labelling session (ml/DATA.md, section 8): listings, a batch, labels, then where you stand.
python ml/scripts/check_listings.py ml/labelling/listings.csv --fix
python ml/scripts/make_pairs.py ml/labelling/listings.csv --batch agree-1 --seed 101
python ml/scripts/label_pairs.py ml/labelling/batches/agree-1.pairs.csv --labeller Om
python ml/scripts/labelling_status.py

# Check one or more labelling files. Exit code 0 means no errors.
python ml/scripts/check_labels.py ml/labelling/pairs_template.csv

# Compare two labellers who labelled the same pairs. --min makes the exit code 1 if kappa is too low.
python ml/scripts/kappa.py ml/examples/pairs_labeller_a.csv ml/examples/pairs_labeller_b.csv --min 0.8

# The fuzzy title baseline on every pair set (needs the raw data, ml/DATA.md section 7.4). Exit code 2 = a file is missing.
pip install -r ml/requirements.txt
python ml/scripts/match_baseline.py all --out ml/results/matching_baseline.json
python ml/scripts/match_baseline.py own ml/labelling/<file>.csv [<second labeller's file>.csv]

# Check the seeded conflicts (ml/DATA.md section 9). Exit code 0 means no errors; warnings are allowed.
python ml/scripts/check_conflicts.py ml/labelling/conflicts.csv

# Measure the trust baselines. Exit code 1 = stopped with a message (such as input errors), 2 = the file is missing.
# Nothing is written after either.
python ml/scripts/trust_baseline.py
python ml/scripts/trust_baseline.py ml/examples/conflicts_synthetic.csv

# Tests (pytest comes with the backend's dev install: pip install -e "backend[dev]" -r ml/requirements.txt)
python -m pytest ml/tests -q
```

On Windows, keep the virtual environment path short (for example `C:\venvs\shopsense`). Otherwise installing
numpy fails with WinError 206 (path too long). The other fix is to turn on long paths once, in PowerShell as
administrator:

```powershell
New-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

then open a new terminal (restart Windows if it still fails).

Lint and type checks use the backend's settings in `backend/pyproject.toml`, so run them from `backend/`:

```bash
cd backend
ruff check ../ml && ruff format --check ../ml
mypy --strict ../ml/scripts
```

CI: the `ml` job in [.github/workflows/ci.yml](../.github/workflows/ci.yml) runs on every pull request and every
push to main, on Python 3.11. It runs ruff, ruff format and mypy (with `backend/pyproject.toml`), the tests, both
scripts on the example files, and a guard that fails if anything in `ml/data/raw/` or `ml/models/` is tracked or
any tracked file under `ml/` is over 1 MiB.

## Rules

- Never commit raw downloads, model weights, notebooks with outputs, `.env` files or keys. `.gitignore` blocks
  `ml/data/raw/`, `ml/data/interim/`, `ml/models/`, model weight files and notebook checkpoints.
- No personal data in any file here.
- Every model is pinned to an exact version. See [MODELS.md](MODELS.md).
