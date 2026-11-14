"""Prompt text for reading one source (US-14, US-28).

Retrieved text goes only inside wrap_as_data. Our own lists go before it, in the user message.

A public call may reach a provider that trains on inputs (build rule 8), so what's in it matters. A criterion key or
category label from our config holds nothing the shopper typed, so it may go in any call. Anything else may have come
from their words: a generated key can be a slug made from their use case, and an unknown category can be what they
typed. Those go only into Reddit calls, whose route has no provider that trains.

A public call may still carry more than the fixed criteria (US-14, with US-46), always as fixed keys with our own
definitions, chosen by code from config/extract.yaml:
- a generated criterion, as the category's fixed criterion it stands for (public_synonyms). Its claims come back under
  the search's own key;
- a hard rule's key, if it is a known spec key (public_spec_keys), so verify and decide get values to filter on;
- price_inr, when the search has a budget;
- "has_<feature>" for each feature the shopper ruled out (S3-9) that config/exclusions.yaml lists, by the feature's own
  name there and with our own definition, so decide can tell which products have it. A feature not listed there is
  left out: it may be the shopper's own words.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.contracts import DataClass, ExtractInput, Source, SourceType
from app.criteria.fallback import fallback_criteria
from app.decide.features import feature_key, known_feature
from app.decide.rules import PRICE_KEY
from app.intake.lexicon import load_lexicon
from app.llm.data_block import DATA_RULE, wrap_as_data

from .settings import ExtractSettings

SYSTEM_PROMPT = f"""You read one source about products and report what it says. You never judge, rank or recommend.

Fill in:
- mentions: at most 15. Each product the source names, as written. is_product is true for a product someone can buy,
  such as "Nova Book 14", and false for a kind of product, such as "memory foam".
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
If the source says nothing useful, return empty lists.

{DATA_RULE}"""

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
        stand_in = next((t for t in settings.public_synonyms.get(key, ()) if t in fixed), None)
        if stand_in is not None and stand_in not in own and stand_in not in renames:
            criteria.append(stand_in)
            renames[stand_in] = key
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
) -> PromptCriteria:
    """Lexicon keys and labels go in any call. Anything else goes only in a call no provider that trains can see.

    A public call carries fixed keys and our own definitions only (public_prompt_keys). Reddit calls are unchanged.
    """
    slug = inp.category_label
    lexicon = load_lexicon()
    fixed = {c.key: c.definition for c in fallback_criteria(slug).criteria}
    may_hold_their_words = data_class is not DataClass.PUBLIC
    renames: Mapping[str, str] = {}
    if may_hold_their_words:
        keys = tuple(inp.criterion_keys)
        lines = tuple(f"- {key}: {fixed.get(key, _words(key))}" for key in keys)
    else:
        settings = settings if settings is not None else ExtractSettings()
        ask = specs if specs is not None else SpecAsk()
        public = public_prompt_keys(slug, inp.criterion_keys, ask.attributes, ask.has_budget, settings, ask.features)
        keys, renames = public.criteria + public.specs, public.renames
        ours = {**fixed, **public.definitions, **settings.public_spec_keys, PRICE_KEY: settings.price_definition}
        lines = tuple(f"- {key}: {ours[key]}" for key in keys)
    if slug in lexicon.categories:
        return PromptCriteria(keys, lines, lexicon.label(slug), renames)
    return PromptCriteria(keys, lines, _words(slug) if may_hold_their_words and slug else None, renames)


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
