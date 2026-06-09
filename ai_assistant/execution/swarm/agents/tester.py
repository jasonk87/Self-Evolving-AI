import logging
import asyncio
from typing import Dict, Any, Optional

from ..protocol import BaseSwarmAgent, AgentRole, SwarmContract
from ..blackboard import Blackboard, BlackboardEvent

logger = logging.getLogger(__name__)

class TesterAgent(BaseSwarmAgent):
    """
    Focuses EXCLUSIVELY on generating test suites (e.g. pytest) based on the contract interfaces.
    It writes tests *concurrently* to the Coder drafting the implementation.
    """
    def __init__(self, name: str, contract: SwarmContract, blackboard: Blackboard, llm_provider: Any):
        super().__init__(name, AgentRole.TESTER, contract, blackboard, llm_provider)
        self.drafts = {} # filename -> code string
        self.test_files = [f for f in self.contract.deliverables if f.startswith("test_")]
        self.impl_files = [f for f in self.contract.deliverables if not f.startswith("test_") and f.endswith(".py")]

    def setup_subscriptions(self):
        # The Tester listens for when the Coder publishes a draft implementation
        self.blackboard.subscribe("code_drafted", self.handle_code_drafted)

    async def run(self):
        self.start_working()
        await self.report_progress("Drafting unit tests based on interfaces concurrently.")

        # 1. Draft the unit tests based solely on the interface definition
        if not self.test_files:
            await self.report_progress("No test files to generate in contract.")
            self.mark_completed()
            return

        for file in self.test_files:
            await self.generate_test_draft(file)

        self.wait_for_implementation()
        await self.report_progress("Test drafts completed. Waiting for code implementation to execute them.")

        # 2. Wait for the 'code_drafted' event which will trigger execution.

    async def generate_test_draft(self, filename: str):
        """Generates unit tests based solely on the contract interface."""
        interfaces_str = "\\n".join([str(i) for i in self.contract.interfaces])

        prompt = f"""
        You are the 'Tester' in a Multi-Agent Swarm. Your ONLY job is to write the 'pytest' suite for '{filename}'.
        You must write rigorous edge-case tests against the following defined interfaces.
        Do NOT write the implementation code. Assume the implementation will exist in the corresponding module.

        Task Description: {self.contract.description}

        Required Interfaces to Test:
        {interfaces_str}

        Constraints:
        {self.contract.constraints}

        Output ONLY the raw Python test code for the file. Do not include markdown or explanations.
        """
        try:
            code = await self.llm_provider.invoke_ollama_model_async(prompt, temperature=0.2)
            cleaned_code = code.replace("```python", "").replace("```", "").strip()
            self.drafts[filename] = cleaned_code

            await self.blackboard.update_state(f"artifact_{filename}", cleaned_code, self.name)
            await self.report_progress(f"Test draft completed for {filename}.")

            # Note: We do NOT publish a general event for test draft completion unless
            # another agent (like Reviewer) needs to see the un-executed test code.

        except Exception as e:
            await self.report_error(e, f"Generating test draft for {filename}")

    async def handle_code_drafted(self, event: BlackboardEvent):
        """
        Callback: Triggered when the Coder finishes a file.
        The Tester attempts to run its tests against the Coder's draft.
        """
        impl_filename = event.data.get("filename")
        impl_code = event.data.get("code")

        # 1. Map implementation file to its corresponding test file
        test_filename = f"test_{impl_filename}"

        # Fast path: If the contract doesn't require testing for this file, skip execution
        # and signal immediate success to unblock the Reviewer.
        if test_filename not in self.test_files:
            await self.report_progress(f"No tests required for '{impl_filename}'. Bypassing test execution.")
            await self.blackboard.publish(
                topic="test_results_passed",
                source_agent=self.name,
                data={"filename": impl_filename, "test_file": None, "logs": "No tests required."}
            )
            return

        # Handle Race Condition: The Coder finished before the Tester finished drafting tests.
        # We wait up to 30 seconds for the draft to appear.
        if test_filename not in self.drafts:
            await self.report_progress(f"Coder finished {impl_filename} early. Waiting for test draft {test_filename} to complete.")
            for _ in range(60): # 60 * 0.5 = 30s timeout
                if test_filename in self.drafts:
                    break
                await asyncio.sleep(0.5)

            if test_filename not in self.drafts:
                await self.report_error(RuntimeError(f"Tester timed out waiting to draft {test_filename}."), "Code drafted callback")
                return

        self.execute_tests()
        await self.report_progress(f"Executing '{test_filename}' against new '{impl_filename}' draft.")

        # 2. Extract our drafted tests for this file
        test_code = self.drafts[test_filename]

        # 3. Execution Phase
        passed, logs = await self._execute_tests(impl_filename, impl_code, test_filename, test_code)

        if passed:
            self.tests_passed()
            await self.report_progress(f"Tests passed for {impl_filename}!")
            await self.blackboard.publish(
                topic="test_results_passed",
                source_agent=self.name,
                data={"filename": impl_filename, "test_file": test_filename, "logs": logs}
            )
        else:
            self.tests_failed()
            await self.report_progress(f"Tests FAILED for {impl_filename}. Sending feedback to Coder.")
            await self.blackboard.publish(
                topic="test_results_failed",
                source_agent=self.name,
                data={"filename": impl_filename, "test_file": test_filename, "logs": logs}
            )

    async def _execute_tests(self, impl_file: str, impl_code: str, test_file: str, test_code: str):
        """
        Writes the implementation and test files to a temporary directory and executes pytest.
        Returns a tuple of (passed_boolean, logs_string).
        """
        import os
        import tempfile
        import asyncio
        import traceback

        logger.info(f"[Tester] Executing pytest for {test_file} against {impl_file}")

        # We need the current python executable to run pytest
        import sys

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                # Construct absolute paths
                impl_path = os.path.join(temp_dir, impl_file)
                test_path = os.path.join(temp_dir, test_file)

                # Write files
                with open(impl_path, 'w', encoding='utf-8') as f:
                    f.write(impl_code)

                with open(test_path, 'w', encoding='utf-8') as f:
                    f.write(test_code)

                # Run pytest inside the temp directory
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-m', 'pytest', test_file, '-v',
                    cwd=temp_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                stdout, stderr = await process.communicate()

                stdout_str = stdout.decode('utf-8', errors='replace')
                stderr_str = stderr.decode('utf-8', errors='replace')

                logs = f"STDOUT:\n{stdout_str}\n\nSTDERR:\n{stderr_str}"

                passed = process.returncode == 0
                return passed, logs

        except Exception as e:
            error_log = f"Failed to execute tests due to exception:\n{traceback.format_exc()}"
            logger.error(f"[Tester] {error_log}")
            return False, error_log
