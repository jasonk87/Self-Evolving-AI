import asyncio

import pytest

from ai_assistant.core import memory_manager as memory_manager_module
from ai_assistant.core.memory_manager import MemoryManager
from ai_assistant.custom_tools import knowledge_tools


def _manager_without_initialization(rag_system=None):
    manager = MemoryManager.__new__(MemoryManager)
    manager.rag_system = rag_system
    manager._rag_sync_task = None
    manager._rag_sync_attempted_signature = None
    return manager


def test_recall_facts_handles_structured_and_legacy_records(monkeypatch):
    monkeypatch.setattr(
        knowledge_tools,
        "load_learned_facts",
        lambda: [
            {"fact_id": "fact_name", "text": "User's name is Jason Kinslow."},
            "User prefers systems-driven games.",
            {"fact_id": "empty", "text": ""},
        ],
    )

    assert knowledge_tools.recall_facts("name") == ["User's name is Jason Kinslow."]
    assert knowledge_tools.recall_facts() == [
        "User's name is Jason Kinslow.",
        "User prefers systems-driven games.",
    ]


@pytest.mark.asyncio
async def test_retrieval_uses_local_facts_when_vector_results_are_empty(monkeypatch):
    facts = [
        {
            "fact_id": "fact_name",
            "text": "User's name is Jason Kinslow.",
            "category": "user_personal_info",
        },
        {
            "fact_id": "fact_python",
            "text": "Python is installed.",
            "category": "system_config",
        },
    ]

    class FakeVectorStore:
        collection = object()

        def get_all(self):
            return [{"text": fact["text"]} for fact in facts]

    class FakeRag:
        vector_store = FakeVectorStore()

        async def retrieve_context(self, query, k):
            return []

    monkeypatch.setattr(memory_manager_module, "load_learned_facts", lambda: facts)
    manager = _manager_without_initialization(FakeRag())

    results = await manager.retrieve_relevant_context("What is my name?", k=3)

    assert results
    assert results[0]["text"] == "User's name is Jason Kinslow."


@pytest.mark.asyncio
async def test_empty_vector_index_is_synchronized_without_blocking_fallback(monkeypatch):
    facts = [
        {
            "fact_id": "fact_location",
            "text": "User lives in south-central Kentucky.",
            "category": "user_personal_info",
        }
    ]
    synced = []

    class FakeVectorStore:
        collection = object()

        def get_all(self):
            return []

    class FakeRag:
        vector_store = FakeVectorStore()

        async def retrieve_context(self, query, k):
            return []

        async def sync_existing_facts(self, missing):
            synced.extend(missing)

    monkeypatch.setattr(memory_manager_module, "load_learned_facts", lambda: facts)
    manager = _manager_without_initialization(FakeRag())

    results = await manager.retrieve_relevant_context("Where does the user live?", k=3)
    await asyncio.sleep(0)

    assert results[0]["text"] == "User lives in south-central Kentucky."
    assert synced == facts


@pytest.mark.asyncio
async def test_user_memory_inventory_prioritizes_personal_memory_and_excludes_debugging(monkeypatch):
    facts = [
        {"fact_id": "personal", "text": "User's name is Jason Kinslow.", "category": "user_personal_info"},
        {"fact_id": "preference", "text": "User prefers autonomous systems.", "category": "user_preference"},
        {"fact_id": "project", "text": "User is building Self Evolving AI.", "category": "project_context"},
        {"fact_id": "debug", "text": "A configuration key cannot be modified.", "category": "system_config"},
    ]
    monkeypatch.setattr(memory_manager_module, "load_learned_facts", lambda: facts)
    manager = _manager_without_initialization()

    results = await manager.retrieve_relevant_context("What all do you know about me?", k=3)
    texts = [result["text"] for result in results]

    assert texts == [
        "User's name is Jason Kinslow.",
        "User prefers autonomous systems.",
        "User is building Self Evolving AI.",
    ]
    assert "A configuration key cannot be modified." not in texts


@pytest.mark.asyncio
async def test_location_dependent_query_includes_known_user_location(monkeypatch):
    facts = [
        {
            "fact_id": "location",
            "text": "User lives in south-central Kentucky near Glasgow.",
            "category": "user_personal_info",
        },
        {
            "fact_id": "old_rule",
            "text": "Ask the user for a location before searching.",
            "category": "system_config",
        },
    ]
    monkeypatch.setattr(memory_manager_module, "load_learned_facts", lambda: facts)
    manager = _manager_without_initialization()

    results = await manager.retrieve_relevant_context("What is the weather today?", k=3)
    texts = [result["text"] for result in results]

    assert texts[0] == "User lives in south-central Kentucky near Glasgow."
    assert "Ask the user for a location before searching." not in texts
