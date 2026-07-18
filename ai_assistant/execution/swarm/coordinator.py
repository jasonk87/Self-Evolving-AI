import asyncio
import logging
from typing import Dict, Any, List, Optional
import uuid

from ai_assistant.core.experiment_scoreboard import record_experiment_scorecard
from .protocol import (
    SwarmContract,
    AgentRole,
    ExperimentScorecard,
    FailureClass,
    FailureClassification,
    coerce_failure_classification,
)
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
        self.completion_future: Optional[asyncio.Future] = None
        self.blocked_classification: Optional[FailureClassification] = None
        self.flaky_failure_count = 0

    def _ensure_completion_future(self) -> asyncio.Future:
        """Create the completion signal on the active event loop when first needed."""
        if self.completion_future is None:
            self.completion_future = asyncio.get_running_loop().create_future()
        return self.completion_future

    def _record_scorecard(self, scorecard: ExperimentScorecard, outcome: str) -> Dict[str, Any]:
        return record_experiment_scorecard(
            scorecard,
            actor="sub_swarm_coordinator",
            experiment_type="sub_swarm",
            source=self.swarm_id,
            metadata={
                "outcome": outcome,
                "contract_task_id": self.contract.task_id,
                "deliverables": self.contract.deliverables,
            },
        )

    def setup_coordinator_subscriptions(self):
        """The Coordinator listens for critical lifecycle events."""
        self.blackboard.subscribe("swarm_complete", self._handle_swarm_complete)
        self.blackboard.subscribe("agent_error", self._handle_agent_error)
        self.blackboard.subscribe("agent_progress", self._handle_progress_update)
        self.blackboard.subscribe("test_results_failed", self._handle_test_failure)

    async def _handle_swarm_complete(self, event: BlackboardEvent):
        """When the Reviewer approves all files, the swarm is done."""
        logger.info(f"[Coordinator {self.swarm_id}] Swarm reported complete!")
        completion_future = self._ensure_completion_future()
        if not completion_future.done():
            completion_future.set_result(True)

    async def _handle_agent_error(self, event: BlackboardEvent):
        """If an agent crashes critically, abort the swarm."""
        error = event.data.get("error")
        role = event.data.get("role")
        logger.error(f"[Coordinator {self.swarm_id}] Critical error in {role}: {error}")
        completion_future = self._ensure_completion_future()
        if not completion_future.done():
            completion_future.set_exception(RuntimeError(f"Swarm aborted due to {role} error: {error}"))

    async def _handle_progress_update(self, event: BlackboardEvent):
        """Pass progress up to the UI/TaskManager if configured."""
        role = event.data.get("role")
        msg = event.data.get("message")
        logger.info(f"[Coordinator {self.swarm_id}] {role.upper()}: {msg}")
        # In a full integration, we would update `TaskManager` here.

    async def _handle_test_failure(self, event: BlackboardEvent):
        """Block the swarm for failure classes that should not trigger more rewrites."""
        classification = coerce_failure_classification(
            event.data.get("failure_classification"),
            str(event.data.get("logs") or ""),
        )
        blocking_classes = {
            FailureClass.STATE_MACHINE_VIOLATION,
            FailureClass.CAPABILITY_VIOLATION,
            FailureClass.ENVIRONMENT_CI_ISSUE,
            FailureClass.UNKNOWN,
        }

        should_block = classification.failure_class in blocking_classes
        if classification.failure_class == FailureClass.FLAKY_LLM_ISSUE:
            self.flaky_failure_count += 1
            should_block = self.flaky_failure_count > 1

        completion_future = self._ensure_completion_future()
        if should_block and not completion_future.done():
            self.blocked_classification = classification
            completion_future.set_result(False)

    async def execute_swarm(self) -> Dict[str, Any]:
        """
        Starts the swarm and returns the finalized artifacts when done.
        """
        logger.info(f"[Coordinator {self.swarm_id}] Starting sub-swarm for contract: {self.contract.task_id}")

        self.setup_coordinator_subscriptions()
        self.completion_future = asyncio.get_running_loop().create_future()

        # 1. Start all agents concurrently as background tasks
        agent_tasks = [asyncio.create_task(agent.run()) for agent in self.agents]

        try:
            # 2. Wait for the completion signal (or timeout)
            completed = await asyncio.wait_for(self.completion_future, timeout=self.timeout)
            if completed is False:
                logger.warning(f"[Coordinator {self.swarm_id}] Swarm blocked by failure classification.")
                scorecard = self._build_scorecard(accepted=False, blocked=True)
                self._record_scorecard(scorecard, "blocked")
                return {
                    "status": "error",
                    "message": f"Swarm blocked: {scorecard.suggested_route}",
                    "scorecard": scorecard.model_dump(),
                }
            logger.info(f"[Coordinator {self.swarm_id}] Swarm execution successful.")

        except asyncio.TimeoutError:
            logger.warning(f"[Coordinator {self.swarm_id}] Swarm timed out after {self.timeout}s.")
            scorecard = self._build_scorecard(accepted=False, blocked=True)
            self._record_scorecard(scorecard, "timeout")
            return {
                "status": "error",
                "message": f"Swarm timed out after {self.timeout}s.",
                "scorecard": scorecard.model_dump(),
            }
        except Exception as e:
            logger.error(f"[Coordinator {self.swarm_id}] Swarm failed: {e}")
            scorecard = self._build_scorecard(accepted=False, blocked=True)
            self._record_scorecard(scorecard, "error")
            return {"status": "error", "message": str(e), "scorecard": scorecard.model_dump()}
        finally:
            # 3. Clean up agent background tasks
            for task in agent_tasks:
                if not task.done():
                    task.cancel()

        # 4. Extract final artifacts from the Blackboard
        final_artifacts = await self._retrieve_final_artifacts()
        scorecard = self._build_scorecard(accepted=True, blocked=False)
        self._record_scorecard(scorecard, "success")

        return {
            "status": "success",
            "artifacts": final_artifacts,
            "scorecard": scorecard.model_dump(),
            "logs": [e for e in self.blackboard.history]
        }

    def _build_scorecard(self, accepted: bool, blocked: bool = False) -> ExperimentScorecard:
        """
        Build a deterministic scorecard from blackboard events and capability checks.
        """
        passed_events = [event for event in self.blackboard.history if event.topic == "test_results_passed"]
        failed_events = [event for event in self.blackboard.history if event.topic == "test_results_failed"]
        tests_run = len(passed_events) + len(failed_events)

        failure_reason = None
        if self.blocked_classification is not None:
            failure_reason = self.blocked_classification
        elif failed_events:
            raw_failure = failed_events[-1].data.get("failure_classification")
            failure_reason = coerce_failure_classification(raw_failure, str(failed_events[-1].data.get("logs") or ""))

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
            blocked=blocked,
            suggested_route=failure_reason.suggested_route if failure_reason else None,
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
