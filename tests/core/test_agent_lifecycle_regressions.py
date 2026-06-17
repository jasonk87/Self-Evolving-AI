import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_assistant.code_services.service import CodeService
from ai_assistant.core.agent_manager import AgentManager
from ai_assistant.core.approval_manager import ApprovalManager
from ai_assistant.core import background_service
from ai_assistant.core.task_manager import ActiveTaskStatus, ActiveTaskType, TaskManager
from ai_assistant.custom_tools import agent_tools
from ai_assistant.custom_tools import search_tools
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.goals import goal_management


def test_background_goal_is_persisted_and_queued_immediately(tmp_path, monkeypatch):
    goals_path = tmp_path / "goals.json"
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(goals_path))
    monkeypatch.setattr(goal_management, "_goals_db", {})

    result = agent_tools.spawn_background_agent("Monitor browser automation", session_id="session-1")
    goal_id = next(iter(goal_management._goals_db))

    assert goal_id in result
    assert json.loads(goals_path.read_text(encoding="utf-8"))[goal_id]["status"] == "pending"
    assert goal_management.get_goal(goal_id)["status"] == "pending"
    assert "Queued for execution" in result


def test_spawn_ephemeral_agent_queues_real_background_goal(tmp_path, monkeypatch):
    goals_path = tmp_path / "goals.json"
    manager = AgentManager(base_path=str(tmp_path / "temp_agents"))
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(goals_path))
    monkeypatch.setattr(goal_management, "_goals_db", {})
    monkeypatch.setattr(agent_tools, "agent_manager", manager)

    result = agent_tools.spawn_ephemeral_agent(
        "Investigate the LLM Call project",
        scope_type="persistent",
        session_id="chat-42",
    )
    goal = next(iter(goal_management._goals_db.values()))

    assert result["queued"] == "true"
    assert result["scope"] == "user"
    assert result["goal_id"] == goal["id"]
    assert goal["status"] == "pending"
    assert goal["metadata"]["type"] == "background_agent"
    assert goal["metadata"]["routed_agent_id"] == result["agent_id"]
    assert goal["metadata"]["source_session_id"] == "chat-42"
    assert (tmp_path / "temp_agents" / result["agent_id"] / "metadata.json").exists()


def test_spawn_ephemeral_agent_workspace_only_mode_for_executor(tmp_path, monkeypatch):
    goals_path = tmp_path / "goals.json"
    manager = AgentManager(base_path=str(tmp_path / "temp_agents"))
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(goals_path))
    monkeypatch.setattr(goal_management, "_goals_db", {})
    monkeypatch.setattr(agent_tools, "agent_manager", manager)

    result = agent_tools.spawn_ephemeral_agent("Run generated code", queue_task=False)

    assert "goal_id" not in result
    assert result["scope"] == "session"
    assert goal_management._goals_db == {}


def test_autonomous_goal_is_queued_immediately(tmp_path, monkeypatch):
    goals_path = tmp_path / "goals.json"
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(goals_path))
    monkeypatch.setattr(goal_management, "_goals_db", {})

    goal = goal_management.create_goal("Autonomous proposal", "Run this")

    assert goal["status"] == "pending"
    assert json.loads(goals_path.read_text(encoding="utf-8"))[goal["id"]]["status"] == "pending"


def test_background_approval_helper_rejects_non_background_goal(tmp_path, monkeypatch):
    goals_path = tmp_path / "goals.json"
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(goals_path))
    monkeypatch.setattr(goal_management, "_goals_db", {})

    goal = goal_management.create_goal("Legacy proposal", "Release this", status="PENDING_APPROVAL")

    assert "not a background-agent launch" in agent_tools.approve_background_goal(goal["id"])
    assert goal_management.get_goal(goal["id"])["status"] == "PENDING_APPROVAL"


def test_startup_releases_legacy_background_launch_but_preserves_source_change_gate(tmp_path, monkeypatch):
    goals_path = tmp_path / "goals.json"
    goals_path.write_text(json.dumps({
        "background": {
            "id": "background",
            "title": "Legacy background",
            "description": "Run",
            "status": "PENDING_APPROVAL",
            "priority": "HIGH",
            "metadata": {"type": "background_agent"},
        },
        "source-change": {
            "id": "source-change",
            "title": "Legacy architect proposal",
            "description": "Review",
            "status": "PENDING_APPROVAL",
            "priority": "HIGH",
        },
    }), encoding="utf-8")
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(goals_path))
    monkeypatch.setattr(goal_management, "_goals_db", {})

    goal_management._initialize_goals_db()

    assert goal_management.get_goal("background")["status"] == "pending"
    assert goal_management.get_goal("source-change")["status"] == "PENDING_APPROVAL"
    assert goal_management.get_goal("source-change")["metadata"]["type"] == "architect_source_change"
    assert json.loads(goals_path.read_text(encoding="utf-8"))["background"]["status"] == "pending"


