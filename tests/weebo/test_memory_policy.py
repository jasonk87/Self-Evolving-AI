import json

import pytest

from weebo import paths
from weebo.events import EventBus
from weebo.memory.memory import Memory, MemoryError_, looks_secret
from weebo.store import Store


@pytest.fixture
def memory(tmp_path):
    return Memory(Store(tmp_path / "m.db"), EventBus())


def test_remember_merges_near_duplicates(memory):
    first, action = memory.remember("User prefers dark mode in every app", "preference", 3)
    assert action == "added"
    second, action = memory.remember("User prefers dark mode in every app.", "preference", 5)
    assert action == "updated" and second["id"] == first["id"] and second["importance"] == 5
    assert memory.stats()["total"] == 1


@pytest.mark.parametrize("text", [
    "my password is hunter2",
    "API key: sk-abcdefghijklmnopqrstuv",
    "card 4111 1111 1111 1111",
    "ssn 123-45-6789",
])
def test_secrets_are_refused(memory, text):
    assert looks_secret(text)
    with pytest.raises(MemoryError_):
        memory.remember(text)


def test_innocent_numbers_are_fine():
    assert not looks_secret("My Discord server has 1234 members and I was born in 1987")


def test_context_block_includes_profile_and_related(memory):
    memory.remember("User's name is Jordan", "fact", 5)
    memory.remember("User is learning to play the cello", "goal", 3)
    memory.remember("User dislikes cilantro", "preference", 2)
    text, used = memory.context_block("any tips for practicing cello?")
    assert "Jordan" in text and "cello" in text
    assert used and all(u.startswith("mem_") for u in used)


def test_legacy_import_maps_categories(memory, tmp_path, monkeypatch):
    root = tmp_path / "project"
    data = root / "ai_assistant" / "core" / "data"
    data.mkdir(parents=True)
    (data / "learned_facts.json").write_text(json.dumps([
        {"text": "User prefers concise answers. (Evidence: USER: keep it short)", "category": "user_preference"},
        {"text": "Python executable path: C:/Python313/python.exe", "category": "system_config"},
        {"text": "User lives near the coast", "category": "user_personal_info"},
    ]))
    (data / "episodic_memories.json").write_text(json.dumps([
        {"title": "Garden plans", "summary": "The user planned a vegetable garden and asked about tomatoes."},
    ]))
    monkeypatch.setattr(paths, "PROJECT_ROOT", root)
    counts = memory.import_legacy()
    assert counts["facts"] == 2 and counts["episodes"] == 1 and counts["skipped"] == 1
    texts = [m["text"] for m in memory.store.list_memories()]
    assert "User prefers concise answers." in texts  # evidence suffix stripped
    assert memory.import_legacy() == {"facts": 0, "episodes": 0, "skipped": 0}  # only once


@pytest.mark.parametrize("path, tier", [
    ("weebo/web/js/chat.js", "autonomous"),
    ("tests/weebo/test_x.py", "autonomous"),
    ("weebo_data/skills/x/SKILL.md", "autonomous"),
    ("weebo/brain/persona.py", "human_required"),
    ("weebo/codex/rpc.py", "human_required"),
    ("weebo/evolution/engine.py", "human_required"),
    ("weebo/supervisor.py", "human_required"),
    ("weebo/selftest.py", "human_required"),
    ("weebo/integrations/sms.py", "human_required"),
    ("tests/weebo/conftest.py", "human_required"),  # the fake engine decides what "passing" means
    (".github/workflows/test-lanes.yml", "human_required"),
    ("pytest.ini", "human_required"),
    ("requirements-core.txt", "human_required"),  # a new dependency is a trust decision, not "docs"
    ("README.md", "autonomous"),
    ("somewhere/else.py", "blocked"),
    ("../outside.py", "blocked"),
])
def test_change_policy_covers_weebo(path, tier):
    from weebo.evolution.policy import decide_governance
    assert decide_governance(path).tier.value == tier


def test_deleting_tests_needs_a_human():
    from weebo.evolution.policy import decide_governance
    assert decide_governance("tests/weebo/test_x.py", "delete").tier.value == "human_required"
    assert decide_governance("weebo_data/skills/x/SKILL.md", "delete").tier.value == "autonomous"
