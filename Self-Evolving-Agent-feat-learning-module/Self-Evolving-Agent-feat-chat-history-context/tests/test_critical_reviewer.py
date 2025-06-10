import unittest
from unittest.mock import AsyncMock
import asyncio
import sys
import os

# Ensure the 'ai_assistant' module can be imported
try:
    from ai_assistant.core.critical_reviewer import CriticalReviewCoordinator
    from ai_assistant.core.reviewer import ReviewerAgent # Needed for type checking if not mocking everything
except ImportError: # pragma: no cover
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.critical_reviewer import CriticalReviewCoordinator
    from ai_assistant.core.reviewer import ReviewerAgent


class TestCriticalReviewCoordinator(unittest.TestCase):

    def setUp(self):
        self.mock_critic1 = AsyncMock(spec=ReviewerAgent)
        self.mock_critic2 = AsyncMock(spec=ReviewerAgent)
        self.coordinator = CriticalReviewCoordinator(self.mock_critic1, self.mock_critic2)

        # Define common inputs for review requests
        self.original_code = "def main():\n    print('old')"
        self.new_code = "def main():\n    print('new')"
        self.code_diff = "-    print('old')\n+    print('new')"
        self.requirements = "Update print statement."
        self.tests_info = "Test: Check if 'new' is printed."

    async def test_request_critical_review_unanimous_approval(self):
        self.mock_critic1.review_code.return_value = {"status": "approved", "comments": "C1 OK"}
        self.mock_critic2.review_code.return_value = {"status": "approved", "comments": "C2 OK"}

        approved, reviews = await self.coordinator.request_critical_review(
            self.original_code, self.new_code, self.code_diff, self.requirements, self.tests_info
        )
        self.assertTrue(approved)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(reviews[0]["status"], "approved")
        self.assertEqual(reviews[1]["status"], "approved")
        self.mock_critic1.review_code.assert_called_once()
        self.mock_critic2.review_code.assert_called_once()

    async def test_request_critical_review_one_requires_changes(self):
        self.mock_critic1.review_code.return_value = {"status": "approved", "comments": "C1 OK"}
        self.mock_critic2.review_code.return_value = {"status": "requires_changes", "comments": "C2 Needs Work"}

        approved, reviews = await self.coordinator.request_critical_review(
            self.original_code, self.new_code, self.code_diff, self.requirements
        )
        self.assertFalse(approved)
        self.assertEqual(len(reviews), 2)
        self.assertIn({"status": "requires_changes", "comments": "C2 Needs Work"}, reviews)

    async def test_request_critical_review_one_rejected(self):
        self.mock_critic1.review_code.return_value = {"status": "rejected", "comments": "C1 Bad"}
        self.mock_critic2.review_code.return_value = {"status": "approved", "comments": "C2 OK"}

        approved, reviews = await self.coordinator.request_critical_review(
            self.original_code, self.new_code, self.code_diff, self.requirements
        )
        self.assertFalse(approved)

    async def test_request_critical_review_both_require_changes(self):
        self.mock_critic1.review_code.return_value = {"status": "requires_changes", "comments": "C1 Needs Work"}
        self.mock_critic2.review_code.return_value = {"status": "requires_changes", "comments": "C2 Also Needs Work"}

        approved, reviews = await self.coordinator.request_critical_review(
            self.original_code, self.new_code, self.code_diff, self.requirements
        )
        self.assertFalse(approved)

    async def test_request_critical_review_one_critic_errors(self):
        self.mock_critic1.review_code.return_value = {"status": "approved", "comments": "C1 OK"}
        self.mock_critic2.review_code.return_value = {"status": "error", "comments": "C2 LLM Down"}

        approved, reviews = await self.coordinator.request_critical_review(
            self.original_code, self.new_code, self.code_diff, self.requirements
        )
        self.assertFalse(approved) # Should not be approved if a critic had an error
        self.assertEqual(len(reviews), 2)
        self.assertIn({"status": "error", "comments": "C2 LLM Down"}, reviews)

    async def test_request_critical_review_both_critics_error(self):
        self.mock_critic1.review_code.return_value = {"status": "error", "comments": "C1 LLM Down"}
        self.mock_critic2.review_code.return_value = {"status": "error", "comments": "C2 LLM Down"}

        approved, reviews = await self.coordinator.request_critical_review(
            self.original_code, self.new_code, self.code_diff, self.requirements
        )
        self.assertFalse(approved)
        self.assertEqual(len(reviews), 2)

    def test_init_invalid_critic_type(self):
        with self.assertRaises(TypeError):
            CriticalReviewCoordinator("not_a_reviewer_agent", self.mock_critic2) # type: ignore
        with self.assertRaises(TypeError):
            CriticalReviewCoordinator(self.mock_critic1, "not_a_reviewer_agent") # type: ignore

# Custom test runner for async methods in a sync class (unittest.TestCase)
if __name__ == '__main__': # pragma: no cover
    suite = unittest.TestSuite()
    async_tests = []
    for name in dir(TestCriticalReviewCoordinator):
        if name.startswith("test_") and asyncio.iscoroutinefunction(getattr(TestCriticalReviewCoordinator, name)):
            async_tests.append(name)
        elif name.startswith("test_"):
            suite.addTest(TestCriticalReviewCoordinator(name))

    if suite.countTestCases() > 0:
        runner = unittest.TextTestRunner()
        runner.run(suite)

    if async_tests:
        loop = asyncio.get_event_loop_policy().new_event_loop()
        asyncio.set_event_loop(loop)
        test_instance = TestCriticalReviewCoordinator()

        async def run_async_tests_on_instance():
            if hasattr(test_instance, 'setUp'):
                test_instance.setUp()
            for name in async_tests:
                await getattr(test_instance, name)()
            if hasattr(test_instance, 'tearDown'):
                test_instance.tearDown() # type: ignore

        try:
            loop.run_until_complete(run_async_tests_on_instance())
        finally:
            loop.close()