def test_source_change_proposals_have_a_separate_ui_only_approval_surface(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(tmp_path / "goals.json"))
    monkeypatch.setattr(goal_management, "_goals_db", {})
    goal = goal_management.create_goal(
        "Architect source change",
        "Modify source code",
        status="PENDING_APPROVAL",
        metadata={"type": "architect_source_change", "requires_user_approval": True},
    )

    assert goal["id"] in agent_tools.list_pending_source_change_proposals()
    assert "queued for execution" in agent_tools._approve_source_change_proposal_from_ui(goal["id"])
    assert goal_management.get_goal(goal["id"])["status"] == "pending"


def test_background_status_lookup_reports_finished_one_shot_without_monitor_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(tmp_path / "goals.json"))
    monkeypatch.setattr(goal_management, "_goals_db", {})
    goal = goal_management.create_goal(
        "Rental lookup",
        "Look for a three-bedroom house to rent in Smiths Grove, KY.",
        status="completed",
        metadata={"type": "background_agent", "execution_mode": "one_shot", "created_at": time.time()},
    )

    from ai_assistant.custom_tools.introspection_tools import find_background_agent_status
    result = find_background_agent_status()

    assert goal["id"] in result
    assert "Durable status: completed" in result
    assert "No continuous monitor or scheduled search is active." in result


def test_source_change_approval_types_never_auto_approve():
    assert background_service._requires_manual_source_approval("architect_proposal")
    assert not background_service._requires_manual_source_approval("tool_fix")
    assert not background_service._requires_manual_source_approval("tool_modification")
    assert not background_service._requires_manual_source_approval("add_learned_fact")




def test_spawn_background_agent_deduplicates_active_goal(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(tmp_path / "goals.json"))
    monkeypatch.setattr(goal_management, "_goals_db", {})

    first = agent_tools.spawn_background_agent("Monitor browser automation")
    second = agent_tools.spawn_background_agent("Monitor browser automation")

    assert len(goal_management._goals_db) == 1
    assert "already exists" in second
    assert next(iter(goal_management._goals_db)) in first


def test_wake_agent_queues_routed_goal_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(tmp_path / "goals.json"))
    monkeypatch.setattr(goal_management, "_goals_db", {})
    manager = AgentManager(base_path=str(tmp_path / "temp_agents"))
    agent_id = manager.create_workspace("persistent files", agent_id="persistent-files", scope_type="user")
    monkeypatch.setattr(agent_tools, "agent_manager", manager)

    result = agent_tools.wake_agent(agent_id, "Find the report", session_id="chat-1")
    goal = next(iter(goal_management._goals_db.values()))

    assert goal["status"] == "pending"
    assert goal["metadata"]["routed_agent_id"] == agent_id
    assert goal["metadata"]["source_session_id"] == "chat-1"
    assert "queued for execution" in result


def test_autonomous_processor_bounds_concurrency_and_prioritizes_agent_launches(monkeypatch):
    goals = [
        {"id": "proposal-1", "description": "Proposal one", "status": "pending"},
        {"id": "agent-1", "description": "Agent launch", "status": "pending", "metadata": {"type": "background_agent"}},
        {"id": "proposal-2", "description": "Proposal two", "status": "pending"},
    ]
    updated = []

    monkeypatch.setattr(background_service, "_orchestrator", SimpleNamespace(learning_agent=None))
    monkeypatch.setattr(background_service.goal_management, "list_goals", lambda status=None: goals if status == "pending" else [])
    monkeypatch.setattr(background_service.goal_management, "update_goal_status", lambda goal_id, status: updated.append((goal_id, status)) or True)
    monkeypatch.setattr(background_service, "_claim_autonomous_goal", lambda goal_id: True)
    monkeypatch.setattr(background_service.asyncio, "create_task", lambda coroutine: coroutine.close())

    asyncio.run(background_service.run_autonomous_goal_processor())

    assert updated == [("agent-1", "in_progress"), ("proposal-1", "in_progress")]


