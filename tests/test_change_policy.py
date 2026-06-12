from ai_assistant.core.change_policy import (
    ChangeAction,
    ChangeZone,
    GovernanceTier,
    classify_change_zone,
    decide_governance,
)


def test_generated_tools_are_autonomous():
    decision = decide_governance(
        "ai_assistant/custom_tools/generated/reverse_image_search.py",
        ChangeAction.CREATE,
    )

    assert decision.zone == ChangeZone.GENERATED_TOOL
    assert decision.tier == GovernanceTier.AUTONOMOUS
    assert decision.allowed_autonomous is True
    assert decision.requires_human_approval is False
    assert "scorecard" in decision.required_gates


def test_dynamic_specialists_are_autonomous_but_audited():
    decision = decide_governance(
        "ai_assistant/custom_tools/dynamic_specialist_visionworker.py",
        ChangeAction.CREATE,
    )

    assert decision.zone == ChangeZone.DYNAMIC_SPECIALIST
    assert decision.allowed_autonomous is True
    assert "worthiness" in decision.required_gates
    assert "judge" in decision.required_gates


def test_core_and_governance_source_require_human_approval():
    for path in (
        "ai_assistant/core/self_modification.py",
        "ai_assistant/core/safety/judge.py",
        "ai_assistant/execution/action_executor.py",
        "ai_assistant/code_services/service.py",
    ):
        decision = decide_governance(path, ChangeAction.MODIFY)

        assert decision.tier == GovernanceTier.HUMAN_REQUIRED
        assert decision.allowed_autonomous is False
        assert decision.requires_human_approval is True
        assert "human_approval" in decision.required_gates


def test_tool_source_can_self_repair_without_human_approval():
    decision = decide_governance(
        "ai_assistant/custom_tools/search_tools.py",
        ChangeAction.MODIFY,
    )

    assert decision.zone == ChangeZone.TOOL_SOURCE
    assert decision.tier == GovernanceTier.AUTONOMOUS
    assert decision.requires_human_approval is False


def test_durable_code_deletion_requires_human_but_quarantine_does_not():
    delete_decision = decide_governance(
        "ai_assistant/custom_tools/generated/bad_tool.py",
        ChangeAction.DELETE,
    )
    quarantine_decision = decide_governance(
        "ai_assistant/custom_tools/generated/bad_tool.py",
        ChangeAction.QUARANTINE,
    )

    assert delete_decision.tier == GovernanceTier.HUMAN_REQUIRED
    assert delete_decision.requires_human_approval is True
    assert quarantine_decision.tier == GovernanceTier.AUTONOMOUS
    assert quarantine_decision.allowed_autonomous is True


def test_runtime_state_and_generated_projects_remain_autonomous():
    assert (
        classify_change_zone("ai_assistant/core/data/pending_approvals.json")
        == ChangeZone.RUNTIME_STATE
    )
    assert (
        classify_change_zone("ai_assistant/ai_generated_projects/demo/src/main.py")
        == ChangeZone.GENERATED_PROJECT
    )

    cleanup = decide_governance(
        "ai_assistant/ai_generated_projects/demo",
        ChangeAction.DELETE,
    )

    assert cleanup.tier == GovernanceTier.AUTONOMOUS
    assert cleanup.requires_human_approval is False
