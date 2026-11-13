"""What a public extraction prompt may carry (US-14, with US-46): fixed keys and our own definitions only.

A public call may reach a provider that trains on inputs (build rule 8). A generated criterion may hold the shopper's
words, so it reaches a public prompt only as the category's fixed criterion it maps to, by the synonyms table in
config/extract.yaml (code, no LLM). A hard-rule attribute goes only if it is a known spec key, and price_inr only when
the search has a budget. Reddit prompts are unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

import yaml  # type: ignore[import-untyped]

import app.contracts as C
from app.cli import public_keys_line
from app.contracts import DataClass, ExtractInput, LLMTask, Source, StageContext
from app.contracts import examples as ex
from app.criteria.fallback import DEFAULT_FALLBACK_PATH as FIXED_SETS
from app.criteria.fallback import fallback_criteria, load_category_sets
from app.decide.rules import PRICE_KEY
from app.extract import ExtractStage, GuardedExtractStage
from app.extract.parsing import RawClaim, RawExtraction
from app.extract.prompts import MAX_PROMPT_KEYS, SpecAsk, allowed_criteria, public_prompt_keys, user_message
from app.extract.settings import DEFAULT_CONFIG_PATH, ExtractSettings, load_extract_settings
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
from tests.fakes.llm import FakeLLMClient
from tests.fakes.research import review_source, thread

SETTINGS = load_extract_settings(env={})
QUOTE = "The Nova Book 14 compiles a large project in under a minute."
PAGE = review_source("https://www.reviews.example/a", title="A made-up review", text=QUOTE)
SHOPPER_KEY = "dialysis_records"  # made from what a shopper typed: it must never reach a provider that trains
CTX = StageContext(search_id="srch_keys")


def _input(keys: list[str], source: Source = PAGE, category: str = "laptop") -> ExtractInput:
    return ExtractInput(source=source, criterion_keys=keys, category_label=category)


def _public(keys: list[str], specs: SpecAsk | None = None, category: str = "laptop") -> tuple[str, ...]:
    return allowed_criteria(_input(keys, category=category), DataClass.PUBLIC, specs, SETTINGS).keys


# --- the table -------------------------------------------------------------------------------------------------------


def test_the_synonyms_point_only_at_fixed_criteria() -> None:
    sets = yaml.safe_load(FIXED_SETS.read_text(encoding="utf-8"))
    groups = [*sets["families"].values(), *sets["categories"].values()]  # "generic" is one of the families
    fixed = {c["key"] for group in groups for c in group["criteria"]}
    assert {c.key for s in load_category_sets().values() for c in s.criteria} <= fixed
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw["public_synonyms"], "the table is in config/extract.yaml"
    targets = {target for choices in SETTINGS.public_synonyms.values() for target in choices}
    assert targets <= fixed, sorted(targets - fixed)


def test_every_spec_key_has_our_own_definition() -> None:
    assert {"weight_kg", "ram_gb", "battery_mah", "refresh_rate_hz"} <= set(SETTINGS.public_spec_keys)
    assert all(10 <= len(text) <= 200 for text in SETTINGS.public_spec_keys.values())
    assert SETTINGS.price_definition


# --- generated criteria ----------------------------------------------------------------------------------------------


def test_a_mapped_generated_criterion_is_sent_under_its_fixed_key() -> None:
    criteria = allowed_criteria(_input(["coding_performance"]), DataClass.PUBLIC, None, SETTINGS)
    assert criteria.keys == ("processor_speed",)
    assert criteria.claim_key("processor_speed") == "coding_performance"
    message = user_message(PAGE, criteria)
    assert "processor_speed" in message and "coding_performance" not in message
    assert "coding performance" not in message  # our definition is sent, never words made from the generated key


def test_a_mapped_criterion_comes_back_under_the_searchs_own_key() -> None:
    raw = RawExtraction(
        mentions=[],
        claims=[
            RawClaim(
                product_mention="Nova Book 14",
                criterion_key="processor_speed",
                score=8.0,
                quote=QUOTE,
                polarity="positive",
            )
        ],
    )
    stage = GuardedExtractStage(FakeLLMClient({LLMTask.EXTRACT_CLAIMS: raw}), SETTINGS, lambda: datetime.now(UTC))
    out = stage.run(_input(["coding_performance"]), CTX)
    assert [c.criterion_key for c in out.claims] == ["coding_performance"]


def test_an_unmapped_generated_criterion_is_not_sent() -> None:
    assert _public([SHOPPER_KEY, "battery_life"]) == ("battery_life",)
    message = user_message(PAGE, allowed_criteria(_input([SHOPPER_KEY]), DataClass.PUBLIC, None, SETTINGS))
    assert "dialysis" not in message


def test_a_mapping_to_a_fixed_key_the_category_lacks_is_not_sent() -> None:
    """'thermals' maps to cooling, which laptops' fixed set doesn't have, so it stays out."""
    assert _public(["thermals"]) == ()


def test_a_fixed_key_the_search_already_has_is_sent_once_under_its_own_name() -> None:
    criteria = allowed_criteria(_input(["performance", "processor_speed"]), DataClass.PUBLIC, None, SETTINGS)
    assert criteria.keys == ("processor_speed",)
    assert criteria.claim_key("processor_speed") == "processor_speed"


# --- spec keys and the price -----------------------------------------------------------------------------------------


def test_a_known_hard_rule_key_is_asked_for_with_our_definition() -> None:
    criteria = allowed_criteria(
        _input(["battery_life"]), DataClass.PUBLIC, SpecAsk(("ram_gb",), has_budget=False), SETTINGS
    )
    assert criteria.keys == ("battery_life", "ram_gb")
    assert f"- ram_gb: {SETTINGS.public_spec_keys['ram_gb']}" in criteria.lines


def test_a_hard_rule_key_outside_the_known_list_is_not_sent() -> None:
    keys = _public(["battery_life"], SpecAsk(("ram_gb", "fits_in_my_daughters_bag"), has_budget=False))
    assert keys == ("battery_life", "ram_gb")


def test_price_inr_is_asked_for_only_with_a_budget() -> None:
    assert PRICE_KEY not in _public(["battery_life"], SpecAsk((), has_budget=False))
    assert _public(["battery_life"], SpecAsk((), has_budget=True)) == ("battery_life", PRICE_KEY)


def test_a_spec_claim_keeps_its_spec_key() -> None:
    raw = RawExtraction(
        mentions=[],
        claims=[
            RawClaim(
                product_mention="Nova Book 14",
                criterion_key="ram_gb",
                value=16,
                unit="GB",
                quote=QUOTE,
                polarity="neutral",
            )
        ],
    )
    stage = GuardedExtractStage(FakeLLMClient({LLMTask.EXTRACT_CLAIMS: raw}), SETTINGS, lambda: datetime.now(UTC))
    stage.ask_for_specs(CTX.search_id, ["ram_gb"], has_budget=False)
    out = stage.run(_input(["battery_life"]), CTX)
    assert [(c.criterion_key, c.value) for c in out.claims] == [("ram_gb", 16)]


# --- Reddit and the CLI's count --------------------------------------------------------------------------------------


def test_reddit_prompts_are_unchanged() -> None:
    reddit = thread("t3_keys", "A made-up thread", text=QUOTE)
    keys = [SHOPPER_KEY, "coding_performance", "battery_life"]
    with_specs = allowed_criteria(_input(keys, reddit), DataClass.REDDIT, SpecAsk(("ram_gb",), True), SETTINGS)
    plain = allowed_criteria(_input(keys, reddit), DataClass.REDDIT)
    assert with_specs.keys == plain.keys == tuple(keys)
    assert with_specs.lines == plain.lines


def test_the_count_says_how_many_criteria_and_keys_reach_public_prompts() -> None:
    counted = public_prompt_keys(
        "laptop", ["coding_performance", SHOPPER_KEY, "battery_life"], ["ram_gb", "fits_my_bag"], True, SETTINGS
    )
    assert counted.criteria == ("processor_speed", "battery_life")
    assert counted.specs == (PRICE_KEY, "ram_gb")  # the price first: decide checks the budget before the rules
    assert counted.line(3) == "Public prompts carried 2 of 3 criteria and 2 spec keys (price_inr, ram_gb)."


def test_with_no_table_a_public_call_carries_fixed_keys_only_as_before() -> None:
    bare = ExtractSettings()
    assert allowed_criteria(_input(["coding_performance", "battery_life"]), DataClass.PUBLIC, None, bare).keys == (
        "battery_life",
    )


# --- the pipeline tells the stage ------------------------------------------------------------------------------------


class _Asked(ExtractStage):
    """The extract stub, noting what the runner asked public calls to look for."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, list[str], bool]] = []

    def ask_for_specs(self, search_id: str, attributes: list[str], has_budget: bool) -> None:
        self.asked.append((search_id, list(attributes), has_budget))