def test_autonomous_processor_reports_unexpected_failure_to_originating_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_management, "DEFAULT_GOALS_FILE", str(tmp_path / "goals.json"))
    monkeypatch.setattr(goal_management, "_goals_db", {})
    goal = goal_management.create_goal(
        "Rental lookup",
        "Find a rental",
        status="pending",
        metadata={"type": "background_agent", "source_session_id": "chat-1"},
    )
    delivered = []
    scheduled = []

    class FailingOrchestrator:
        learning_agent = None

        async def process_prompt(self, **kwargs):
            raise RuntimeError("test crash")

    async def fake_broadcast(session_id, message, title="Agent Report"):
        delivered.append((session_id, message, title))

    monkeypatch.setattr(background_service, "_orchestrator", FailingOrchestrator())
    monkeypatch.setattr(background_service, "_claim_autonomous_goal", lambda goal_id: True)
    monkeypatch.setattr(background_service, "_release_autonomous_goal_claim", lambda goal_id: None)
    monkeypatch.setattr(background_service, "broadcast_agent_message", fake_broadcast)
    monkeypatch.setattr(background_service.asyncio, "create_task", lambda coroutine: scheduled.append(coroutine))

    asyncio.run(background_service.run_autonomous_goal_processor())
    asyncio.run(scheduled[0])

    saved_goal = goal_management.get_goal(goal["id"])
    assert saved_goal["status"] == "failed"
    assert "test crash" in saved_goal["metadata"]["result_summary"]
    assert delivered[0][0] == "chat-1"
    assert "failed unexpectedly" in delivered[0][1]


def test_queued_goal_runs_before_idle_cadence(monkeypatch):
    monkeypatch.setattr(background_service.goal_management, "list_goals", lambda status=None: [{"id": "queued"}] if status == "pending" else [])

    assert background_service.should_run_autonomous_goal_processor(10, 100)


def test_autonomous_goal_claim_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(background_service, "get_data_dir", lambda: str(tmp_path))

    assert background_service._claim_autonomous_goal("goal-1")
    assert not background_service._claim_autonomous_goal("goal-1")
    background_service._release_autonomous_goal_claim("goal-1")
    assert background_service._claim_autonomous_goal("goal-1")
    background_service._release_autonomous_goal_claim("goal-1")


def test_roster_prunes_stale_session_agents_but_preserves_user_agents(tmp_path, monkeypatch):
    manager = AgentManager(base_path=str(tmp_path / "temp_agents"))
    stale_session_id = manager.create_workspace("old session", agent_id="old-session")
    current_session_id = manager.create_workspace("current session", agent_id="current-session")
    user_agent_id = manager.create_workspace("persistent files", agent_id="persistent-files", scope_type="user")

    stale_metadata_path = tmp_path / "temp_agents" / stale_session_id / "metadata.json"
    stale_metadata = json.loads(stale_metadata_path.read_text(encoding="utf-8"))
    stale_metadata["created_at"] = time.time() - 7200
    stale_metadata_path.write_text(json.dumps(stale_metadata), encoding="utf-8")
    monkeypatch.setattr(agent_tools, "agent_manager", manager)

    roster = agent_tools.list_active_agents()

    assert not (tmp_path / "temp_agents" / stale_session_id).exists()
    assert current_session_id in roster
    assert user_agent_id in roster
    assert "Removed 1 stale session workspace(s)." in roster


def test_roster_does_not_describe_idle_persistent_workspace_as_active(tmp_path, monkeypatch):
    manager = AgentManager(base_path=str(tmp_path / "temp_agents"))
    agent_id = manager.create_workspace("file access", agent_id="persistent-files", scope_type="user")
    monkeypatch.setattr(agent_tools, "agent_manager", manager)
    monkeypatch.setattr(goal_management, "_goals_db", {})

    roster = agent_tools.list_active_agents()

    assert agent_id in roster
    assert "No agents are currently running or queued for a task." in roster
    assert "Available agent workspaces (not currently working):" in roster
    assert "Goal status: available" in roster


def test_background_report_persists_without_socket_broadcaster(monkeypatch):
    captured = {}

    class FakeChatSessionManager:
        def __init__(self, storage_path):
            captured["storage_path"] = storage_path

        def add_message(self, session_id, role, content):
            captured["message"] = (session_id, role, content)

    monkeypatch.setattr("ai_assistant.core.chat_manager.ChatSessionManager", FakeChatSessionManager)
    monkeypatch.setattr(background_service, "_socket_broadcaster", None)
    monkeypatch.setattr("app_globals.latest_active_chat_session_id", None)

    asyncio.run(background_service.broadcast_agent_message("chat-1", "Finished.", title="Agent Report"))

    assert captured["message"] == ("chat-1", "assistant", "**Agent Report**\n\nFinished.")


