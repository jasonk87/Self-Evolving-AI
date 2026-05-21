import logging
import asyncio
from typing import Dict, Any, Optional

from ..protocol import BaseSwarmAgent, AgentRole, SwarmContract
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

    def setup_subscriptions(self):
        # The Coder listens for test failures from the Tester to revise its code
        self.blackboard.subscribe("test_results_failed", self.handle_test_failure)

    async def run(self):
        self.status = "working"
        await self.report_progress("Starting code generation for contract deliverables.")

        # 1. Identify which deliverables are implementation files (not test files)
        impl_files = [f for f in self.contract.deliverables if not f.startswith("test_") and f.endswith(".py")]

        if not impl_files:
            await self.report_progress("No implementation files to generate in contract.")
            self.status = "completed"
            return

        # 2. Generate initial drafts for each file based on the interface definitions
        for file in impl_files:
            await self.generate_draft(file)

        self.status = "waiting_for_tests"
        await self.report_progress("Finished initial drafts. Waiting for tester feedback.")

        # 3. The Coder now idles. It will be woken up by 'test_results_failed' events
        # via the callback `handle_test_failure`. If the coordinator publishes 'swarm_complete',
        # the task will be cancelled by the coordinator.

    async def generate_draft(self, filename: str, previous_error: Optional[str] = None):
        """Generates code for a specific file and publishes it to the blackboard."""

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
            prompt += f"\\n\\nPREVIOUS TESTS FAILED WITH THE FOLLOWING ERROR:\\n{previous_error}\\nFix the implementation."

        prompt += "\\n\\nOutput ONLY the raw Python code for the file. Do not include markdown or explanations."

        try:
            # Assume we are using gemini or ollama via the provided llm_provider
            code = await self.llm_provider.invoke_ollama_model_async(prompt, temperature=0.2)

            # Clean markdown
            cleaned_code = code.replace("```python", "").replace("```", "").strip()
            self.drafts[filename] = cleaned_code

            # Update shared state and publish event
            await self.blackboard.update_state(f"artifact_{filename}", cleaned_code, self.name)
            await self.blackboard.publish(
                topic="code_drafted",
                source_agent=self.name,
                data={"filename": filename, "code": cleaned_code}
            )
            await self.report_progress(f"Draft completed for {filename}.")

        except Exception as e:
            await self.report_error(e, f"Generating draft for {filename}")

    async def handle_test_failure(self, event: BlackboardEvent):
        """Callback: Wakes up the coder to fix bugs found by the Tester."""
        filename = event.data.get("filename") # The implementation file that failed
        error_logs = event.data.get("logs")

        if filename in self.drafts:
            self.status = "revising"
            await self.report_progress(f"Received test failure for {filename}. Revising code.")
            await self.generate_draft(filename, previous_error=error_logs)
            self.status = "waiting_for_tests"
