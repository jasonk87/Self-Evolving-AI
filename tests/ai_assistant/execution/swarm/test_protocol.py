import pytest
from transitions import MachineError

from typing import Any
from ai_assistant.execution.swarm.protocol import SwarmContract, AgentRole, BaseSwarmAgent, AgentState
from ai_assistant.execution.swarm.blackboard import Blackboard

class MockProvider:
    pass

class DummyAgent(BaseSwarmAgent):
    def __init__(self, name: str, role: AgentRole, contract: SwarmContract, blackboard: Blackboard, llm_provider: Any):
        super().__init__(name, role, contract, blackboard, llm_provider)

    def setup_subscriptions(self):
        pass

    async def run(self):
        pass

@pytest.fixture
def contract():
    return SwarmContract(
        task_id="test_task_1",
        description="A test description that is long enough.",
        deliverables=["main.py"]
    )

@pytest.fixture
def blackboard():
    return Blackboard("test_swarm")

@pytest.fixture
def agent(contract, blackboard):
    return DummyAgent("test_agent", AgentRole.CODER, contract, blackboard, MockProvider())

def test_initial_state(agent):
    assert agent.status == AgentState.INITIALIZED.value

def test_valid_lifecycle_transitions(agent):
    agent.start_working()
    assert agent.status == AgentState.WORKING.value

    agent.wait_for_tests()
    assert agent.status == AgentState.WAITING_FOR_TESTS.value

    agent.start_revising()
    assert agent.status == AgentState.REVISING.value

    agent.wait_for_tests()
    assert agent.status == AgentState.WAITING_FOR_TESTS.value

    agent.mark_completed()
    assert agent.status == AgentState.COMPLETED.value

def test_invalid_transition_throws_machine_error(agent):
    # Cannot go directly from initialized to completed (must start working)
    with pytest.raises(MachineError):
        agent.mark_completed()

    agent.start_working()

    # Cannot go from working directly to tests_passed (must wait/execute first)
    with pytest.raises(MachineError):
        agent.tests_passed()

def test_terminal_states_prevent_regular_transitions(agent):
    agent.start_working()
    agent.report_critical_error()
    assert agent.status == AgentState.ERROR.value

    with pytest.raises(MachineError):
        agent.start_working()

    with pytest.raises(MachineError):
        agent.wait_for_tests()

def test_quarantine_transition(agent):
    agent.start_working()
    agent.quarantine()
    assert agent.status == AgentState.QUARANTINED.value

    with pytest.raises(MachineError):
        agent.start_working()

def test_recover_from_error(agent):
    agent.start_working()
    agent.report_critical_error()
    assert agent.status == AgentState.ERROR.value

    agent.recover()
    assert agent.status == AgentState.WORKING.value

def test_reset_from_terminal_states(agent):
    agent.start_working()
    agent.mark_completed()
    assert agent.status == AgentState.COMPLETED.value

    agent.reset()
    assert agent.status == AgentState.INITIALIZED.value

    agent.start_working()
    agent.quarantine()
    assert agent.status == AgentState.QUARANTINED.value

    agent.reset()
    assert agent.status == AgentState.INITIALIZED.value

    agent.start_working()
    agent.report_critical_error()
    assert agent.status == AgentState.ERROR.value

    agent.reset()
    assert agent.status == AgentState.INITIALIZED.value

def test_capabilities_registry(contract, blackboard):
    # Test Coordinator capabilities
    coordinator = DummyAgent("coord", AgentRole.COORDINATOR, contract, blackboard, MockProvider())
    assert coordinator.has_capability("can_create_pr")
    assert not coordinator.has_capability("can_edit_files")

    # Test Coder capabilities
    coder = DummyAgent("coder", AgentRole.CODER, contract, blackboard, MockProvider())
    assert coder.has_capability("can_edit_files")
    assert coder.has_capability("can_execute_code")
    assert not coder.has_capability("can_approve_changes")

    # Test Tester capabilities
    tester = DummyAgent("tester", AgentRole.TESTER, contract, blackboard, MockProvider())
    assert tester.has_capability("can_run_tests")
    assert tester.has_capability("can_edit_files")
    assert not tester.has_capability("can_approve_changes")

    # Test Reviewer capabilities
    reviewer = DummyAgent("reviewer", AgentRole.REVIEWER, contract, blackboard, MockProvider())
    assert reviewer.has_capability("can_approve_changes")
    assert not reviewer.has_capability("can_edit_files")

def test_require_capability_enforcement(agent):
    # Agent is a CODER, so they can edit files
    agent.require_capability("can_edit_files") # Should pass silently

    # Agent is a CODER, they cannot approve changes
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_approve_changes'"):
        agent.require_capability("can_approve_changes")

@pytest.mark.asyncio
async def test_real_paths_enforcement(contract, blackboard):
    from ai_assistant.execution.swarm.agents.tester import TesterAgent
    from ai_assistant.execution.swarm.agents.coder import CoderAgent
    from ai_assistant.execution.swarm.agents.reviewer import ReviewerAgent

    # Tester checking capabilities
    tester = TesterAgent("tester", contract, blackboard, MockProvider())

    # Tester has run tests capability, this should not throw PermissionError
    # (We catch whatever other error might happen because we are unit testing without mock data)
    try:
        await tester._execute_tests("impl.py", "impl_code", "test.py", "test_code")
    except Exception as e:
        assert not isinstance(e, PermissionError)

    # Coder trying to execute tests
    coder = CoderAgent("coder", contract, blackboard, MockProvider())
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_run_tests'"):
        await TesterAgent._execute_tests(coder, "impl.py", "impl_code", "test.py", "test_code")

    # Reviewer trying to edit files
    reviewer = ReviewerAgent("reviewer", contract, blackboard, MockProvider())
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_edit_files'"):
        await TesterAgent.generate_test_draft(reviewer, "test_x.py")

    # Coder trying to review a file
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_approve_changes'"):
        await ReviewerAgent._review_file(coder, "x.py", "code")

    # Coordinator does not have `can_edit_files`
    from ai_assistant.execution.swarm.agents.coder import CoderAgent
    from ai_assistant.execution.swarm.protocol import AgentRole
    coordinator_agent = DummyAgent("coord", AgentRole.COORDINATOR, contract, blackboard, MockProvider())
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_edit_files'"):
        await CoderAgent.generate_draft(coordinator_agent, "impl.py")