def test_background_report_targets_latest_active_chat(monkeypatch):
    captured = {"messages": [], "events": []}

    class FakeChatSessionManager:
        def __init__(self, storage_path):
            captured["storage_path"] = storage_path

        def get_session(self, session_id):
            return {"id": session_id} if session_id in {"chat-1", "chat-2"} else None

        def add_message(self, session_id, role, content):
            captured["messages"].append((session_id, role, content))

    async def fake_broadcaster(event_name, payload):
        captured["events"].append((event_name, payload))

    monkeypatch.setattr("ai_assistant.core.chat_manager.ChatSessionManager", FakeChatSessionManager)
    monkeypatch.setattr(background_service, "_socket_broadcaster", fake_broadcaster)
    monkeypatch.setattr("app_globals.latest_active_chat_session_id", "chat-2")

    asyncio.run(background_service.broadcast_agent_message("chat-1", "Finished.", title="Agent Report"))

    session_id, role, content = captured["messages"][0]
    assert session_id == "chat-2"
    assert role == "assistant"
    assert "Earlier background agent task completed" in content
    assert "Finished." in content
    event_name, payload = captured["events"][0]
    assert event_name == "agent_message"
    assert payload["session_id"] == "chat-2"
    assert payload["source_session_id"] == "chat-1"


def test_background_report_falls_back_to_latest_chat(monkeypatch):
    captured = {"messages": []}

    class FakeChatSessionManager:
        def __init__(self, storage_path):
            pass

        def list_sessions(self):
            return [{"id": "latest-chat", "updated_at": 100}]

        def add_message(self, session_id, role, content):
            captured["messages"].append((session_id, role, content))

    monkeypatch.setattr("ai_assistant.core.chat_manager.ChatSessionManager", FakeChatSessionManager)
    monkeypatch.setattr(background_service, "_socket_broadcaster", None)
    monkeypatch.setattr("app_globals.latest_active_chat_session_id", None)

    asyncio.run(background_service.broadcast_scheduled_message("Job finished.", title="Scheduled Job"))

    assert captured["messages"] == [
        ("latest-chat", "assistant", "**Scheduled Job**\n\nJob finished.")
    ]


def test_background_report_creates_system_updates_chat_when_no_target(monkeypatch):
    captured = {"messages": [], "created_titles": []}

    class FakeChatSessionManager:
        def __init__(self, storage_path):
            pass

        def list_sessions(self):
            return []

        def create_session(self, title="New Chat"):
            captured["created_titles"].append(title)
            return "system-chat"

        def add_message(self, session_id, role, content):
            captured["messages"].append((session_id, role, content))

    monkeypatch.setattr("ai_assistant.core.chat_manager.ChatSessionManager", FakeChatSessionManager)
    monkeypatch.setattr(background_service, "_socket_broadcaster", None)
    monkeypatch.setattr("app_globals.latest_active_chat_session_id", None)

    asyncio.run(background_service.broadcast_scheduled_message("Reminder due.", title="Scheduled Reminder"))

    assert captured["created_titles"] == ["System Updates"]
    assert captured["messages"] == [
        ("system-chat", "assistant", "**Scheduled Reminder**\n\nReminder due.")
    ]


def test_due_reminders_are_broadcast_to_active_chat(monkeypatch):
    captured = {"notifications": [], "reports": []}

    class FakeNotificationManager:
        def add_notification(self, **kwargs):
            captured["notifications"].append(kwargs)

    async def fake_broadcast(message, title="Scheduled Task"):
        captured["reports"].append((title, message))

    monkeypatch.setattr(background_service, "check_due_reminders", lambda: [{
        "message": "stretch",
        "target_time": "2026-06-17T10:00:00",
    }])
    monkeypatch.setattr(background_service, "broadcast_scheduled_message", fake_broadcast)

    count = asyncio.run(background_service.check_and_broadcast_due_reminders(FakeNotificationManager()))

    assert count == 1
    assert captured["notifications"][0]["summary_message"] == "REMINDER: stretch"
    assert captured["reports"] == [
        ("Scheduled Reminder", "Reminder due: stretch\n\nScheduled for: 2026-06-17T10:00:00")
    ]


