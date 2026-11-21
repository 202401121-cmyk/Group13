"""The explanation code writes itself, from code's own numbers (US-20): the personal part of every paragraph (D-91), and
the whole of it when no evidence sentence is kept.

Plain sentences with only code's numbers, so they need no check and no citation. A strength that is one of the
shopper's priorities says so; nothing the shopper chose ever leaves this process (D-91). A product nothing could be
ranked on shows no number at all (DR-09): never a score of 0, never "None".
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from app.contracts import Explanation, ExplanationSentence

from .facts import Fact, ProductFacts, TradeOff, shown_score
from .readability import GRADE_LIMIT, grade

COMPARED = "compared with the other products we found"
TOO_CLOSE = "It is too close to call with a product next to it."
NAMES_PER_SENTENCE = 5  # so a sentence of long criterion names stays under the contract's 400 characters
PRIORITY = "one of your priorities"
# D-91: a top product whose only evidence is Reddit or video comments, which are never sent to a provider.
COMMUNITY_ONLY = "Community posts discuss this; see the sources."


def template(facts: ProductFacts, priorities: Collection[str] = (), *, community_only: bool = False) -> Explanation:
    """The template paragraph. `priorities` are the shopper's criterion keys; `community_only` adds COMMUNITY_ONLY."""
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
    texts += [strength_sentence(fact, fact.key in priorities) for fact in facts.top]
    texts += unverified_sentences([facts.labels[k] for k in facts.unverified])
    if community_only:
        texts.append(COMMUNITY_ONLY)
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


def strength_sentence(fact: Fact, priority: bool = False) -> str:
    """The template's sentence for one of the top 3: what it adds and its score, said as code scored it, and whether
    the shopper named it a priority (why it matters to them, D-91), as long as saying so keeps the sentence at Grade 9
    or under."""
    name = fact.label[:1].upper() + fact.label[1:]
    if priority:
        said = strength_sentence(fact)
        mine = said.replace(f"{name} adds ", f"{name}, {PRIORITY}, adds ", 1)
        return mine if grade(mine) <= GRADE_LIMIT else said  # the mention never makes a sentence hard to read
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
