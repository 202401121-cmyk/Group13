"""One product's paragraph (US-20, US-22, D-91): code's sentences, always, with the evidence sentences that were kept.

- No evidence sentence kept: the template, word for word (templates.py).
- With some: the template's sentences, each kept evidence sentence right after the sentence about its criterion's
  strength (a trade-off's after the last strength), and the template's trade-off. The method is the check's:
  llm_checked when the model passed them (each one `supported`), llm_unchecked when the fallback kept them.
- The contract asks every sentence of a written paragraph to cite claims: an evidence sentence cites its own quotes'
  claims, a strength sentence its criterion's claims, and every other code sentence the claims its evidence sentences
  cite. Code's own sentences carry no verdict (`supported` None).
- A paragraph never passes the contract's 8 sentences: evidence sentences past that are left out, the last criteria
  first.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Literal

from app.contracts import Explanation, ExplanationSentence

from .facts import ProductFacts
from .parsing import Draft
from .templates import template

MAX_SENTENCES = 8  # Explanation.sentences' own limit


def paragraph(
    facts: ProductFacts,
    priorities: Collection[str],
    kept: Sequence[Draft],
    method: Literal["llm_checked", "llm_unchecked"],
    *,
    community_only: bool = False,
) -> Explanation:
    code = template(facts, priorities, community_only=community_only)
    if not kept or facts.score is None:
        return code
    room = MAX_SENTENCES - len(code.sentences)
    kept = list(kept)[: max(0, room)]
    if not kept:
        return code
    everything = list(dict.fromkeys(c for d in kept for c in d.claim_ids))
    supported = True if method == "llm_checked" else None
    strengths = {i + _first_strength(facts): f for i, f in enumerate(facts.top)}  # sentence index -> its fact
    after: dict[int, list[Draft]] = {}
    last = max(strengths, default=0)
    for draft in kept:
        at = next((i for i, f in strengths.items() if f.key == draft.key), last)
        after.setdefault(at, []).append(draft)
    sentences: list[ExplanationSentence] = []
    for i, sentence in enumerate(code.sentences):
        fact = strengths.get(i)
        cites = list(fact.claim_ids) if fact is not None and fact.claim_ids else everything
        sentences.append(ExplanationSentence(text=sentence.text, claim_ids=cites))
        sentences += [
            ExplanationSentence(text=d.text, claim_ids=list(d.claim_ids), supported=supported) for d in after.get(i, [])
        ]
    return Explanation.model_validate(
        {**code.model_dump(), "sentences": [s.model_dump() for s in sentences], "method": method}
    )


def _first_strength(facts: ProductFacts) -> int:
    """The index of the template's first strength sentence: after the rank, and the tie when there is one."""
    return 2 if facts.tied else 1