def _asked(rubric: C.Rubric) -> list[tuple[str, list[str], bool]]:
    stages = default_search_stages()
    stub = _Asked()
    stages.extract = stub
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=rubric)
    PipelineRunner(stages, InMemoryResultStore()).run(request)
    return stub.asked


def test_the_runner_passes_the_hard_rules_keys_and_the_budget_once_per_search() -> None:
    [(search_id, attributes, has_budget)] = _asked(ex.rubric())
    assert search_id
    assert attributes == [r.attribute for r in ex.rubric().hard_rules] == ["ram_gb"]
    assert has_budget is True


def test_no_budget_is_passed_as_no_budget() -> None:
    rubric = C.Rubric.model_validate({**ex.rubric().model_dump(), "budget": None})
    [(_, _, has_budget)] = _asked(rubric)
    assert has_budget is False


# --- the room a capped page needs ------------------------------------------------------------------------------------


def test_spec_keys_only_fill_the_room_the_criteria_leave_price_first() -> None:
    laptop = [c.key for c in fallback_criteria("laptop").criteria]  # 8 fixed criteria
    counted = public_prompt_keys("laptop", [*laptop, "coding_performance"], ["ram_gb", "weight_kg"], True, SETTINGS)
    assert len(counted.criteria) == 8  # coding_performance's stand-in is processor_speed, already in the search
    assert counted.specs == (PRICE_KEY, "ram_gb")  # 10 lines in all: weight_kg waits for room
    assert len(counted.criteria) + len(counted.specs) == MAX_PROMPT_KEYS


