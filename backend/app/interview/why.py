"""Why a question comes now, in plain words (US-04, S4-5). Code writes it from the belief; no LLM is asked.

The smart interview asks next about the criterion that matters most and is least settled, so the reason says just
that: how much the criterion usually matters (from the belief's mean) and that the shopper's view isn't known yet (from
its sd). The criterion's definition follows when the whole fits in InterviewQuestion.why_asking's 200 characters, so
a plain criterion keeps its explanation under "Why this question?". The label is kept exactly as written (D-44).
"""

from __future__ import annotations

from app.contracts import Criterion, WeightBelief

from .beliefs import BeliefSettings

MAX_WHY = 200  # InterviewQuestion.why_asking limit
A_LOT = 7  # a mean of 6.5 or more (halves round up) "usually matters a lot"
SOME = 4  # 4 to 6 "usually matters"; below 4 "can matter"


def why_now(criterion: Criterion, belief: WeightBelief, settings: BeliefSettings) -> str:
    """'Battery life: it usually matters a lot and we don't know your view yet, so we ask now.', then the definition.

    The label opens the sentence, as in the question itself, and "it" keeps the grammar right for a plural label ("RAM
    and storage: it usually matters"). When the definition doesn't fit beside that, the reason drops "so we ask now"
    to make room; with no room even then, the reason stands alone.
    """
    level = int(belief.mean + 0.5)  # halves round up, as people read them: 6.5 is "7 out of 10"
    matters = "usually matters a lot" if level >= A_LOT else "usually matters" if level >= SOME else "can matter"
    view = "we don't know your view yet" if belief.sd >= settings.prior_sd else "we're not sure of your view yet"
    reason = _within(f"{criterion.label}: it {matters} and {view}, so we ask now.", MAX_WHY)
    shorter = f"{criterion.label}: it {matters}, and {view}."
    definition = " ".join(criterion.definition.split())
    for text in (f"{reason} {definition}", f"{shorter} {definition}"):
        if len(text) <= MAX_WHY:
            return text
    return reason


def _within(text: str, limit: int) -> str:
    """The text, or as many whole words as fit (a guard: a 60-character label still leaves room for the reason)."""
    if len(text) <= limit:
        return text
    cut = text[: limit + 1].rsplit(" ", 1)[0]
    return cut if cut and len(cut) <= limit else text[:limit]
