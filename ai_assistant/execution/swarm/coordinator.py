import asyncio
import logging
from typing import Dict, Any, List, Optional
import uuid

from .protocol import SwarmContract, AgentRole, ExperimentScorecard, FailureClassification
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

        self.setup_coordinator_subscriptions()

        # 1. Start all agents concurrently as background tasks
        agent_tasks = [asyncio.create_task(agent.run()) for agent in self.agents]

        try:
            # 2. Wait for the completion signal (or timeout)
            await asyncio.wait_for(self.completion_future, timeout=self.timeout)
            logger.info(f"[Coordinator {self.swarm_id}] Swarm execution successful.")

        except asyncio.TimeoutError:
            logger.warning(f"[Coordinator {self.swarm_id}] Swarm timed out after {self.timeout}s.")
            scorecard = self._build_scorecard(accepted=False)
            return {
                "status": "error",
                "message": f"Swarm timed out after {self.timeout}s.",
                "scorecard": scorecard.model_dump(),
            }
        except Exception as e:
            logger.error(f"[Coordinator {self.swarm_id}] Swarm failed: {e}")
            scorecard = self._build_scorecard(accepted=False)
            return {"status": "error", "message": str(e), "scorecard": scorecard.model_dump()}
        finally:
            # 3. Clean up agent background tasks
            for task in agent_tasks:
                if not task.done():
                    task.cancel()

        # 4. Extract final artifacts from the Blackboard
        final_artifacts = await self._retrieve_final_artifacts()

        return {
            "status": "success",
            "artifacts": final_artifacts,
            "scorecard": self._build_scorecard(accepted=True).model_dump(),
            "logs": [e for e in self.blackboard.history]
        }

    def _build_scorecard(self, accepted: bool) -> ExperimentScorecard:
        """
        Build a deterministic scorecard from blackboard events and capability checks.
        """
        passed_events = [event for event in self.blackboard.history if event.topic == "test_results_passed"]
        failed_events = [event for event in self.blackboard.history if event.topic == "test_results_failed"]
        tests_run = len(passed_events) + len(failed_events)

        failure_reason = None
        if failed_events:
            raw_failure = failed_events[-1].data.get("failure_classification")
            if isinstance(raw_failure, FailureClassification):
                failure_reason = raw_failure
            elif isinstance(raw_failure, dict):
                failure_reason = FailureClassification(**raw_failure)

        capability_entries: List[Dict[str, Any]] = []
        for agent in self.agents:
            capability_entries.extend(agent.get_capability_audit_log())

        files_touched = sorted({
            str(event.data.get("filename"))
            for event in self.blackboard.history
            if event.data.get("filename")
        } | set(self.contract.deliverables))

        risk_level = self._estimate_risk_level(files_touched, bool(failed_events))

        return ExperimentScorecard(
            task_id=self.contract.task_id,
            tests_run=tests_run,
            tests_passed=len(passed_events),
            risk_level=risk_level,
            files_touched=files_touched,
            capabilities_used=capability_entries,
            failure_reason=failure_reason,
            accepted=accepted and not failed_events,
        )

    @staticmethod
    def _estimate_risk_level(files_touched: List[str], had_failures: bool) -> str:
        if had_failures:
            return "high"
        if any(path.endswith((".lock", ".toml")) or "requirements" in path for path in files_touched):
            return "medium"
        if len(files_touched) > 4:
            return "medium"
        return "low"

    async def _retrieve_final_artifacts(self) -> Dict[str, Any]:
        """
        Helper method to retrieve final artifacts from the blackboard.
        """
        from .protocol import CapabilityRegistry

        # Enforce memory access capabilities for the coordinator role natively via the registry
        CapabilityRegistry.require_role_capability(AgentRole.COORDINATOR, "can_access_memory")

        final_artifacts = {}
        for filename in self.contract.deliverables:
            code = await self.blackboard.get_state(f"artifact_{filename}")
            if code:
                final_artifacts[filename] = code
            else:
                logger.warning(f"[Coordinator {self.swarm_id}] Missing final artifact for {filename}")

        return final_artifacts
