"""Prompt text for reading one source (US-14, US-28).

Retrieved text goes only inside wrap_as_data. Our own lists go before it, in the user message.

A public call may reach a provider that trains on inputs (build rule 8), so what's in it matters. A criterion key or
category label from our config holds nothing the shopper typed, so it may go in any call. Anything else may have come
from their words: a generated key can be a slug made from their use case, and an unknown category can be what they
typed. Those go only into Reddit calls, whose route has no provider that trains.

A public call may still carry more than the fixed criteria (US-14, with US-46), always as fixed keys with our own
definitions, chosen by code from config/extract.yaml:
- a generated criterion, as the category's fixed criterion it stands for: an exact public_synonyms entry, else the
  fixed key its words name, unit words left out ("capacity_litres" -> capacity), else a one-word public_synonyms
  alias among its words (app/decide/directions.stand_in, which decide uses too). Only the fixed key and our own
  definition are sent, never the generated key; its claims come back under the search's own key;
- a hard rule's key, if it is a known spec key (public_spec_keys), so verify and decide get values to filter on;
- price_inr, when the search has a budget;
- "has_<feature>" for each feature the shopper ruled out (S3-9) that config/exclusions.yaml lists, by the feature's own
  name there and with our own definition, so decide can tell which products have it. A feature not listed there is
  left out: it may be the shopper's own words.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from app.contracts import DataClass, ExtractInput, Source, SourceType
from app.criteria.fallback import fallback_criteria
from app.decide.directions import stand_in
from app.decide.features import feature_key, known_feature
from app.decide.rules import PRICE_KEY
from app.intake.lexicon import load_lexicon
from app.llm.config import LLMConfigError, load_llm_config
from app.llm.data_block import DATA_RULE, wrap_as_data

from .settings import ExtractSettings

SYSTEM_PROMPT = f"""You read one source about products and report what it says. You never judge, rank or recommend.

Fill in:
- mentions: every product the source names, up to 60, just its name: "Nova Book 14". A kind of product as
  {{"raw_name": "memory foam", "is_product": false}}.
- claims: at most 10, the most useful ones. Each is one statement about one product or kind of product, on one of the
  criteria listed in the message:
  - product_mention: its name, as written in the source.
  - is_product_type: true if it is a kind of product and not one you can buy.
  - criterion_key: the key from the list, exactly.
  - value: a measured fact, as a number or a few words, with unit if it has one, such as 1.4 with unit kg. For a yes or
    no fact, use "yes" or "no".
  - score: an opinion, from 0 (very bad) to 10 (excellent), such as about 8 for "great" and about 3 for "poor". Anything
    that is not a measured fact is a score. Give a value or a score, never both.
  - quote: the source's own words, copied exactly, at most 25 words.
  - polarity: positive, negative or neutral.

Leave out any field you do not use. If no listed criterion fits a statement, leave that statement out. Never guess.
If it names no product and says nothing useful, return empty lists.

{DATA_RULE}"""

# A public page asked for the price or a rule key gives those first (US-14, S4-1): for every product it prices or
# specifies, before the criteria, with up to EXTRA_CLAIMS more claims where the call's output limit holds them. Reddit
# calls, and public calls asked for neither, keep SYSTEM_PROMPT byte for byte.
#
# The variant must still let a capped page go whole to Groq, the public last resort, with its 1,200-token answer
# (tests/unit/llm/test_data_block.py): the worst case there leaves about 26 tokens. So the keys to give first are not
# listed again: their lines simply come first in the criteria, and only the claims phrase below changes, by about 19.
BASE_CLAIMS = 10
EXTRA_CLAIMS = 5
CLAIM_TOKENS = 70  # about what one claim takes in an answer (config/extract.yaml)
MENTIONS_TOKENS = 600  # 60 bare names at about 10 tokens (15 full objects took 566 in a live reply, US-49)
GROQ_PUBLIC_ANSWER = 1200  # config/models.yaml pool.public_output_tokens, if that file can't be read
_CLAIMS_PHRASE = "- claims: at most 10, the most useful ones."
if _CLAIMS_PHRASE not in SYSTEM_PROMPT:  # pragma: no cover - a guard on our own text
    raise RuntimeError("SYSTEM_PROMPT changed: update the price-first variant in app/extract/prompts.py")


def claim_room(first: Sequence[str], output_tokens: int) -> int:
    """How many claims a call may ask for: BASE_CLAIMS, and with keys to give first up to EXTRA_CLAIMS more, as many
    as `output_tokens` holds after the mentions and the base claims: 2,000 holds all 5, Groq's 1,200 none."""
    if not first:
        return BASE_CLAIMS
    spare = (output_tokens - MENTIONS_TOKENS) // CLAIM_TOKENS - BASE_CLAIMS
    return BASE_CLAIMS + max(0, min(EXTRA_CLAIMS, spare))


