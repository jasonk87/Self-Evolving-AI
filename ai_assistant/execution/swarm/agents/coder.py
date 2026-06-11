import logging
import asyncio
from typing import Dict, Any, Optional
import pathlib

from ..protocol import (
    BaseSwarmAgent,
    AgentRole,
    FailureClass,
    SwarmContract,
    coerce_failure_classification,
)
from ..blackboard import Blackboard, BlackboardEvent

logger = logging.getLogger(__name__)

class CoderAgent(BaseSwarmAgent):
    """
    Focuses EXCLUSIVELY on generating the implementation code defined in the contract.
    It does NOT write tests. It does not review. It only codes.
    """
    def __init__(self, name: str, contract: SwarmContract, blackboard: Blackboard, llm_provider: Any):
        super().__init__(name, AgentRole.CODER, contract, blackboard, llm_provider)
        self.drafts = {} # filename -> code string
        self.flaky_retry_counts: Dict[str, int] = {}

    def setup_subscriptions(self):
        # The Coder listens for test failures from the Tester to revise its code
        self.blackboard.subscribe("test_results_failed", self.handle_test_failure)

    async def run(self):
        self.start_working()
        await self.report_progress("Starting code generation for contract deliverables.")

        # 1. Identify which deliverables are implementation files (not test files)
        impl_files = [f for f in self.contract.deliverables if not f.startswith("test_") and f.endswith(".py")]

        if not impl_files:
            await self.report_progress("No implementation files to generate in contract.")
            self.mark_completed()
            return

        # 2. Generate initial drafts for each file based on the interface definitions
        for file in impl_files:
            await self.generate_draft(file)

        self.wait_for_tests()
        await self.report_progress("Finished initial drafts. Waiting for tester feedback.")

        # 3. The Coder now idles. It will be woken up by 'test_results_failed' events
        # via the callback `handle_test_failure`. If the coordinator publishes 'swarm_complete',
        # the task will be cancelled by the coordinator.

    @staticmethod
    def _is_dependency_file(filename: str) -> bool:
        """Helper to determine if a file is a recognized dependency or configuration file."""
        dependency_files = {
            "requirements.txt", "requirements-core.txt", "requirements-dev.txt",
            "requirements.lock", "pyproject.toml", "setup.py", "setup.cfg",
            "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock", "pdm.lock",
            "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"
        }
        name = pathlib.Path(filename).name
        return name in dependency_files

    async def generate_draft(
        self,
        filename: str,
        previous_error: Optional[str] = None,
        repair_guidance: Optional[str] = None,
    ):
        """Generates code for a specific file and publishes it to the blackboard."""
        self.require_capability("can_edit_files")

        if CoderAgent._is_dependency_file(filename):
            self.require_capability("can_modify_dependencies")

        self.require_capability("can_access_memory")

        # Extract interfaces relevant to this file (simplification: we pass all interfaces for now)
        interfaces_str = "\\n".join([str(i) for i in self.contract.interfaces])

        prompt = f"""
        You are the 'Coder' in a Multi-Agent Swarm. Your ONLY job is to write the implementation for '{filename}'.
        Do NOT write unit tests.

        Task Description: {self.contract.description}

        Required Interfaces:
        {interfaces_str}

        Constraints:
        {self.contract.constraints}
        """

        if previous_error:
            if repair_guidance:
                prompt += (
                    f"\\n\\nCLASSIFICATION-DRIVEN REPAIR ROUTE:\\n{repair_guidance}"
                    f"\\n\\nFAILURE DETAILS:\\n{previous_error}"
                )
            else:
                prompt += f"\\n\\nPREVIOUS TESTS FAILED WITH THE FOLLOWING ERROR:\\n{previous_error}\\nFix the implementation."

        prompt += "\\n\\nOutput ONLY the raw Python code for the file. Do not include markdown or explanations."

        try:
            # Assume we are using gemini or ollama via the provided llm_provider
            code = await self.llm_provider.invoke_ollama_model_async(prompt, temperature=0.2)

            # Clean markdown
            cleaned_code = code.replace("```python", "").replace("```", "").strip()
            self.drafts[filename] = cleaned_code

            # Update shared state and publish event
            self.require_capability("can_access_memory")
            await self.blackboard.update_state(f"artifact_{filename}", cleaned_code, self.name)
            await self.blackboard.publish(
                topic="code_drafted",
                source_agent=self.name,
                data={"filename": filename, "code": cleaned_code}
            )
            await self.report_progress(f"Draft completed for {filename}.")

        except PermissionError:
            raise
        except Exception as e:
            await self.report_error(e, f"Generating draft for {filename}")

    async def handle_test_failure(self, event: BlackboardEvent):
        """Callback: route tester failures by classification before revising code."""
        filename = event.data.get("filename") # The implementation file that failed
        error_logs = event.data.get("logs")
        classification = coerce_failure_classification(
            event.data.get("failure_classification"),
            str(error_logs or ""),
        )

        route = classification.suggested_route
        blocking_classes = {
            FailureClass.STATE_MACHINE_VIOLATION,
            FailureClass.CAPABILITY_VIOLATION,
            FailureClass.ENVIRONMENT_CI_ISSUE,
            FailureClass.UNKNOWN,
        }
        if classification.failure_class in blocking_classes:
            await self.report_progress(
                f"Failure classified as {classification.failure_class.value}; blocking coder rewrite.",
                {"suggested_route": route},
            )
            return

        if classification.failure_class == FailureClass.FLAKY_LLM_ISSUE:
            retry_count = self.flaky_retry_counts.get(str(filename), 0)
            if retry_count >= 1:
                await self.report_progress(
                    f"Repeated flaky LLM failure for {filename}; blocking further retries.",
                    {"suggested_route": route},
                )
                return
            self.flaky_retry_counts[str(filename)] = retry_count + 1

        target_filename = self._select_repair_target(filename, classification.failure_class)
        if not target_filename:
            return

        if target_filename not in self.drafts and not CoderAgent._is_dependency_file(target_filename):
            return

        self.start_revising()
        guidance = self._build_repair_guidance(classification.failure_class)
        await self.report_progress(
            f"Routing {classification.failure_class.value} for {target_filename}.",
            {"suggested_route": route},
        )
        await self.generate_draft(
            target_filename,
            previous_error=error_logs,
            repair_guidance=guidance,
        )
        self.wait_for_tests()

    def _select_repair_target(self, filename: Optional[str], failure_class: FailureClass) -> Optional[str]:
        if failure_class == FailureClass.DEPENDENCY_MISSING:
            for deliverable in self.contract.deliverables:
                if CoderAgent._is_dependency_file(deliverable):
                    return deliverable
        return str(filename) if filename else None

    @staticmethod
    def _build_repair_guidance(failure_class: FailureClass) -> str:
        guidance = {
            FailureClass.DEPENDENCY_MISSING: (
                "Dependency repair route: identify the missing external package and update the dependency "
                "manifest or dependency-facing code. Do not make an unrelated implementation retry."
            ),
            FailureClass.IMPORT_PATH_ISSUE: (
                "Import/path repair route: fix module names, package boundaries, or relative imports while "
                "preserving behavior."
            ),
            FailureClass.TEST_ASSERTION_MISMATCH: (
                "Test-or-behavior review route: compare the asserted contract with the implementation and "
                "adjust only the side that contradicts the task contract."
            ),
            FailureClass.REAL_LOGIC_BUG: (
                "Logic bug repair route: fix the implementation defect indicated by the failing tests."
            ),
            FailureClass.FLAKY_LLM_ISSUE: (
                "Flaky LLM retry route: produce a complete, deterministic raw code response with no markdown, "
                "no placeholders, and no omitted imports."
            ),
        }
        return guidance.get(failure_class, "Route to human review; do not perform a blind rewrite.")