def test_with_room_every_known_rule_key_is_asked_for() -> None:
    counted = public_prompt_keys("laptop", ["battery_life"], ["ram_gb", "weight_kg"], True, SETTINGS)
    assert counted.specs == (PRICE_KEY, "ram_gb", "weight_kg")


# --- ruled-out features (S3-9, with US-40) ---------------------------------------------------------------------------


def test_a_ruled_out_known_feature_is_asked_for_as_a_fixed_key_with_our_definition() -> None:
    ask = SpecAsk((), has_budget=False, features=("touchscreen",))
    criteria = allowed_criteria(_input(["battery_life"]), DataClass.PUBLIC, ask, SETTINGS)
    assert criteria.keys == ("battery_life", "has_touchscreen")
    assert "- has_touchscreen: Whether it has touchscreen (yes or no)." in criteria.lines


def test_a_feature_is_asked_for_by_its_own_name_however_it_was_written() -> None:
    counted = public_prompt_keys("laptop", ["battery_life"], [], False, SETTINGS, features=["Touch Screen", "RGB"])
    assert counted.specs == ("has_touchscreen", "has_rgb_lighting")
    assert counted.definitions["has_rgb_lighting"] == "Whether it has rgb lighting (yes or no)."


def test_an_unknown_feature_is_not_sent() -> None:
    ask = SpecAsk((), has_budget=False, features=("my daughters glitter case", "touchscreen"))
    criteria = allowed_criteria(_input(["battery_life"]), DataClass.PUBLIC, ask, SETTINGS)
    assert criteria.keys == ("battery_life", "has_touchscreen")
    message = user_message(PAGE, criteria)
    assert "glitter" not in message and "daughter" not in message


