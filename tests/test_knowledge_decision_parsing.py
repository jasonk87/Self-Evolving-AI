import pytest

from ai_assistant.custom_tools import knowledge_tools
from ai_assistant.custom_tools.knowledge_tools import _extract_decision_json


@pytest.mark.parametrize(
    "response",
    [
        '{"decision":"ADD","reason":"new","target_id":null,"merged_fact":null}',
        '```json\n{"decision":"DISCARD","reason":"duplicate"}\n```',
        'I reviewed the facts. {"decision":"UPDATE","target_id":"fact-1","merged_fact":"updated"}',
    ],
)
def test_extract_decision_json_handles_model_wrappers(response):
    parsed = _extract_decision_json(response)

    assert parsed["decision"] in {"ADD", "DISCARD", "UPDATE"}


def test_extract_decision_json_rejects_empty_or_non_object_output():
    assert _extract_decision_json("") is None
    assert _extract_decision_json("[]") is None
    assert _extract_decision_json("I could not decide") is None


@pytest.mark.asyncio
async def test_fact_curation_uses_central_router_and_updates_memory(monkeypatch):
    saved = []
    ingested = []

    class FakeRag:
        async def retrieve_context(self, query, k, score_threshold):
            return [{"id": "fact-1", "text": "The user works nights."}]

        def generate_id(self, text):
            return "generated"

        def delete_fact_by_id(self, fact_id):
            assert fact_id == "fact-1"

        async def ingest_fact(self, fact, metadata=None):
            ingested.append((fact, metadata))

    async def fake_generate_response(prompt, **kwargs):
        assert kwargs["task_name"] == "fact_management"
        return '{"decision":"UPDATE","target_id":"fact-1","merged_fact":"The user works nights and weekends."}'

    async def fake_get_rag_system():
        return FakeRag()

    monkeypatch.setattr(knowledge_tools, "_get_rag_system", fake_get_rag_system)
    monkeypatch.setattr(knowledge_tools, "load_learned_facts", lambda: ["The user works nights."])
    monkeypatch.setattr(knowledge_tools, "save_learned_facts", lambda facts: saved.append(facts))
    monkeypatch.setattr(knowledge_tools.model_router, "generate_response", fake_generate_response)

    assert await knowledge_tools._curate_and_update_fact_store(["The user works nights and weekends."])
    assert saved == [["The user works nights and weekends."]]
    assert ingested[0][0] == "The user works nights and weekends."
