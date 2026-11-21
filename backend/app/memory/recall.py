"""Which remembered preferences fill a new interview (US-06, DECIDED 3). Plain code: no model, no database.

- The score is cosine x 0.5 ^ (age_days / half_life_days): a preference counts half as much every 90 days.
- A weight's cosine is with the new criterion it is closest to (each criterion's "label: definition"), and it fills
  that criterion. One weight per criterion: the better score, ties to the newer.
- A must-have's cosine is with the criterion it is closest to, the same way.
- A leave-out marked Always holds by the shopper's own word, not by closeness to a criterion: its cosine is 1, so only
  its age counts. (A brand one comes from any category; recall's read already chose the rows.)
- The best `top_k` at or above `min_score` are kept, the best first; a tie goes to the newer. Remembered answers
  (weights and must-haves) come first and leave-outs fill the places left, so the shopper's Always leave-outs, which
  score on age alone, never push their answers out.
A row from the future counts as new: its age is 0.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .embedder import cosine
from .settings import MemorySettings
from .store import StoredPreference

DAY_S = 86400.0
TIE_PLACES = 6


@dataclass(frozen=True)
class Ranked:
    row: StoredPreference
    score: float
    similarity: float
    age_days: float
    criterion_key: str | None  # a weight: the new criterion it fills


def decay(age_days: float, half_life_days: float) -> float:
    return float(0.5 ** (max(0.0, age_days) / half_life_days))


def rank(
    rows: Sequence[StoredPreference],
    criteria: Sequence[tuple[str, Sequence[float]]],
    now: datetime,
    settings: MemorySettings,
) -> list[Ranked]:
    """The remembered preferences to use, the best first."""
    if not criteria:
        return []
    best_per_criterion: dict[str, Ranked] = {}
    others: list[Ranked] = []
    for row in rows:
        age = max(0.0, (now - row.updated_at).total_seconds() / DAY_S)
        if row.kind == "exclusion":
            others.append(Ranked(row, decay(age, settings.half_life_days), 1.0, age, None))
            continue
        if not row.embedding:
            continue
        key, similarity = max(
            ((key, cosine(row.embedding, vector)) for key, vector in criteria if len(vector) == len(row.embedding)),
            key=lambda pair: pair[1],
            default=(None, 0.0),
        )
        if key is None:
            continue
        found = Ranked(row, similarity * decay(age, settings.half_life_days), similarity, age, key)
        if row.kind == "weight":
            held = best_per_criterion.get(key)
            if held is None or _order(found) < _order(held):
                best_per_criterion[key] = found
        else:
            others.append(Ranked(row, found.score, similarity, age, row.criterion_key))
    candidates = [*best_per_criterion.values(), *others]
    kept = [r for r in candidates if r.score >= settings.min_score]
    return sorted(kept, key=lambda r: (r.row.kind == "exclusion", *_order(r)))[: settings.top_k]


def _order(ranked: Ranked) -> tuple[float, float, str]:
    """The best score first (to 6 decimal places, so float noise never decides a tie); a tie to the newer; then by id,
    so the order never depends on how rows were read."""
    return (-round(ranked.score, TIE_PLACES), -ranked.row.updated_at.timestamp(), str(ranked.row.id))
