import abc
from enum import Enum
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, model_validator

from .blackboard import Blackboard, BlackboardEvent

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
