import abc
from enum import Enum
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, model_validator
from transitions import Machine

from .blackboard import Blackboard, BlackboardEvent

class AgentState(Enum):
    INITIALIZED = "initialized"
    WORKING = "working"
    WAITING_FOR_TESTS = "waiting_for_tests"
    WAITING_FOR_IMPLEMENTATION = "waiting_for_implementation"
    REVISING = "revising"
    REVIEWING = "reviewing"
    EXECUTING_TESTS = "executing_tests"
    TESTS_PASSED = "tests_passed"
    TESTS_FAILED = "tests_failed"
    COMPLETED = "completed"
    ERROR = "error"

class AgentRole(Enum):
    COORDINATOR = "coordinator"
    CODER = "coder"
    TESTER = "tester"
    REVIEWER = "reviewer"


class SwarmContract(BaseModel):
    """
    Defines the exact requirements and deliverables for a sub-swarm task.
    Created by the Orchestrator/ActionExecutor and validated strictly.
    """
    task_id: str = Field(..., description="Unique identifier for the sub-swarm task.")
    description: str = Field(..., min_length=5, description="Detailed description of the work to be done.")
    interfaces: List[Dict[str, Any]] = Field(default_factory=list, description="Expected public interfaces or functions.")
    deliverables: List[str] = Field(..., min_length=1, description="List of file paths the swarm is expected to create or modify.")
    constraints: Dict[str, str] = Field(default_factory=dict, description="Key-value pairs of technical constraints.")

    @model_validator(mode='after')
    def validate_contract_logic(self):
        """Ensure the contract is logically sound."""
        if not self.deliverables:
            raise ValueError("SwarmContract must specify at least one deliverable file.")
        return self


class BaseSwarmAgent(abc.ABC):
    """
    Abstract base class for all specialized sub-swarm agents.
    Every agent shares the same blackboard and receives the same contract.
    """
    def __init__(self, name: str, role: AgentRole, contract: SwarmContract, blackboard: Blackboard, llm_provider: Any):
        self.name = name
        self.role = role
        self.contract = contract
        self.blackboard = blackboard
        self.llm_provider = llm_provider

        # State machine setup
        self.states = [state.value for state in AgentState]
        self.machine = Machine(model=self, states=self.states, initial=AgentState.INITIALIZED.value, model_attribute="status")

        # Add basic transitions that most agents will need
        self.machine.add_transition(trigger='start_working', source='*', dest=AgentState.WORKING.value)
        self.machine.add_transition(trigger='mark_completed', source='*', dest=AgentState.COMPLETED.value)
        self.machine.add_transition(trigger='report_critical_error', source='*', dest=AgentState.ERROR.value)

        # Specific transitions
        self.machine.add_transition(trigger='wait_for_tests', source='*', dest=AgentState.WAITING_FOR_TESTS.value)
        self.machine.add_transition(trigger='wait_for_implementation', source='*', dest=AgentState.WAITING_FOR_IMPLEMENTATION.value)
        self.machine.add_transition(trigger='start_revising', source='*', dest=AgentState.REVISING.value)
        self.machine.add_transition(trigger='start_reviewing', source='*', dest=AgentState.REVIEWING.value)
        self.machine.add_transition(trigger='execute_tests', source='*', dest=AgentState.EXECUTING_TESTS.value)
        self.machine.add_transition(trigger='tests_passed', source='*', dest=AgentState.TESTS_PASSED.value)
        self.machine.add_transition(trigger='tests_failed', source='*', dest=AgentState.TESTS_FAILED.value)

        # Initialize event subscriptions specific to the agent's role
        self.setup_subscriptions()

    @abc.abstractmethod
    def setup_subscriptions(self):
        """Register callbacks on the blackboard for relevant events."""
        pass

    @abc.abstractmethod
    async def run(self):
        """
        The main execution loop for the agent.
        Runs continuously in the background until the swarm is completed or cancelled.
        """
        pass

    async def report_progress(self, message: str, data: Optional[Dict[str, Any]] = None):
        """Utility to post progress updates to the coordinator."""
        await self.blackboard.publish(
            topic="agent_progress",
            source_agent=self.name,
            data={"role": self.role.value, "message": message, "details": data or {}}
        )

    async def report_error(self, error: Exception, context: str):
        """Utility to report critical failures."""
        self.report_critical_error()
        await self.blackboard.publish(
            topic="agent_error",
            source_agent=self.name,
            data={"role": self.role.value, "error": str(error), "context": context}
        )
