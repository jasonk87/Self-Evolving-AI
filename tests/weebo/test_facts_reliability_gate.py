import pytest

from weebo import paths
from weebo.evolution.reliability import check_reliability

pytestmark = pytest.mark.asyncio


async def test_reliability_suite_runs_without_models():
    result = await check_reliability(paths.PROJECT_ROOT)
    assert result["ok"], result["summary"]
    assert "5 reliability scenarios passed" in result["summary"]


async def test_missing_candidate_facts_blocks_even_with_no_behavior_cases(app, tmp_path):
    # An incomplete/older candidate cannot silently skip the new mandatory suite.
    (tmp_path / "weebo").mkdir()
    (tmp_path / "weebo" / "__init__.py").write_text("")
    verdict = await app.evals.gate(tmp_path, count=False)
    assert not verdict["ok"] and not verdict["skipped"]
    assert "weebo.facts" in verdict["summary"]


async def test_factual_correction_creates_incident_and_eval(app):
    conv = app.conversations.desk()
    app.store.add_message(conv["id"], "user", "When is the planning meeting?", turn_id="turn-one")
    app.store.add_message(conv["id"], "assistant", "At 3pm.", turn_id="turn-one", data={"trigger": "user"})
    case = app.evals.capture_correction(conv["id"], "That's wrong, it moved to 4pm.")
    assert case and case["source"] == "correction"
    incidents = app.store.list_journal(kinds=["reliability.incident"])
    assert incidents[0]["data"]["case_id"] == case["id"]