@lru_cache(maxsize=1)
def groq_public_answer() -> int | None:
    """The answer a public page gets when Groq reads it as the last resort (F-6): the pool's public answer, 1,200
    tokens today, never raised here. A call's one prompt goes to every model on its route, so the room must fit this
    smallest answer too: 15 claims cut Groq's public answers off in the 15 Nov live run. An unreadable config counts as
    the safe 1,200."""
    try:
        return load_llm_config().pool.public_output_tokens
    except LLMConfigError:
        return GROQ_PUBLIC_ANSWER


def public_answer_tokens(settings: ExtractSettings) -> int:
    """What a public page's room has to fit: the smaller of the answer extract asks for and Groq's public answer as the
    last resort. Every other model on the public routes may write at least 4,096 tokens (config/models.yaml)."""
    asked = settings.output_tokens(DataClass.PUBLIC)
    groq = groq_public_answer()
    return asked if groq is None else min(asked, groq)


def system_prompt(room: int, first: int) -> str:
    """SYSTEM_PROMPT, or with `first` keys listed first, its price-first variant allowing `room` claims: a claim on
    each of those keys for every product the source prices or specifies, then the most useful ones."""
    if first <= 0:
        return SYSTEM_PROMPT
    keys = "the first key" if first == 1 else f"the first {first} keys"
    phrase = (
        f"- claims: at most {room}, starting with {keys} for each product priced or specified, then the most useful."
    )
    return SYSTEM_PROMPT.replace(_CLAIMS_PHRASE, phrase)


_LABELS = {SourceType.REDDIT: "Reddit text", SourceType.YOUTUBE: "video text"}
# A title isn't counted in the source's cap, so a long one could push the data block's end past what fits a call.
MAX_TITLE_CHARS = 100
# The most key lines one prompt carries: a rubric's 10 criteria. A capped page beside them just fits Groq whole as the
# public last resort (tests/unit/llm/test_data_block.py), so spec keys only fill the room the criteria leave.
MAX_PROMPT_KEYS = 10
NO_CRITERIA = "No criteria for this source: list the products it names, and leave claims empty."
FEATURE_DEFINITION = "Whether it has {feature} (yes or no)."


@dataclass(frozen=True)
class SpecAsk:
    """What a search asks every public call for besides criteria: its hard rules' keys, whether it has a budget, and
    the features it rules out, each by its own name in config/exclusions.yaml (S3-9)."""

    attributes: tuple[str, ...] = ()
    has_budget: bool = False
    features: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptCriteria:
    """What this one call may carry. A claim on any other key is dropped, because the model was never asked for it."""

    keys: tuple[str, ...]
    lines: tuple[str, ...]
    category: str | None
    renames: Mapping[str, str] = field(default_factory=dict)  # a key sent in place of the search's own key -> that key
    first: tuple[str, ...] = ()  # the price and rule keys to give first, as sent; empty for Reddit and plain calls
    room: int = BASE_CLAIMS  # the claims this call may ask for (claim_room)
    system: str = SYSTEM_PROMPT  # the system prompt this call sends

    def claim_key(self, key: str) -> str:
        """The search's own key for a claim made under `key`."""
        return self.renames.get(key, key)


@dataclass(frozen=True)
class PublicKeys:
    """The keys a search's public prompts carry: criteria (fixed keys) and spec keys. Same for every public source."""

    criteria: tuple[str, ...]
    specs: tuple[str, ...]
    renames: Mapping[str, str]
    definitions: Mapping[str, str] = field(default_factory=dict)  # our own, for each feature key among the specs

    def line(self, criteria_in_search: int) -> str:
        """For the CLI's --claims: how much of the search reached public prompts."""
        n = len(self.specs)
        specs = f"{n} spec {'key' if n == 1 else 'keys'} ({', '.join(self.specs)})" if n else "no spec keys"
        return f"Public prompts carried {len(self.criteria)} of {criteria_in_search} criteria and {specs}."


def _words(slug: str) -> str:
    return slug.replace("_", " ")


