# ml/data

Where ML data lives on your own machine. Git keeps only this README, `checksums.sha256` and small, reviewed files.

| Folder | What goes in it | In git? |
|---|---|---|
| `raw/` | Downloads exactly as they arrived, such as a public matching benchmark. Never edit them. | No, never (git-ignored) |
| `interim/` | Anything a script makes along the way: cleaned copies, joins, samples. | No, never (git-ignored) |
| `processed/` | Small final files the team has reviewed, such as an agreed train, validation and test split. | Yes, if small and reviewed |

## Rules

- Raw downloads go in `raw/`. Record each dataset's source, size and licence in [../DATA.md](../DATA.md) before
  you use it.
- Never commit personal data: no names, emails, phone numbers or real user sessions, in any folder.
- `processed/` is for small files only (a few MB at most). Anything bigger is shared outside git, for example on
  the team Drive, with a note in [../DATA.md](../DATA.md) saying where it is.
- Split by `product_family`, so one product family never appears in two splits.
- Model files never go here. Checkpoints belong in `ml/models/`, which is also git-ignored.

Git keeps no empty folders. Create `raw/`, `interim/` or `processed/` when you first need it.

## Public datasets and checksums

The public benchmarks go in `raw/<slug>/`: `raw/wdc_products/`, `raw/abt_buy/` and `raw/amazon_google/`. The
download commands are in [../DATA.md](../DATA.md), section 7.4.

`checksums.sha256` is tracked; the data it describes is not. It lists the SHA-256 of every downloaded and unpacked
file, with paths from the repository root. After downloading, check your copy from the root (Git Bash on Windows):

```bash
sha256sum -c ml/data/checksums.sha256
```
