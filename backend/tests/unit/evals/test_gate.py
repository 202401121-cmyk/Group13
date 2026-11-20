"""US-49's quality gate (S4-13): the recorded runs replayed offline, measured, and held to their floors and to main.

The synthetic recording below is made up for these tests: its page, products and replies name nothing real.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.contracts import ExtractionResult, Source, SourceType
from app.contracts.research import DEFAULT_TIER
from evals import gate, thresholds

T0 = "2026-11-19T04:00:00Z"
PAGE = "https://www.91mobiles.com/made-up-best-phones"
TEXT = (
    "Our made-up pick is the Nova Phone 7, with a sharp camera and a two-day battery. "
    "The Kite X2 is close behind, and the Opal Mini 3 is the budget choice."
)


def _source() -> dict[str, Any]:
    import hashlib

    return Source.model_validate(
        {
            "id": "src_" + hashlib.sha256(PAGE.encode()).hexdigest()[:16],
            "url": PAGE,
            "type": "review_site",
            "tier": DEFAULT_TIER[SourceType.REVIEW_SITE],
            "title": "Made-up best phones",
            "text": TEXT,
            "text_hash": hashlib.sha256(TEXT.encode()).hexdigest(),
            "fetched_at": T0,
            "site": "91mobiles.com",
            "via": "direct",
        }
    ).model_dump(mode="json")


def _rubric() -> dict[str, Any]:
    from app.contracts import examples as ex

    return ex.rubric().model_dump(mode="json")


def _reply(quote: str = "a sharp camera and a two-day battery") -> str:
    return json.dumps(
        {
            "mentions": [
                {"raw_name": "Nova Phone 7", "is_product": True, "brand": "Nova"},
                {"raw_name": "Kite X2", "is_product": True, "brand": "Kite"},
            ],
            "claims": [
                {
                    "product_mention": "Nova Phone 7",
                    "criterion_key": "battery_life",
                    "polarity": "positive",
                    "quote": quote,
                    "score": 8.0,
                }
            ],
        }
    )


def make_recording(root: Path, name: str = "gate_made_up", reply: str | None = None) -> Path:
    """A recording as record_run writes it, with one page and one extraction reply. The prompt is the one the real
    extract stage builds, taken from a first replay, so the reply is found the way a real recording's is."""
    folder = root / name
    folder.mkdir(parents=True)
    expected = ["Nova Phone 7", "Kite X2", "Opal Mini 3"]
    manifest = {"name": name, "request": "a made-up phone request", "expected_products": expected}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "pipeline.json").write_text(
        json.dumps(
            [
                {
                    "run": 1,
                    "rubric": _rubric(),
                    "sources": [_source()],
                    "shown": [],
                    "filtered_out": [],
                    "cant_tell": [],
                    "citations": 0,
                }
            ]
        ),
        encoding="utf-8",
    )
    prompt = _prompt_for(folder)
    row = {
        "status": 200,
        "request_body": {"messages": [{"role": "system", "content": "x"}, {"role": "user", "content": prompt}]},
        "response_body": {"choices": [{"message": {"role": "assistant", "content": reply or _reply()}}]},
    }
    (folder / "groq.json").write_text(json.dumps([row]), encoding="utf-8")
    return folder


def _prompt_for(folder: Path) -> str:
    """The user prompt the real extract stage sends for the recording's source."""
    seen: list[str] = []

    class Catch(gate.ReplayLLM):
        def reply_for(self, user: str, *, fresh_only: bool = False) -> str | None:
            seen.append(user)
            return None

    original = gate.ReplayLLM
    gate.ReplayLLM = Catch  # type: ignore[misc]
    try:
        gate.replay(folder)
    finally:
        gate.ReplayLLM = original  # type: ignore[misc]
    assert seen, "the extract stage sent the page to the LLM"
    return seen[0]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "recorded"


def test_a_replayed_recording_is_measured(root: Path) -> None:
    make_recording(root)
    record = gate.measure(root)
    (row,) = record["recordings"].values()
    assert (row["expected"], row["survived"], row["missed"]) == (3, 2, ["Opal Mini 3"])  # the reply never names it
    assert (row["claims"], row["claims_without_quote"]) == (1, 0)
    measures = {m["id"]: m["value"] for m in record["measures"]}
    assert measures["gate.expected_survival"] == round(2 / 3, 4)
    assert measures["gate.claims_kept"] == 1