def public_prompt_keys(
    category: str,
    criterion_keys: Sequence[str],
    attributes: Sequence[str],
    has_budget: bool,
    settings: ExtractSettings,
    features: Sequence[str] = (),
) -> PublicKeys:
    """Fixed keys only, chosen by code (no LLM): the search's fixed criteria, each generated one's fixed stand-in, known
    spec keys, the price and the ruled-out features config/exclusions.yaml knows. A generated key with no stand-in in
    this category stays out, as do an unknown rule key and an unknown feature. Spec keys only fill the room the
    criteria leave, up to MAX_PROMPT_KEYS lines in all: the price first, then the rules, then the features.
    """
    fixed = {c.key for c in fallback_criteria(category).criteria}
    own = {key for key in criterion_keys if key in fixed}
    criteria: list[str] = []
    renames: dict[str, str] = {}
    for key in criterion_keys:
        if key in fixed:
            criteria.append(key)
            continue
        found = stand_in(key, fixed, settings.public_synonyms)  # matched in code, never sent as written (D-51)
        if found is not None and found not in own and found not in renames:
            criteria.append(found)
            renames[found] = key
    known = {*settings.public_spec_keys, PRICE_KEY}
    # The price first, as decide checks the budget before the rules (US-17); then the rules, in their order.
    wanted = [*([PRICE_KEY] if has_budget else []), *attributes]
    specs = [k for k in dict.fromkeys(wanted) if k in known and k not in criteria]
    ours = _feature_definitions(features)
    specs += [k for k in ours if k not in criteria and k not in specs]
    room = max(0, MAX_PROMPT_KEYS - len(criteria))  # never more lines than a full rubric: the page must fit whole
    sent = tuple(specs[:room])
    return PublicKeys(tuple(criteria), sent, renames, {k: text for k, text in ours.items() if k in sent})


def _feature_definitions(features: Sequence[str]) -> dict[str, str]:
    """Each known feature's key, with our definition made from its own name in config/exclusions.yaml, never from how
    it was written. An unknown feature has none, so it is never sent."""
    out: dict[str, str] = {}
    for written in features:
        name, key = known_feature(written), feature_key(written)
        if name is not None and key is not None:
            out.setdefault(key, FEATURE_DEFINITION.format(feature=name))
    return out


def allowed_criteria(
    inp: ExtractInput,
    data_class: DataClass,
    specs: SpecAsk | None = None,
    settings: ExtractSettings | None = None,
    answer_tokens: int | None = None,
) -> PromptCriteria:
    """Lexicon keys and labels go in any call. Anything else goes only in a call no provider that trains can see.

    A public call carries fixed keys and our own definitions only (public_prompt_keys). Asked for the price or rule
    keys, it lists them first and asks for them first, for every product the page prices or specifies (S4-1), with the
    room claim_room gives within `answer_tokens`: by default the smallest answer any model on the route may give
    (public_answer_tokens), so the extra claims never cut off Groq's public answer. Reddit calls are unchanged.
    """
    slug = inp.category_label
    lexicon = load_lexicon()
    fixed = {c.key: c.definition for c in fallback_criteria(slug).criteria}
    may_hold_their_words = data_class is not DataClass.PUBLIC
    renames: Mapping[str, str] = {}
    first: tuple[str, ...] = ()
    room = BASE_CLAIMS
    if may_hold_their_words:
        keys = tuple(inp.criterion_keys)
        lines = tuple(f"- {key}: {fixed.get(key, _words(key))}" for key in keys)
    else:
        settings = settings if settings is not None else ExtractSettings()
        ask = specs if specs is not None else SpecAsk()
        public = public_prompt_keys(slug, inp.criterion_keys, ask.attributes, ask.has_budget, settings, ask.features)
        first = tuple(key for key in public.specs if key not in public.definitions)  # price, rules; never a feature
        # The keys to give first are listed first (the system prompt names them by position, not again by name).
        keys = first + tuple(key for key in public.criteria + public.specs if key not in first)
        renames = public.renames
        ours = {**fixed, **public.definitions, **settings.public_spec_keys, PRICE_KEY: settings.price_definition}
        lines = tuple(f"- {key}: {ours[key]}" for key in keys)
        room = claim_room(first, answer_tokens if answer_tokens is not None else public_answer_tokens(settings))
    system = system_prompt(room, len(first))
    if slug in lexicon.categories:
        return PromptCriteria(keys, lines, lexicon.label(slug), renames, first, room, system)
    label = _words(slug) if may_hold_their_words and slug else None
    return PromptCriteria(keys, lines, label, renames, first, room, system)


def source_text(source: Source) -> str:
    """What a reader of this source sees, best comments first, as capping left them. A claim must quote from this."""
    return "\n\n".join([source.text, *(comment.text for comment in source.comments)])


def user_message(source: Source, criteria: PromptCriteria) -> str:
    """The title is context for the model, but no claim can rest on it: a page's title can be invisible on the page."""
    title = source.title[:MAX_TITLE_CHARS]
    block = f"{title}\n\n{source_text(source)}" if title else source_text(source)
    head = [f"Category: {criteria.category}"] if criteria.category else []
    head += ["Criteria:", *criteria.lines] if criteria.lines else [NO_CRITERIA]
    return "\n".join(head) + "\n\n" + wrap_as_data(block, _LABELS.get(source.type, "web text"))
