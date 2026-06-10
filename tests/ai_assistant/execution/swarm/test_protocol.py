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
