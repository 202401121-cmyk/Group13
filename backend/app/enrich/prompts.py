"""The fact look-up's one batched call (US-41). Public text only: fixed criteria with our own definitions (D-51), and
passages of pages each already found to name its product. Never the shopper's words or a generated criterion's text.

D-101: each page is sent as passages (passages.py), with the criteria looked up for its product, and the answer is kept
short: at most one claim for each of those criteria.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.contracts import Source
from app.extract.prompts import PromptCriteria
from app.llm.data_block import DATA_RULE, wrap_as_data

from .passages import GAP

QUOTE_WORDS = 20

FACTS_SYSTEM_PROMPT = f"""You read passages from a few web pages: the parts of each page around one product's name and
the criteria asked for it, with {GAP.strip()} where text was left out. Each page is listed with its product and the
criteria to look for. For each page, report what its passages say about that product on those criteria only. You never
judge, rank or recommend.

Answer {{"pages": [{{"page": 1, "claims": [...]}}]}}, one entry for each page that says something useful. Keep it
short: at most one claim for each criterion listed for a page. Each claim is one statement about the page's product:
  - product_mention: the product's name, as written on the page.
  - criterion_key: the key, exactly as listed for that page.
  - value: a measured fact, as a number or a few words, with unit if it has one, such as 1.4 with unit kg. For a yes or
    no fact, use "yes" or "no".
  - score: an opinion, from 0 (very bad) to 10 (excellent). Anything that is not a measured fact is a score. Give a
    value or a score, never both.
  - quote: the page's own words, copied exactly, at most {QUOTE_WORDS} words, never across a {GAP.strip()}.
  - polarity: positive, negative or neutral.

Leave out any field you do not use, any statement about another product, and any page that says nothing on its
criteria. Never guess.

{DATA_RULE}"""

LABELS = {"manufacturer": "maker's page", "review_site": "review page"}


@dataclass(frozen=True)
class PageAsk:
    """One page as the call gets it: the product it was read for, the criteria looked up for that product (as the call
    names them) and the passages sent."""

    page: Source
    product: str
    keys: tuple[str, ...]
    text: str


def facts_message(criteria: PromptCriteria, pages: Sequence[PageAsk]) -> str:
    """The criteria, then each page as data, numbered from 1, with its product and the criteria to look for. The
    product's name is the page's own words: a page that doesn't name it is never sent."""
    head = [f"Category: {criteria.category}"] if criteria.category else []
    head += ["Criteria:", *criteria.lines]
    blocks = []
    for number, ask in enumerate(pages, start=1):
        text = f"{ask.page.title}\n\n{ask.text}" if ask.page.title else ask.text
        label = LABELS.get(ask.page.type.value, "web page")
        blocks.append(f"Page {number}, about {ask.product}, for {', '.join(ask.keys)}:\n" + wrap_as_data(text, label))
    return "\n".join(head) + "\n\n" + "\n\n".join(blocks)
