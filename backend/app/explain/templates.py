"""The explanation code writes itself, from the same facts the LLM is given (US-20): the baseline and the fallback.

Plain sentences with only code's numbers, so it needs no check and no citation. A product nothing could be ranked on
shows no number at all (DR-09): never a score of 0, never "None".
"""

from __future__ import annotations

from collections.abc import Sequence

from app.contracts import Explanation, ExplanationSentence

from .facts import Fact, ProductFacts, TradeOff, shown_score

COMPARED = "compared with the other products we found"
TOO_CLOSE = "It is too close to call with a product next to it."
NAMES_PER_SENTENCE = 5  # so a sentence of long criterion names stays under the contract's 400 characters


def template(facts: ProductFacts) -> Explanation:
    if facts.score is None:
        return Explanation(
            product_id=facts.product_id,
            sentences=[ExplanationSentence(text=f"Listed #{facts.rank}. We found no scores to rank it on.")],
            unverified=list(facts.unverified),
            method="template",
        )
    texts = [f"It ranks #{facts.rank} with a score of {facts.score} out of 100."]
    if facts.tied:
        texts.append(TOO_CLOSE)
    texts += [_strength(fact) for fact in facts.top]
    texts += unverified_sentences([facts.labels[k] for k in facts.unverified])
    return Explanation(
        product_id=facts.product_id,
        sentences=[ExplanationSentence(text=text) for text in texts],
        trade_off=trade_off_text(facts.trade_off),
        unverified=list(facts.unverified),
        method="template",
    )


def trade_off_text(trade: TradeOff | None) -> str | None:
    if trade is None:
        return None
    fact = trade.fact
    if trade.kind == "disputed":
        return f"Sources disagree on its {fact.label}."
    compared = f" {COMPARED}" if fact.basis == "compared" else ""
    return f"Its weak point is {fact.label}, with a score of {shown_score(fact.score)} out of 10{compared}."


def _strength(fact: Fact) -> str:
    name = fact.label[:1].upper() + fact.label[1:]
    adds = f"{name} adds {fact.points} point{'' if fact.points == 1 else 's'}"
    score = shown_score(fact.score)
    if fact.estimated:
        return f"{adds}, but its score of {score} out of 10 is an estimate from similar products."
    compared = f" {COMPARED}" if fact.basis == "compared" else ""
    disputed = ", but sources disagree" if fact.disputed else ""
    return f"{adds}, with a score of {score} out of 10{compared}{disputed}."


def unverified_sentences(names: Sequence[str]) -> list[str]:
    """Every criterion it could not check, named, in sentences of at most NAMES_PER_SENTENCE names."""
    chunks = [names[i : i + NAMES_PER_SENTENCE] for i in range(0, len(names), NAMES_PER_SENTENCE)]
    return [f"We {'also ' if i else ''}could not check its {_listed(chunk)}." for i, chunk in enumerate(chunks)]


def _listed(names: Sequence[str]) -> str:
    """ "a", "a or b", "a, b or c"."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} or {names[-1]}"
