import abc
from enum import Enum
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from .blackboard import Blackboard, BlackboardEvent

class AgentRole(Enum):
    COORDINATOR = "coordinator"
    CODER = "coder"
    TESTER = "tester"
    REVIEWER = "reviewer"


@dataclass
class SwarmContract:
    """
    Defines the exact requirements and deliverables for a sub-swarm task.
    Created by the Orchestrator/ActionExecutor.
    """
    task_id: str
    description: str
    interfaces: List[Dict[str, Any]] = field(default_factory=list) # [{'name': 'AuthService', 'methods': ['login(user)']}]
    deliverables: List[str] = field(default_factory=list) # ['auth_service.py', 'test_auth_service.py']
    constraints: Dict[str, str] = field(default_factory=dict)

    def validate(self):
        """Ensure the contract is well-formed before starting."""
        if not self.task_id or not self.description:
            raise ValueError("SwarmContract requires task_id and description.")
        if not self.deliverables:
            raise ValueError("SwarmContract must specify at least one deliverable file.")


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
        self.status = "initialized"

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
        self.status = "error"
        await self.blackboard.publish(
            topic="agent_error",
            source_agent=self.name,
            data={"role": self.role.value, "error": str(error), "context": context}
        )