def test_features_come_after_the_price_and_the_rules_within_the_limit() -> None:
    features = ("touchscreen", "notch")
    counted = public_prompt_keys("laptop", ["battery_life"], ["ram_gb"], True, SETTINGS, features=features)
    assert counted.specs == (PRICE_KEY, "ram_gb", "has_touchscreen", "has_notch")
    laptop = [c.key for c in fallback_criteria("laptop").criteria]  # 8 fixed criteria: room for two more lines
    full = public_prompt_keys("laptop", laptop, ["ram_gb"], True, SETTINGS, features=features)
    assert full.specs == (PRICE_KEY, "ram_gb")  # the features wait for room
    some = public_prompt_keys("laptop", laptop[:7], ["ram_gb"], True, SETTINGS, features=features)
    assert some.specs == (PRICE_KEY, "ram_gb", "has_touchscreen")
    assert len(some.criteria) + len(some.specs) == MAX_PROMPT_KEYS


def test_a_feature_key_the_spec_list_defines_keeps_that_definition() -> None:
    settings = ExtractSettings(
        public_spec_keys={"has_notch": "Whether its screen has a notch cut into it (yes or no)."}
    )
    ask = SpecAsk((), has_budget=False, features=("notch",))
    criteria = allowed_criteria(_input(["battery_life"]), DataClass.PUBLIC, ask, settings)
    assert "- has_notch: Whether its screen has a notch cut into it (yes or no)." in criteria.lines


def test_reddit_prompts_are_unchanged_by_features() -> None:
    reddit = thread("t3_feats", "A made-up thread", text=QUOTE)
    keys = [SHOPPER_KEY, "battery_life"]
    ask = SpecAsk(("ram_gb",), True, features=("touchscreen",))
    with_features = allowed_criteria(_input(keys, reddit), DataClass.REDDIT, ask, SETTINGS)
    plain = allowed_criteria(_input(keys, reddit), DataClass.REDDIT)
    assert with_features.keys == plain.keys == tuple(keys)
    assert with_features.lines == plain.lines


def test_the_stage_keeps_the_features_it_is_asked_for() -> None:
    raw = RawExtraction(
        mentions=[],
        claims=[
            RawClaim(
                product_mention="Nova Book 14",
                criterion_key="has_touchscreen",
                value="no",
                quote=QUOTE,
                polarity="neutral",
            )
        ],
    )
    stage = GuardedExtractStage(FakeLLMClient({LLMTask.EXTRACT_CLAIMS: raw}), SETTINGS, lambda: datetime.now(UTC))
    stage.ask_for_specs(CTX.search_id, [], has_budget=False, features=["touchscreen"])
    out = stage.run(_input(["battery_life"]), CTX)
    assert [(c.criterion_key, c.value) for c in out.claims] == [("has_touchscreen", "no")]
    stage.ask_for_specs("srch_nofeatures", [], has_budget=False)  # still works without features
    assert stage.run(_input(["battery_life"]), StageContext(search_id="srch_nofeatures")).claims == []


def test_the_cli_count_includes_the_features() -> None:
    rubric = C.Rubric.model_validate(
        {
            **ex.rubric().model_dump(),
            "exclusions": [{"kind": "feature", "value": "touchscreen"}, {"kind": "feature", "value": "glitter"}],
        }
    )
    assert public_keys_line(rubric, SETTINGS).endswith("3 spec keys (price_inr, ram_gb, has_touchscreen).")
