import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
from ai_assistant.core.critical_reviewer import CriticalReviewCoordinator
from ai_assistant.core.reviewer import ReviewerAgent

class TestCriticalReviewCoordinator(unittest.TestCase):
    def setUp(self):
        self.mock_critic = AsyncMock(spec=ReviewerAgent)
        self.coordinator = CriticalReviewCoordinator(self.mock_critic)
        self.code_to_review = "def foo(): pass"
        self.diff = "+ def foo(): pass"
        self.reqs = "Implement foo"

    def test_init_invalid_critic_type(self):
        with self.assertRaises(TypeError):
            CriticalReviewCoordinator("not a reviewer agent")

    def test_request_critical_review_approved(self):
        self.mock_critic.review_code.return_value = {
            "status": "approved",
            "comments": "Looks good",
            "suggestions": ""
        }

        async def run_test():
            is_approved, reviews = await self.coordinator.request_critical_review(
                self.code_to_review, self.code_to_review, self.diff, self.reqs
            )
            self.assertTrue(is_approved)
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[0]["status"], "approved")

        asyncio.run(run_test())

    def test_request_critical_review_rejected(self):
        self.mock_critic.review_code.return_value = {
            "status": "rejected",
            "comments": "Bad code",
            "suggestions": "Fix it"
        }

        async def run_test():
            is_approved, reviews = await self.coordinator.request_critical_review(
                self.code_to_review, self.code_to_review, self.diff, self.reqs
            )
            self.assertFalse(is_approved)
            self.assertEqual(reviews[0]["status"], "rejected")

        asyncio.run(run_test())

    def test_request_critical_review_error(self):
        self.mock_critic.review_code.return_value = {
            "status": "error",
            "comments": "LLM failed",
            "suggestions": ""
        }

        async def run_test():
            is_approved, reviews = await self.coordinator.request_critical_review(
                self.code_to_review, self.code_to_review, self.diff, self.reqs
            )
            self.assertFalse(is_approved)
            self.assertEqual(reviews[0]["status"], "error")

        asyncio.run(run_test())

if __name__ == '__main__':
    unittest.main()
