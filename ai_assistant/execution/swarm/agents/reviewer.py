import logging
from typing import Any

from ..protocol import BaseSwarmAgent, AgentRole, SwarmContract
from ..blackboard import Blackboard, BlackboardEvent

logger = logging.getLogger(__name__)

class ReviewerAgent(BaseSwarmAgent):
    """
    Focuses on static analysis, docstrings, and style compliance.
    It acts as the final gatekeeper before the SubSwarmCoordinator considers the task 'done'.
    """
    def __init__(self, name: str, contract: SwarmContract, blackboard: Blackboard, llm_provider: Any):
        super().__init__(name, AgentRole.REVIEWER, contract, blackboard, llm_provider)
        self.approved_files = set()
        self.required_files = set(contract.deliverables)

    def setup_subscriptions(self):
        # The Reviewer listens for successful test runs to begin its final polish phase
        self.blackboard.subscribe("test_results_passed", self.handle_tests_passed)

    async def run(self):
        self.wait_for_tests()
        await self.report_progress("Waiting for tests to pass before reviewing code.")

        # The Reviewer idles until 'test_results_passed' wakes it up.

    async def handle_tests_passed(self, event: BlackboardEvent):
        """Callback: When tests pass for a file, review the implementation and test files."""
        impl_filename = event.data.get("filename")
        test_filename = event.data.get("test_file")

        self.start_reviewing()
        await self.report_progress(f"Reviewing {impl_filename} and {test_filename} for style and docs.")

        # Retrieve the latest code from the shared state
        self.require_capability("can_access_memory")
        self.require_capability("can_read_files")
        impl_code = await self.blackboard.get_state(f"artifact_{impl_filename}")
        test_code = await self.blackboard.get_state(f"artifact_{test_filename}")

        # 1. Review Implementation
        if impl_filename and impl_code:
            await self._review_file(impl_filename, impl_code)

        # 2. Review Tests (Optional, for rigor)
        if test_filename and test_code:
            await self._review_file(test_filename, test_code)

        self.wait_for_tests()

    async def _review_file(self, filename: str, code: str):
        """Uses LLM (or static analysis tools like `ruff`) to clean up the code."""
        self.require_capability("can_approve_changes")
        prompt = f"""
        You are the 'Reviewer' in a Multi-Agent Swarm. The code for '{filename}' has passed all unit tests.
        Your ONLY job is to add missing docstrings, enforce PEP8 style (like a linter), and add type hints if missing.
        Do NOT change the underlying logic or behavior of the code, as that would invalidate the passing tests.

        Code to Review:
        ```python
        {code}
        ```

        Output ONLY the polished Python code. Do not include markdown or explanations.
        """
        try:
            polished_code = await self.llm_provider.invoke_ollama_model_async(
                prompt,
                temperature=0.1 # Low temp for style/docs
            )
            cleaned_code = polished_code.replace("```python", "").replace("```", "").strip()

            # Update the finalized artifact in the state
            self.require_capability("can_finalize_artifacts")
            self.require_capability("can_access_memory")
            await self.blackboard.update_state(f"artifact_{filename}", cleaned_code, self.name)
            self.approved_files.add(filename)

            await self.blackboard.publish(
                topic="file_approved",
                source_agent=self.name,
                data={"filename": filename}
            )
            await self.report_progress(f"Review completed and approved for {filename}.")

            # Check if all required deliverables have been approved
            if self.approved_files.issuperset(self.required_files):
                await self.report_progress("All deliverables reviewed and approved! Swarm contract fulfilled.")
                await self.blackboard.publish(
                    topic="swarm_complete",
                    source_agent=self.name,
                    data={"message": "All files approved by Reviewer."}
                )

        except PermissionError:
            raise
        except Exception as e:
            await self.report_error(e, f"Reviewing {filename}")
