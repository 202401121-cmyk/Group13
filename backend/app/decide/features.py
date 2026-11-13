"""The features a shopper can rule out (US-10, S3-9): config/exclusions.yaml's names and their other spellings.

Plain code with no model. Intake reads a typed exclusion with this table, the search asks extract and verify for each
known feature's key, and decide reads a value under that same key, so all three agree on one spelling per feature:
"touch screen" and "touchscreen" are both "has_touchscreen". It lives here because decide imports nothing outside
app/decide/ and app/contracts/ (build rule 2), and intake imports it from here.
"""

from __future__ import annotations

import logging
import re
from functools import cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

FEATURES_PATH = Path(__file__).resolve().parents[2] / "config" / "exclusions.yaml"
MAX_KEY_CHARS = 64  # a criterion key's limit (Slug)
_WORD = re.compile(r"[a-z0-9]+")
_FEATURE_KEY = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class _Feature(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    also: list[str] = Field(default_factory=list)


class _FeatureFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    features: list[_Feature] = Field(min_length=1)


def _plain(text: str) -> str:
    return " ".join(text.casefold().split())


@cache
def load_feature_words(path: Path = FEATURES_PATH) -> dict[str, str]:
    """Every way of writing a feature, in lowercase, mapped to the feature's own name."""
    data = _FeatureFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    return {_plain(form): f.name for f in data.features for form in (f.name, *f.also)}


def feature_name(item: str) -> str | None:
    """The feature's own name for any way of writing it, a plural "s" understood. Raises if the file is unusable."""
    features = load_feature_words()
    plain = _plain(item)
    for form in (plain, plain.removesuffix("s"), plain.removesuffix("es")):
        if form in features:
            return features[form]
    return None


def known_feature(value: str) -> str | None:
    """The feature's own name in config/exclusions.yaml, for any way of writing it. None if it isn't listed there, or
    the file can't be read: a feature is then never asked for, and nothing the shopper typed goes anywhere (US-46)."""
    try:
        return feature_name(value)
    except Exception as exc:  # a broken config file must not stop a search (build rule 9). The type only
        log.warning("feature words not read: %s", type(exc).__name__)
        return None


def feature_key(value: str) -> str | None:
    """The key a known feature's value is read under, "has_" and its own name's words: "has_touchscreen". None for a
    feature config/exclusions.yaml doesn't list."""
    feature = known_feature(value)
    if feature is None:
        return None
    key = "has_" + "_".join(_WORD.findall(feature.casefold()))
    return key if _FEATURE_KEY.fullmatch(key) and len(key) <= MAX_KEY_CHARS else None
