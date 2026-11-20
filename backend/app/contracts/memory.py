"""What ShopSense remembers for a signed-in shopper (US-06, US-11, Sprint 5, P-40): their weights, must-haves and
leave-outs, each as one item they can see, change or forget.

An item keeps what the shopper told us in plain words, so the memory screen shows it as they said it (US-11), and the
interview can bring it back as a remembered answer (US-06, `RememberedPreference`). No contract here holds an
embedding, a user id or an email: the database keeps those beside the row, and the browser never sees them. A guest has
no memory at all (D-36).
"""

from __future__ import annotations

from itertools import pairwise
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from .common import AwareDatetime, Contract, Score10, Slug, StrictScore10
from .intake import Exclusion, HardRule

MAX_PREFERENCE_TEXT = 200
MAX_PREFERENCES = 500

PreferenceKind = Literal["weight", "hard_rule", "exclusion"]


class PreferenceItem(Contract):
    """One thing remembered: a weight for a criterion, a must-have, or a leave-out.

    A weight carries its criterion and its 0 to 10 weight; a must-have its rule, in the hard rule's own shape; a
    leave-out its exclusion. Each holds that and nothing of the others. An item belongs to one category, except a brand
    left out with "Always", which holds for every category (US-10).
    """

    id: UUID
    kind: PreferenceKind
    category: Slug | None = Field(
        default=None, description="The category it holds for. None only for a brand left out with Always: every one"
    )
    criterion_key: Slug | None = Field(default=None, description="A weight's criterion; None for the other kinds")
    text: str = Field(
        min_length=1,
        max_length=MAX_PREFERENCE_TEXT,
        description="What the shopper told us, in plain words, such as 'Battery life matters a lot'",
    )
    weight: Score10 | None = Field(default=None, description="A weight's 0 to 10 weight; None for the other kinds")
    rule: HardRule | None = Field(default=None, description="A must-have's rule; None for the other kinds")
    exclusion: Exclusion | None = Field(default=None, description="A leave-out; None for the other kinds")
    always: bool = Field(default=False, description="Kept for every later search; a leave-out's own Always")
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _check(self) -> PreferenceItem:
        carried = {name for name in ("criterion_key", "weight", "rule", "exclusion") if getattr(self, name) is not None}
        needs = {
            "weight": {"criterion_key", "weight"},
            "hard_rule": {"rule"},
            "exclusion": {"exclusion"},
        }[self.kind]
        allowed = needs | ({"criterion_key"} if self.kind == "hard_rule" else set())
        if not needs <= carried or not carried <= allowed:
            raise ValueError(
                f"a {self.kind} item carries {', '.join(sorted(needs))} and none of the other kinds' fields"
            )
        if self.exclusion is not None and self.exclusion.always != self.always:
            raise ValueError("a leave-out's always is the item's own always")
        brand_always = self.exclusion is not None and self.exclusion.kind == "brand" and self.always
        if brand_always != (self.category is None):
            raise ValueError("a brand left out with Always has no category, as it holds for every one; others have one")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at is before created_at")
        return self


class PreferenceList(Contract):
    """Everything remembered for the signed-in shopper, newest first (US-11)."""

    items: list[PreferenceItem] = Field(default_factory=list, max_length=MAX_PREFERENCES)

    @model_validator(mode="after")
    def _check(self) -> PreferenceList:
        times = [i.created_at for i in self.items]
        if any(later > earlier for earlier, later in pairwise(times)):
            raise ValueError("the items are newest first")
        return self


class PreferenceEdit(Contract):
    """A change to one remembered item (US-11): its words, its weight, a must-have switched on or off, or Always. Each
    field left out stays as it is; an edit changes at least one."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=False)  # a request body

    text: str | None = Field(default=None, min_length=1, max_length=MAX_PREFERENCE_TEXT, description="New words")
    weight: StrictScore10 | None = Field(default=None, description="A weight's new 0 to 10 weight")
    active: bool | None = Field(
        default=None, description="A must-have: false keeps it as a preference that no longer filters (US-09)"
    )
    always: bool | None = Field(default=None, description="Keep it for every later search, or not")

    @model_validator(mode="after")
    def _check(self) -> PreferenceEdit:
        if all(getattr(self, name) is None for name in ("text", "weight", "active", "always")):
            raise ValueError("an edit that changes nothing; send at least one of text, weight, active or always")
        return self


class PreferenceExport(Contract):
    """Every remembered item, for the shopper to download (US-11), and when it was made."""

    exported_at: AwareDatetime
    items: list[PreferenceItem] = Field(default_factory=list, max_length=MAX_PREFERENCES)