def test_agent_workspace_rejects_paths_outside_base_directory(tmp_path):
    manager = AgentManager(base_path=str(tmp_path / "temp_agents"))

    with pytest.raises(ValueError):
        manager.create_workspace("escape", agent_id="..\\outside")


def test_approval_timestamps_are_seconds_and_old_milliseconds_are_normalized(tmp_path):
    manager = ApprovalManager()
    manager.filepath = str(tmp_path / "pending_approvals.json")
    manager.pending_requests = {}
    manager.callbacks = {}

    req_id = manager.add_request("test", {}, "seconds")
    assert abs(manager.get_request(req_id)["timestamp"] - time.time()) < 2

    manager.pending_requests[req_id]["timestamp"] *= 1000
    assert manager.get_pending_requests()[0]["timestamp"] < 10_000_000_000


def test_terminal_task_does_not_reappear_after_wal_recovery(tmp_path):
    task_path = tmp_path / "active_tasks.json"
    manager = TaskManager(filepath=str(task_path))
    task = manager.add_task("done", ActiveTaskType.LEARNING_NEW_FACT)

    manager.update_task_status(task.task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY)
    reloaded = TaskManager(filepath=str(task_path))

    assert reloaded.get_task(task.task_id) is None
    assert reloaded.list_active_tasks() == []


def test_task_manager_uses_shared_configured_data_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_assistant.core.task_manager.get_data_dir", lambda: str(tmp_path))

    manager = TaskManager()
    manager.add_task("shared store", ActiveTaskType.LEARNING_NEW_FACT)

    assert manager.active_tasks_filepath == str(tmp_path / "active_tasks.json")
    assert (tmp_path / "active_tasks.json").exists()


def test_add_planning_heuristic_persists_without_tool_modification(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_assistant.execution.action_executor.get_data_dir", lambda: str(tmp_path))
    executor = ActionExecutor(learning_agent=SimpleNamespace(), task_manager=None, notification_manager=None)

    result = asyncio.run(executor.execute_action({
        "action_type": "ADD_PLANNING_HEURISTIC",
        "details": {"heuristic": "Use the requested search provider.", "trigger_context": "search"},
    }))

    assert result is True
    saved = json.loads((tmp_path / "planning_heuristics.json").read_text(encoding="utf-8"))
    assert saved[0]["heuristic"] == "Use the requested search provider."


def test_add_learned_fact_uses_datetime_module_correctly(monkeypatch):
    executor = ActionExecutor(learning_agent=SimpleNamespace(), task_manager=None, notification_manager=None)
    monkeypatch.setattr(executor, "_assess_and_categorize_fact", lambda fact: asyncio.sleep(0, result=(True, "valuable", "test")))
    monkeypatch.setattr("ai_assistant.execution.action_executor.load_learned_facts", lambda: [])
    captured = {}
    def _save(facts):
        captured["facts"] = facts
        return True
    monkeypatch.setattr("ai_assistant.execution.action_executor.save_learned_facts", _save)
    monkeypatch.setattr("ai_assistant.execution.action_executor.mark_suggestion_implemented", lambda *args, **kwargs: True)

    result = asyncio.run(executor.execute_action({
        "action_type": "ADD_LEARNED_FACT",
        "details": {"fact_to_learn": "The user's preferred provider is Google."},
    }))

    assert result is True
    assert captured["facts"][0]["created_at"]


def test_execute_deep_research_is_async_compatible(monkeypatch):
    perform = AsyncMock(return_value={"summary": "done", "sources": []})
    monkeypatch.setattr("ai_assistant.core.deep_research.DeepResearcher.perform_deep_research", perform)

    result = asyncio.run(search_tools.execute_deep_research("query"))

    assert result == "done"
    perform.assert_awaited_once_with("query")


def test_code_service_supports_architect_whole_file_context():
    provider = SimpleNamespace(invoke_ollama_model_async=AsyncMock(return_value="print('updated')"))
    service = CodeService(llm_provider=provider)

    result = asyncio.run(service.modify_code(
        context="ARCHITECT_EVOLUTION",
        modification_instruction="Improve structure.",
        existing_code="print('old')",
        module_path=None,
        function_name=None,
    ))

    assert result["status"] == "SUCCESS_CODE_GENERATED"
    assert result["modified_code_string"] == "print('updated')"
