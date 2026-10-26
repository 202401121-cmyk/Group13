# ml/

The ML track of ShopSense: labelled data, labelling tools and model cards for the ML stories (US-15 product
matching first, then US-16, US-04, US-19, US-06, US-22 and the stretch US-36).

The backend never imports anything from here. Trained models reach the app through the stage code in `backend/app/`,
behind the same contracts and fallbacks as every other stage. The scripts here use the Python standard library only
(Python 3.11 or newer, Windows included), so they run without the backend's virtual environment.

> Note: `examples/pairs_labeller_a.csv` and `examples/pairs_labeller_b.csv` are synthetic, made up only to test the
> scripts. They are NOT training data.

## Folder map

```
ml/
├── README.md            # this file
├── DATA.md              # datasets, the labelling CSV format, the labelling guide, the agreement rule
├── MODELS.md            # one card per model: story, owner, type, data, baseline, target, sprint
├── labelling/
│   └── pairs_template.csv     # header row and 3 example rows; copy it to start labelling
├── scripts/
│   ├── check_labels.py  # checks labelling CSV files and prints a summary
│   └── kappa.py         # Cohen's kappa between two labellers on the same pairs
├── examples/            # synthetic files for testing the scripts only (see the note above)
├── tests/               # pytest tests for the scripts
├── data/                # local data; raw/ and interim/ are git-ignored (see data/README.md)
└── models/              # local model checkpoints; git-ignored, never committed
```

## Commands

Run these from the repository root.

```bash
# Check one or more labelling files. Exit code 0 means no errors.
python ml/scripts/check_labels.py ml/labelling/pairs_template.csv

# Compare two labellers who labelled the same pairs. --min makes the exit code 1 if kappa is too low.
python ml/scripts/kappa.py ml/examples/pairs_labeller_a.csv ml/examples/pairs_labeller_b.csv --min 0.8

# Tests (pytest comes with the backend's dev install: cd backend && pip install -e ".[dev]")
python -m pytest ml/tests -q
```

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
