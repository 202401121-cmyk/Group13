"""Build rule 8 for US-06: nothing a shopper typed or the app remembers reaches any provider (P-40, D-51, D-52).

Memory makes no LLM call at all: the embeddings come from a model on our own server. The only network it ever uses is
the first download of the public model's files, which names the repository, revision and file, and never a token or a
shopper's text."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.contracts import CriteriaInput, Intent, InterviewState
from app.criteria import fallback_set
from app.interview.rubric_builder import build_rubric
from app.memory import InMemoryPreferenceStore, MemoryService, MemorySettings
from app.memory.embedder import MemoryUnavailable, load_embedder
from app.memory.settings import load_memory_settings
from tests.unit.memory.fakes import WordEmbedder, ready_holder
from tests.unit.memory.test_text_and_lasting import answered

MEMORY = Path(__file__).resolve().parents[3] / "app" / "memory"
# app.llm reaches every provider through httpx; memory must import neither, nor any other HTTP library or SDK
NEVER = ("app.llm", "httpx", "requests", "urllib", "aiohttp", "groq", "google", "mistralai", "cerebras")


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("path", sorted(MEMORY.glob("*.py")), ids=lambda p: p.name)
def test_memory_imports_no_llm_client_and_no_http_library(path: Path) -> None:
    for name in imports_of(path):
        assert not any(name == bad or name.startswith(bad + ".") for bad in NEVER), (path.name, name)


def test_a_save_and_a_recall_embed_only_on_this_server() -> None:
    """The service embeds with its own model: the texts reach the embedder in this process, and nothing else."""
    embedder = WordEmbedder()
    memory = MemoryService(MemorySettings(), ready_holder(embedder), InMemoryPreferenceStore())
    criteria = fallback_set(CriteriaInput(category="laptop", category_label="Laptop"))
    intent = Intent(
        raw_query="a made-up private request", category="laptop", category_label="Laptop", detected_by="rules"
    )
    state = answered(
        InterviewState(session_id="sess_privacytest01", intent=intent, criteria=criteria), "battery_life", 8.0
    )
    user = uuid.uuid4()
    memory.remember(user, state, build_rubric(state))
    memory.recall(user, state)
    sent = [text for call in embedder.calls for text in call]
    assert sent and all("made-up private request" not in text for text in sent)  # the request itself is never embedded


def test_the_only_download_names_the_public_model_and_never_a_token_or_text(tmp_path: Path) -> None:
    settings = load_memory_settings(env={})
    asked: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> str:
        asked.append(kwargs)
        raise OSError("stop")

    with pytest.raises(MemoryUnavailable):
        load_embedder(settings, tmp_path, download=record)
    assert {call["repo_id"] for call in asked} == {settings.repo}
    assert {call["filename"] for call in asked} <= {settings.model_file, settings.tokenizer_file}
    assert all(call["token"] is False for call in asked)
