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

def test_require_role_capability_enforcement():
    from ai_assistant.execution.swarm.protocol import CapabilityRegistry

    # Coordinator has can_access_memory
    CapabilityRegistry.require_role_capability(AgentRole.COORDINATOR, "can_access_memory")

    # Reviewer does not have can_edit_files
    with pytest.raises(PermissionError, match=r"Role 'reviewer' lacks required capability: 'can_edit_files'"):
        CapabilityRegistry.require_role_capability(AgentRole.REVIEWER, "can_edit_files")

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

    # Fake Coder trying to execute tests (Coder has no can_run_tests)
    class FakeCoderAsTester(TesterAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.role = AgentRole.CODER

    fake_coder_tester = FakeCoderAsTester("fake", contract, blackboard, MockProvider())
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_run_tests'"):
        await fake_coder_tester._execute_tests("impl.py", "impl_code", "test.py", "test_code")

    # Fake Reviewer trying to edit files
    class FakeReviewerAsTester(TesterAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.role = AgentRole.REVIEWER

    fake_reviewer_tester = FakeReviewerAsTester("fake", contract, blackboard, MockProvider())
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_edit_files'"):
        await fake_reviewer_tester.generate_test_draft("test_x.py")

    # Fake Coder trying to review a file
    class FakeCoderAsReviewer(ReviewerAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.role = AgentRole.CODER

    fake_coder_reviewer = FakeCoderAsReviewer("fake", contract, blackboard, MockProvider())
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_approve_changes'"):
        await fake_coder_reviewer._review_file("x.py", "code")

    # Fake Coordinator trying to draft code
    class FakeCoordinatorAsCoder(CoderAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.role = AgentRole.COORDINATOR

    fake_coordinator_coder = FakeCoordinatorAsCoder("fake", contract, blackboard, MockProvider())
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_edit_files'"):
        await fake_coordinator_coder.generate_draft("impl.py")

@pytest.mark.asyncio
async def test_dependency_modification_capabilities(contract, blackboard):
    from ai_assistant.execution.swarm.agents.coder import CoderAgent
    from ai_assistant.execution.swarm.agents.tester import TesterAgent

    class MockLLMProvider:
        async def invoke_ollama_model_async(self, *args, **kwargs):
            return "dummy_code"

    # Provide a mock llm provider so generate_draft does not crash on None
    coder = CoderAgent("coder", contract, blackboard, MockLLMProvider())

    # Verify the internal static method matches all expanded target dependencies and nested paths
    dependency_files = [
        "requirements.txt", "requirements-core.txt", "requirements-dev.txt",
        "requirements.lock", "pyproject.toml", "setup.py", "setup.cfg",
        "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock", "pdm.lock",
        "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "backend/requirements.txt", "frontend/package.json"
    ]
    for dep_file in dependency_files:
        assert CoderAgent._is_dependency_file(dep_file)

    assert not CoderAgent._is_dependency_file("main.py")
    assert not CoderAgent._is_dependency_file("index.js")
    assert not CoderAgent._is_dependency_file("backend/main.py")

    # Coder can modify dependencies, so drafting 'package.json' should not raise PermissionError.
    try:
        await coder.generate_draft("package.json")
    except Exception as e:
        assert not isinstance(e, PermissionError)

    # Drafting a normal python file 'main.py' should also not raise PermissionError
    try:
        await coder.generate_draft("main.py")
    except Exception as e:
        assert not isinstance(e, PermissionError)

    # A Tester, however, should not be able to generate a draft of a dependency file
    # if it doesn't have the can_modify_dependencies capability.
    # Tester doesn't have 'can_modify_dependencies'. We use Coder's logic via a fake agent
    class FakeTesterAsCoder(CoderAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.role = AgentRole.TESTER

    fake_tester_coder = FakeTesterAsCoder("fake", contract, blackboard, MockLLMProvider())

    # Tester DOES have can_edit_files. If it tries to run generate_draft on "package.json",
    # it will pass can_edit_files, but fail on can_modify_dependencies.
    with pytest.raises(PermissionError, match=r"lacks required capability: 'can_modify_dependencies'"):
        await fake_tester_coder.generate_draft("package.json")

    # If the Tester tries to run generate_draft on "main.py",
    # it passes can_edit_files, skips can_modify_dependencies, and might fail somewhere else or pass.
    # The key point is it doesn't fail on a PermissionError for capabilities it has.
    try:
        await fake_tester_coder.generate_draft("main.py")
    except Exception as e:
        assert not isinstance(e, PermissionError)
