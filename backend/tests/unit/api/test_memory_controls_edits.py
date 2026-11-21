"""US-11, the finer points of editing and deleting what is remembered:
- only a weight's words can change (a must-have's and a leave-out's words come from the rule the interview uses);
- the app's own words follow a new weight, even when the old words are sent back with it;
- an edit never writes over a change made while it was being made (409);
- the list is newest first;
- a preference the shopper deleted is never made again by an interview that had recalled it before.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.api.account import PREFERENCE_CHANGED
from app.contracts import Exclusion, PreferenceItem, PreferenceList
from app.interview.remembered import with_remembered
from app.interview.rubric_builder import build_rubric
from app.memory import InMemoryPreferenceStore, StoredPreference
from app.memory.store import Change
from tests.unit.api.test_memory_controls import NOW, RAM, A, World
from tests.unit.memory.test_service import state_for
from tests.unit.memory.test_text_and_lasting import answered


@pytest.fixture
def world() -> World:
    built = World()
    built.seed(A)
    return built


@pytest.mark.parametrize("kind", ["hard_rule", "exclusion"])
def test_only_a_weights_words_can_change(world: World, kind: str) -> None:
    row = world.row(A, kind)
    response = world.call("PATCH", f"/preferences/{row.id}", A, json={"text": "Never Zenbrand"})
    assert response.status_code == 422 and "Only a remembered weight's words" in response.json()["detail"]
    assert world.row(A, kind) == row


def test_the_apps_words_follow_a_new_weight_even_when_the_old_words_come_with_it(world: World) -> None:
    weight = world.row(A, "weight")
    response = world.call("PATCH", f"/preferences/{weight.id}", A, json={"text": weight.text, "weight": 2})
    item = PreferenceItem.model_validate(response.json())
    assert (item.weight, item.text.endswith(": low importance")) == (2.0, True)
    assert world.row(A, "weight").embedding == weight.embedding  # no new words, so no new embedding


class RacingStore(InMemoryPreferenceStore):
    """A save lands between the edit's read and its write, as an interview finishing at that moment would."""

    raced = False

    def one(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> StoredPreference | None:
        found = super().one(user_id, preference_id)
        if found is not None and not self.raced:
            self.raced = True
            full = next(r for r in self.rows(user_id) if r.id == preference_id)
            super().update(user_id, preference_id, Change(full.text, 9.0, full.rule))  # the save in between
        return found


def test_an_edit_never_writes_over_a_change_made_meanwhile() -> None:
    world = World()
    world.store = RacingStore(clock=lambda: world.clock["now"])
    world.memory._store = world.store
    world.seed(A)
    weight = world.row(A, "weight")
    world.clock["now"] = NOW + timedelta(minutes=5)
    response = world.call("PATCH", f"/preferences/{weight.id}", A, json={"text": "Battery: all day"})
    assert response.status_code == 409 and response.json()["detail"] == PREFERENCE_CHANGED
    assert world.row(A, "weight").weight == 9.0 and world.row(A, "weight").text == weight.text  # the save stands


def test_the_list_is_newest_first(world: World) -> None:
    world.clock["now"] = NOW + timedelta(days=1)
    later = answered(state_for("headphones"), "sound_quality", 8.0)
    world.memory.remember(A, later, build_rubric(later))
    items = PreferenceList.model_validate({"items": world.items(A)}).items
    assert items[0].category == "headphones"
    assert [i.created_at for i in items] == sorted((i.created_at for i in items), reverse=True)
    assert len({i.created_at for i in items}) == 2  # two different times, so the order is really tested


def _recalled_state(world: World) -> Any:
    """An interview that recalled A's preferences, then finished without the shopper changing them."""
    state = state_for(hard_rules=[], exclusions=[])
    items = world.memory.recall(A, state)
    # the weight and the leave-out come back; this must-have's words aren't close to a criterion (S5-3's recall)
    assert {i.preference.kind for i in items} == {"weight", "exclusion"}
    return with_remembered(state, items)


def test_a_deleted_preference_is_never_made_again_by_an_interview_that_recalled_it(world: World) -> None:
    state = _recalled_state(world)
    for row in world.store.rows(A):
        assert world.call("DELETE", f"/preferences/{row.id}", A).status_code == 204
    assert world.memory.remember(A, state, build_rubric(state)) == 0
    assert world.store.rows(A) == []


def test_forgetting_everything_also_holds_against_an_open_interview(world: World) -> None:
    state = _recalled_state(world)
    assert world.call("DELETE", "/preferences", A).status_code == 204
    world.memory.remember(A, state, build_rubric(state))
    assert world.store.rows(A) == []


def test_a_recalled_preference_still_there_is_renewed_not_doubled(world: World) -> None:
    state = _recalled_state(world)
    recalled = {r.preference_id for r in state.remembered}
    before = {r.id: r.updated_at for r in world.store.rows(A)}
    world.clock["now"] = NOW + timedelta(days=2)
    assert world.memory.remember(A, state, build_rubric(state)) == len(recalled) == 2
    after = {r.id: r.updated_at for r in world.store.rows(A)}
    assert set(after) == set(before)  # no row added
    assert all(after[k] > before[k] for k in recalled)


def test_a_new_answer_is_saved_even_after_its_old_one_was_deleted(world: World) -> None:
    """The shopper deleted the remembered weight, then answered it again in a new interview: that is new data."""
    weight = world.row(A, "weight")
    assert world.call("DELETE", f"/preferences/{weight.id}", A).status_code == 204
    fresh = answered(
        state_for(exclusions=[Exclusion(kind="brand", value="Acme", always=True)], hard_rules=[RAM]),
        "battery_life",
        5.0,
    )
    world.memory.remember(A, fresh, build_rubric(fresh))
    assert any(r.kind == "weight" for r in world.store.rows(A))
