"""The backend the merge browser test runs against (US-50, DECIDED 2): the real app and its API, with fake LLM
providers, the search's stub stages and the database off. No key is read and no call leaves the machine.

- **Fake providers:** the same fake LLM client the test suite uses (tests/fakes/llm.py). With no canned answer it
  answers as every provider down would, so intake takes its keyword rules, criteria their fixed family set, and the
  interview its fixed questions: what the shopper sees when the providers are down, never a made-up answer.
- **The search:** every stage as its Sprint 1 stub (research's example sources, extract's example claims, decide's
  example ranking), so a result always comes back, labelled a sample.
- **The database is off:** nothing is saved.

    python -m tests.worst_case.fake_stack --port 8100 --frontend-origin http://localhost:3200   (from backend/)
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence

from fastapi import FastAPI

import app.main as app_main
import app.presearch as presearch_flow
from app.api import SearchRuns, load_run_settings
from app.api.presearch import get_llm, get_request_store
from app.commerce import CommerceStage
from app.db import NullRequestStore
from app.decide import DecideStage
from app.enrich import EnrichStage
from app.entities import MatchStage
from app.explain import TemplateExplainStage
from app.extract import ExtractStage
from app.main import Housekeeping, create_app
from app.pipeline import DatabaseResultStore, PipelineRunner, SearchStages
from app.research import ResearchStage
from app.verify import VerifyStage
from tests.fakes.llm import FakeLLMClient

# Process settings for the launcher only (never set by the tests that build the app): no database, no model downloads,
# no Reddit archive, whatever a developer's .env holds. An empty value wins over the .env file.
OFFLINE = {"DATABASE_URL": "", "MEMORY": "off", "MATCHER": "fuzzy", "NLI": "off", "REDDIT_SOURCE": "off"}


def stub_stages() -> SearchStages:
    """Every search stage as its Sprint 1 stub; explain's template, which has no stub left (US-20)."""
    return SearchStages(
        research=ResearchStage(),
        extract=ExtractStage(),
        match=MatchStage(),
        verify=VerifyStage(),
        enrich=EnrichStage(),
        decide=DecideStage(),
        explain=TemplateExplainStage(),
        commerce=CommerceStage(),
    )


def build_app(frontend_origin: str = "", stages: Callable[[], SearchStages] = stub_stages) -> FastAPI:
    """The app with fake providers, `stages` for every search and the database off."""
    app = create_app(env={"FRONTEND_ORIGINS": frontend_origin} if frontend_origin else {})
    llm = FakeLLMClient()
    requests = NullRequestStore()
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_request_store] = lambda: requests
    app.state.search_runs.close()
    saves = DatabaseResultStore(requests)  # the database off: nothing is saved, and the result says so
    app.state.search_runs = SearchRuns(lambda: PipelineRunner(stages(), saves), load_run_settings({}))
    app.state.housekeeping = Housekeeping(app.state.search_runs)
    return app


def main(argv: Sequence[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m tests.worst_case.fake_stack")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--frontend-origin", default="http://localhost:3200")
    args = parser.parse_args(argv)
    os.environ.update(OFFLINE)
    # This process only: the interview's shared client (asked for when a typed reply needs it) is the fake one too, and
    # the plain POST /api/search route runs the stubs as the run routes do, so no typed reply or search reaches a
    # real provider, whatever keys the machine holds.
    fake = FakeLLMClient()
    presearch_flow.default_llm = lambda: fake
    app_main.default_search_stages = stub_stages
    uvicorn.run(build_app(args.frontend_origin), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
