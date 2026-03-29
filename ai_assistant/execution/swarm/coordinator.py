import asyncio
import logging
from typing import Dict, Any, List, Optional
import uuid

from .protocol import SwarmContract, AgentRole
from .blackboard import Blackboard, BlackboardEvent
from .agents.coder import CoderAgent
from .agents.tester import TesterAgent
from .agents.reviewer import ReviewerAgent

logger = logging.getLogger(__name__)

class SubSwarmCoordinator:
    """
    Manages the lifecycle of a Multi-Agent Swarm for a specific SwarmContract.
    It spins up the Blackboard, the agents, and waits for the swarm to complete
    its deliverables or timeout.
    """
    def __init__(self, contract: SwarmContract, llm_provider: Any, timeout_seconds: int = 300):
        self.swarm_id = f"swarm_{uuid.uuid4().hex[:8]}"
        self.contract = contract
        self.llm_provider = llm_provider
        self.timeout = timeout_seconds

        # Core Infrastructure
        self.blackboard = Blackboard(self.swarm_id)

        # Sub-agents
        self.agents = [
            CoderAgent(f"Coder_{self.swarm_id}", self.contract, self.blackboard, self.llm_provider),
            TesterAgent(f"Tester_{self.swarm_id}", self.contract, self.blackboard, self.llm_provider),
            ReviewerAgent(f"Reviewer_{self.swarm_id}", self.contract, self.blackboard, self.llm_provider)
        ]

        # Future to signal when the swarm is done
        self.completion_future = asyncio.Future()

    def setup_coordinator_subscriptions(self):
        """The Coordinator listens for critical lifecycle events."""
        self.blackboard.subscribe("swarm_complete", self._handle_swarm_complete)
        self.blackboard.subscribe("agent_error", self._handle_agent_error)
        self.blackboard.subscribe("agent_progress", self._handle_progress_update)

    async def _handle_swarm_complete(self, event: BlackboardEvent):
        """When the Reviewer approves all files, the swarm is done."""
        logger.info(f"[Coordinator {self.swarm_id}] Swarm reported complete!")
        if not self.completion_future.done():
            self.completion_future.set_result(True)

    async def _handle_agent_error(self, event: BlackboardEvent):
        """If an agent crashes critically, abort the swarm."""
        error = event.data.get("error")
        role = event.data.get("role")
        logger.error(f"[Coordinator {self.swarm_id}] Critical error in {role}: {error}")
        if not self.completion_future.done():
            self.completion_future.set_exception(RuntimeError(f"Swarm aborted due to {role} error: {error}"))

    async def _handle_progress_update(self, event: BlackboardEvent):
        """Pass progress up to the UI/TaskManager if configured."""
        role = event.data.get("role")
        msg = event.data.get("message")
        logger.info(f"[Coordinator {self.swarm_id}] {role.upper()}: {msg}")
        # In a full integration, we would update `TaskManager` here.

    async def execute_swarm(self) -> Dict[str, Any]:
        """
        Starts the swarm and returns the finalized artifacts when done.
        """
        logger.info(f"[Coordinator {self.swarm_id}] Starting sub-swarm for contract: {self.contract.task_id}")
        # The contract is now a Pydantic model and validated at construction. No manual .validate() call needed.

        self.setup_coordinator_subscriptions()

        # 1. Start all agents concurrently as background tasks
        agent_tasks = [asyncio.create_task(agent.run()) for agent in self.agents]

        try:
            # 2. Wait for the completion signal (or timeout)
            await asyncio.wait_for(self.completion_future, timeout=self.timeout)
            logger.info(f"[Coordinator {self.swarm_id}] Swarm execution successful.")

        except asyncio.TimeoutError:
            logger.warning(f"[Coordinator {self.swarm_id}] Swarm timed out after {self.timeout}s.")
            return {"status": "error", "message": f"Swarm timed out after {self.timeout}s."}
        except Exception as e:
            logger.error(f"[Coordinator {self.swarm_id}] Swarm failed: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            # 3. Clean up agent background tasks
            for task in agent_tasks:
                if not task.done():
                    task.cancel()

        # 4. Extract final artifacts from the Blackboard
        final_artifacts = {}
        for filename in self.contract.deliverables:
            code = await self.blackboard.get_state(f"artifact_{filename}")
            if code:
                final_artifacts[filename] = code
            else:
                logger.warning(f"[Coordinator {self.swarm_id}] Missing final artifact for {filename}")

        return {
            "status": "success",
            "artifacts": final_artifacts,
            "logs": [e for e in self.blackboard.history]
        }
