import unittest
from unittest.mock import AsyncMock, patch
import asyncio
import sys
import os
import json

try:
    from ai_assistant.core.reviewer import ReviewerAgent, REVIEW_CODE_PROMPT_TEMPLATE
except ImportError: # pragma: no cover
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.reviewer import ReviewerAgent, REVIEW_CODE_PROMPT_TEMPLATE

class TestReviewerAgent(unittest.TestCase):
    def setUp(self):
        self.mock_llm_model_name = "mock_reviewer_model"
        # Patch get_model_for_task in the context of the reviewer module
        self.get_model_patcher = patch('ai_assistant.core.reviewer.get_model_for_task')
        self.mock_get_model_for_task = self.get_model_patcher.start()
        self.mock_get_model_for_task.return_value = self.mock_llm_model_name

        self.reviewer = ReviewerAgent() # Now uses mocked get_model_for_task

    def tearDown(self):
        self.get_model_patcher.stop()

    @patch('ai_assistant.core.reviewer.invoke_ollama_model_async', new_callable=AsyncMock)
    async def test_review_code_with_diff(self, mock_invoke_llm):
        mock_review_json = {"status": "approved", "comments": "LGTM with diff", "suggestions": ""}
        mock_invoke_llm.return_value = json.dumps(mock_review_json)

        code_to_review = "def f(): pass"
        original_requirements = "Make f better"
        code_diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-def f(): pass\n+def f(): return # better"
        related_tests = "assert f() is None"

        result = await self.reviewer.review_code(
            code_to_review, original_requirements, related_tests=related_tests, code_diff=code_diff
        )

        self.assertEqual(result, mock_review_json)
        mock_invoke_llm.assert_called_once()
        args, kwargs = mock_invoke_llm.call_args
        prompt_sent_to_llm = args[0]
        self.assertIn(code_to_review, prompt_sent_to_llm)
        self.assertIn(original_requirements, prompt_sent_to_llm)
        self.assertIn(related_tests, prompt_sent_to_llm)
        self.assertIn(code_diff, prompt_sent_to_llm)
        self.assertIn(self.mock_llm_model_name, kwargs.get("model_name"))

    @patch('ai_assistant.core.reviewer.invoke_ollama_model_async', new_callable=AsyncMock)
    async def test_review_code_without_diff(self, mock_invoke_llm):
        mock_review_json = {"status": "requires_changes", "comments": "Needs diff", "suggestions": "Provide diff"}
        mock_invoke_llm.return_value = json.dumps(mock_review_json)

        result = await self.reviewer.review_code("def f(): pass", "Make f better", code_diff=None)

        self.assertEqual(result, mock_review_json)
        args, _ = mock_invoke_llm.call_args
        prompt_sent_to_llm = args[0]
        self.assertIn("No diff provided. Full code is under review.", prompt_sent_to_llm)

    @patch('ai_assistant.core.reviewer.invoke_ollama_model_async', new_callable=AsyncMock)
    async def test_review_code_empty_diff_string(self, mock_invoke_llm):
        mock_review_json = {"status": "approved", "comments": "Empty diff string handled", "suggestions": ""}
        mock_invoke_llm.return_value = json.dumps(mock_review_json)

        result = await self.reviewer.review_code("def f(): pass", "Make f better", code_diff="   ") # Empty string

        self.assertEqual(result, mock_review_json)
        args, _ = mock_invoke_llm.call_args
        prompt_sent_to_llm = args[0]
        self.assertIn("No diff provided. Full code is under review.", prompt_sent_to_llm)

    @patch('ai_assistant.core.reviewer.invoke_ollama_model_async', new_callable=AsyncMock)
    async def test_review_code_llm_malformed_json(self, mock_invoke_llm):
        mock_invoke_llm.return_value = "This is not JSON"
        result = await self.reviewer.review_code("def f(): pass", "reqs", code_diff="diff")
        self.assertEqual(result["status"], "error")
        self.assertIn("Failed to parse LLM response as JSON", result["comments"])

    @patch('ai_assistant.core.reviewer.invoke_ollama_model_async', new_callable=AsyncMock)
    async def test_review_code_llm_empty_response(self, mock_invoke_llm):
        mock_invoke_llm.return_value = ""
        result = await self.reviewer.review_code("def f(): pass", "reqs")
        self.assertEqual(result["status"], "error")
        self.assertIn("LLM returned an empty response", result["comments"])

    async def test_review_code_missing_code_to_review(self):
        result = await self.reviewer.review_code("", "reqs")
        self.assertEqual(result["status"], "error")
        self.assertIn("No code provided for review", result["comments"])

    async def test_review_code_missing_requirements(self):
        result = await self.reviewer.review_code("def f(): pass", "")
        self.assertEqual(result["status"], "error")
        self.assertIn("Original requirements were not provided", result["comments"])

# Basic async test runner for unittest.TestCase
# This allows running async tests defined with 'async def'
# Copied from test_code_service.py for standalone execution if needed.
if __name__ == '__main__': # pragma: no cover
    suite = unittest.TestSuite()
    async_test_methods_names = []

    for name in dir(TestReviewerAgent):
        if name.startswith("test_"):
            method = getattr(TestReviewerAgent, name)
            if asyncio.iscoroutinefunction(method):
                async_test_methods_names.append(name)
            else:
                suite.addTest(TestReviewerAgent(name)) # Add sync tests directly

    if suite.countTestCases() > 0:
        runner_sync = unittest.TextTestRunner()
        print("Running synchronous tests for TestReviewerAgent...")
        runner_sync.run(suite)

    if async_test_methods_names:
        print("\nRunning asynchronous tests for TestReviewerAgent...")
        loop = asyncio.get_event_loop_policy().new_event_loop()
        asyncio.set_event_loop(loop)
        test_instance = TestReviewerAgent()

        async def run_async_tests_on_instance():
            if hasattr(test_instance, 'setUp'):
                test_instance.setUp()
            try:
                for name in async_test_methods_names:
                    await getattr(test_instance, name)()
            finally:
                if hasattr(test_instance, 'tearDown'):
                    test_instance.tearDown()

        try:
            loop.run_until_complete(run_async_tests_on_instance())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    if not suite.countTestCases() and not async_test_methods_names:
        print("No tests found for TestReviewerAgent.")
    else:
        print("\nFinished TestReviewerAgent tests.")
