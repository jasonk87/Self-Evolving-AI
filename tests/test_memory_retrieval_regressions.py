import asyncio
import json

import pytest

from ai_assistant.core import memory_manager as memory_manager_module
from ai_assistant.core.memory_manager import MemoryManager
from ai_assistant.custom_tools import knowledge_tools
from ai_assistant.memory.rag_system import RAGSystem


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
async def test_stale_vector_results_are_filtered_and_reconciled(monkeypatch):
    facts = [{"text": "User prefers concise status reports.", "category": "user_preference"}]
    reconciled = []

    class FakeVectorStore:
        collection = object()

        def get_all(self):
            return [
                {"id": "current", "text": facts[0]["text"]},
                {"id": "stale", "text": "User prefers long technical walls of text."},
            ]

    class FakeRag:
        vector_store = FakeVectorStore()

        async def retrieve_context(self, query, k):
            return [
                {"id": "stale", "text": "User prefers long technical walls of text."},
                {"id": "current", "text": facts[0]["text"]},
            ]

        async def reconcile_facts(self, canonical):
            reconciled.extend(canonical)

    monkeypatch.setattr(memory_manager_module, "load_learned_facts", lambda: facts)
    manager = _manager_without_initialization(FakeRag())

    results = await manager.retrieve_relevant_context("How should status reports look?", k=3)
    await asyncio.sleep(0)

    assert [result["text"] for result in results] == [facts[0]["text"]]
    assert reconciled == facts


def test_relevant_heuristics_include_always_active_and_matching_rules(monkeypatch, tmp_path):
    heuristics = [
        {
            "heuristic": "Always be direct about what was actually verified.",
            "trigger_context": "always_active",
            "created_at": "2026-07-18T10:00:00+00:00",
        },
        {
            "heuristic": "For coding work, run targeted tests before reporting success.",
            "trigger_context": "general_planning",
            "created_at": "2026-07-18T11:00:00+00:00",
        },
        {
            "heuristic": "For restaurant searches, compare opening hours.",
            "trigger_context": "general_planning",
            "created_at": "2026-07-18T12:00:00+00:00",
        },
    ]
    (tmp_path / "planning_heuristics.json").write_text(json.dumps(heuristics), encoding="utf-8")
    monkeypatch.setattr(memory_manager_module, "get_data_dir", lambda: str(tmp_path))
    manager = _manager_without_initialization()

    results = manager.retrieve_relevant_heuristics("Fix this coding bug and run tests", k=6)
    texts = [item["heuristic"] for item in results]

    assert "Always be direct about what was actually verified." in texts
    assert "For coding work, run targeted tests before reporting success." in texts
    assert "For restaurant searches, compare opening hours." not in texts


@pytest.mark.asyncio
async def test_rag_reconciliation_removes_stale_and_adds_missing_facts():
    class FakeVectorStore:
        def __init__(self):
            self.deleted = []

        def _generate_id(self, text):
            return text.casefold()

        def get_all(self):
            return [
                {"id": "kept fact", "text": "Kept fact"},
                {"id": "stale fact", "text": "Stale fact"},
            ]

        def delete(self, ids):
            self.deleted.extend(ids)

    rag = RAGSystem.__new__(RAGSystem)
    rag.vector_store = FakeVectorStore()
    synced = []

    async def sync_existing_facts(facts):
        synced.extend(facts)

    rag.sync_existing_facts = sync_existing_facts
    canonical = [{"text": "Kept fact"}, {"text": "New fact"}]

    result = await rag.reconcile_facts(canonical)

    assert rag.vector_store.deleted == ["stale fact"]
    assert synced == [{"text": "New fact"}]
    assert result == {"added": 1, "removed": 1}


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