def test_a_recording_with_a_quote_missing_fails_the_gate(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Extraction drops a quote that isn't on the page; were that check ever lost, the gate still catches it."""
    make_recording(root)
    real = gate.measure_one

    def without_the_check(rp: gate.Replay) -> dict[str, Any]:
        claim = rp.results[0].claims[0]
        forged = claim.model_validate({**claim.model_dump(), "quote": "words that are not on the page at all"})
        rp.results[0] = ExtractionResult.model_validate({**rp.results[0].model_dump(), "claims": [forged.model_dump()]})
        return real(rp)

    monkeypatch.setattr(gate, "measure_one", without_the_check)
    record = gate.measure(root)
    measures = {m["id"]: m for m in record["measures"]}
    assert measures["gate.claims_without_quote"]["value"] == 1
    assert [p for p in gate.floor_problems(record["measures"]) if "claims_without_quote" in p]


def test_quoted_reads_the_sources_own_words() -> None:
    assert gate.quoted("A  SHARP camera", "with a sharp camera and")
    assert not gate.quoted("a blurry camera", "with a sharp camera and")
    assert not gate.quoted("   ", "anything")


def test_survives_matches_a_name_both_ways() -> None:
    assert gate.survives("Nova Phone 7", ["Nova Phone 7 (Blue, 128 GB)"])
    assert gate.survives("Nova Phone 7 Pro", ["Nova Phone 7 Pro"])
    assert not gate.survives("Nova Phone 7 Pro", ["Nova Phone 8 Pro"])
    assert not gate.survives("Nova Phone 7", ["Phone"])  # one shared word is not the product


def _measures(**values: float) -> list[dict[str, Any]]:
    units = {"gate.expected_survival": "rate", "gate.claims_kept": "claims"}
    return [{"id": k, "value": v, "unit": units[k], "status": "measured"} for k, v in values.items()]


def test_a_drop_of_more_than_10_points_from_main_fails() -> None:
    main = _measures(**{"gate.expected_survival": 0.95})
    assert gate.against_main(_measures(**{"gate.expected_survival": 0.849}), main)  # 10.1 points
    assert gate.against_main(_measures(**{"gate.expected_survival": 0.85}), main) == []  # 10.0 points: not more


def test_a_count_falling_more_than_10_percent_from_main_fails() -> None:
    main = _measures(**{"gate.claims_kept": 100})
    assert gate.against_main(_measures(**{"gate.claims_kept": 89}), main)
    assert gate.against_main(_measures(**{"gate.claims_kept": 90}), main) == []


def test_a_floor_breach_fails() -> None:
    floor = thresholds.load(thresholds.THRESHOLDS).measures["gate.expected_survival"].floor
    breach = [{"id": "gate.expected_survival", "value": round(floor - 0.01, 4), "unit": "rate", "status": "measured"}]
    assert gate.floor_problems(breach) == [f"gate.expected_survival is below its floor: {breach[0]['value']} < {floor}"]


def test_the_gate_never_reaches_the_network() -> None:
    with gate.no_network(), pytest.raises(gate.NetworkRefused), httpx.Client() as http:
        http.get("https://example.com/")


def test_a_replay_calls_out_nowhere(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_recording(root)
    sent: list[httpx.Request] = []

    def record(transport: object, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise AssertionError("a real call")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", record)
    gate.measure(root)  # its own guard sits on top, and nothing reaches even this one
    assert sent == []


def test_a_prompt_with_no_recorded_reply_gets_no_answer(root: Path) -> None:
    folder = make_recording(root)
    (folder / "groq.json").write_text("[]", encoding="utf-8")
    (row,) = gate.measure(root)["recordings"].values()
    assert (row["claims"], row["survived"]) == (0, 0)


def test_a_retry_gets_the_next_recorded_reply() -> None:
    rows = [
        {"status": 200, "request_body": {"messages": [{"role": "user", "content": "p"}]},
         "response_body": {"choices": [{"message": {"content": reply}}]}}
        for reply in ("first", "second")
    ]  # fmt: skip
    llm = gate.ReplayLLM(rows)
    assert [llm.reply_for("p"), llm.reply_for("p"), llm.reply_for("p")] == ["first", "second", "second"]


def test_the_committed_record_is_what_the_gate_gives_now() -> None:
    committed = json.loads(gate.GATE_OUT.read_text(encoding="utf-8"))
    for key in ("generated", "commit"):
        committed.pop(key)
    assert committed == gate.measure()


def test_the_committed_recordings_meet_their_floors_and_the_labels_agree() -> None:
    record = gate.measure()
    assert len(record["recordings"]) == 5
    assert gate.floor_problems(record["measures"]) == []
    assert record["label_problems"] == []


def test_main_reports_its_time_and_fails_on_a_fall_from_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = json.loads(gate.GATE_OUT.read_text(encoding="utf-8"))
    for m in baseline["measures"]:
        if m["id"] == "gate.expected_survival":
            m["value"] = min(1.0, m["value"] + 0.2)
    path = tmp_path / "main.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    assert gate.main(["--baseline", str(path)]) == 1
    out = capsys.readouterr().out
    assert "The gate took" in out and "fell" in out


def test_main_passes_against_its_own_record(capsys: pytest.CaptureFixture[str]) -> None:
    assert gate.main(["--baseline", str(gate.GATE_OUT)]) == 0
    assert "The gate took" in capsys.readouterr().out


def test_a_copy_of_a_real_recording_replays_the_same(tmp_path: Path) -> None:
    folder = gate.recordings()[0]
    shutil.copytree(folder, tmp_path / folder.name)
    assert gate.measure(tmp_path)["recordings"][folder.name] == gate.measure()["recordings"][folder.name]


def test_the_reply_the_live_client_accepted_wins_over_the_raw_ones(root: Path) -> None:
    """A prompt can have several raw replies (a retry, the route's next provider); the one the run used is replayed."""
    folder = make_recording(root, reply=_reply(quote="words that are not on the page at all"))
    prompt = _prompt_for(folder)
    run = json.loads((folder / "pipeline.json").read_text(encoding="utf-8"))
    run[0]["llm_replies"] = [{"task": "extract_claims", **gate._hashes(prompt), "reply": json.loads(_reply())}]
    (folder / "pipeline.json").write_text(json.dumps(run), encoding="utf-8")
    (row,) = gate.measure(root)["recordings"].values()
    assert row["claims"] == 1  # the accepted reply's quote is on the page; the raw one's isn't, and would be dropped
