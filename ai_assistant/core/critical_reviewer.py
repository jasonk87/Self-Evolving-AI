import asyncio
from typing import List, Dict, Any, Tuple, Optional

# Attempt to import ReviewerAgent with fallback for different execution contexts
try:
    from ai_assistant.core.reviewer import ReviewerAgent
except ImportError: # pragma: no cover
    # This fallback might be needed if running this file directly for testing
    # or if PYTHONPATH is not perfectly set up in some environments.
    # Adjust the path based on your project structure.
    import sys
    import os
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.reviewer import ReviewerAgent


class CriticalReviewCoordinator:
    def __init__(self, critic1: ReviewerAgent, critic2: ReviewerAgent):
        """
        Initializes the CriticalReviewCoordinator with two ReviewerAgent instances.
        Args:
            critic1: The first ReviewerAgent instance.
            critic2: The second ReviewerAgent instance.
        """
        if not isinstance(critic1, ReviewerAgent) or not isinstance(critic2, ReviewerAgent):
            raise TypeError("Both critic1 and critic2 must be instances of ReviewerAgent.")
        self.critic1 = critic1
        self.critic2 = critic2

    async def request_critical_review(
        self,
        original_code: str,
        new_code_string: str,
        code_diff: str,
        original_requirements: str,
        related_tests: Optional[str] = None
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Requests a review from two critics and determines if there's unanimous approval.

        Args:
            original_code: The original code string (used for context if needed by reviewers).
            new_code_string: The new code string to be reviewed.
            code_diff: The diff string showing changes from original to new code.
            original_requirements: Description of the original requirements or the goal of the change.
            related_tests: Optional string of related test cases.

        Returns:
            A tuple: (unanimous_approval: bool, reviews: List[Dict[str, Any]]).
            'reviews' contains the review dictionaries from both critics.
        """
        review_tasks = [
            self.critic1.review_code(
                code_to_review=new_code_string, # Critics review the proposed new code
                original_requirements=original_requirements,
                related_tests=related_tests,
                code_diff=code_diff,
                attempt_number=1 # Assuming first attempt for critical review
            ),
            self.critic2.review_code(
                code_to_review=new_code_string,
                original_requirements=original_requirements,
                related_tests=related_tests,
                code_diff=code_diff,
                attempt_number=1 # Assuming first attempt for critical review
            )
        ]

        collected_reviews: List[Dict[str, Any]] = await asyncio.gather(*review_tasks)

        approved_count = 0
        all_reviews_valid = True
        for review in collected_reviews:
            if review.get("status") == "approved":
                approved_count += 1
            # Consider a review invalid if it's an error status from the reviewer itself
            if review.get("status") == "error":
                all_reviews_valid = False
                # Potentially log this error or include it in the review list for upstream handling
                print(f"Warning: A critic returned an error status: {review.get('comments')}")


        # Unanimous approval means both critics approved and neither had an internal error
        unanimous_approval = all_reviews_valid and (approved_count == 2)

        return unanimous_approval, collected_reviews

if __name__ == '__main__': # pragma: no cover
    # Example Usage (requires ReviewerAgent and a running LLM for ReviewerAgent)
    async def example_main():
        # This is a simplified example. In a real scenario, ReviewerAgent
        # would be configured with an LLM. Here, we might need to mock it
        # if we don't want to make actual LLM calls during this direct execution.

        # For this example, let's create placeholder ReviewerAgents.
        # They won't make real LLM calls unless ReviewerAgent is modified
        # to have a mockable part or if an LLM is running.
        class MockReviewerAgent(ReviewerAgent):
            def __init__(self, name: str, mock_review_response: Dict[str, Any]):
                super().__init__(llm_model_name="mock_model_for_coordinator_test") # Avoids config/LLM issues for this direct run
                self.name = name
                self.mock_response = mock_review_response
                print(f"MockReviewerAgent '{self.name}' initialized.")


            async def review_code(self, **kwargs) -> Dict[str, Any]:
                print(f"MockReviewerAgent '{self.name}' review_code called. Returning mock response.")
                # Simulate some async behavior if needed
                await asyncio.sleep(0.01)
                return self.mock_response

        critic_alpha_response_approved = {"status": "approved", "comments": "Looks good!", "suggestions": ""}
        critic_beta_response_approved = {"status": "approved", "comments": "Excellent work.", "suggestions": ""}
        critic_gamma_response_changes = {"status": "requires_changes", "comments": "Needs minor tweaks.", "suggestions": "Fix line 10."}
        critic_delta_response_error = {"status": "error", "comments": "LLM failed for delta.", "suggestions": ""}


        coordinator1 = CriticalReviewCoordinator(
            MockReviewerAgent("Alpha", critic_alpha_response_approved),
            MockReviewerAgent("Beta", critic_beta_response_approved)
        )
        coordinator2 = CriticalReviewCoordinator(
            MockReviewerAgent("Alpha", critic_alpha_response_approved),
            MockReviewerAgent("Gamma", critic_gamma_response_changes)
        )
        coordinator3 = CriticalReviewCoordinator(
            MockReviewerAgent("Alpha", critic_alpha_response_approved),
            MockReviewerAgent("Delta", critic_delta_response_error) # One critic has an error
        )


        sample_original_code = "def func():\n  return 1"
        sample_new_code = "def func():\n  return 2"
        sample_diff = "- return 1\n+ return 2"
        sample_reqs = "Change return value to 2."

        print("\n--- Testing Coordinator 1 (Both Approve) ---")
        approved1, reviews1 = await coordinator1.request_critical_review(
            sample_original_code, sample_new_code, sample_diff, sample_reqs
        )
        print(f"Unanimous Approval: {approved1}")
        print(f"Reviews: {reviews1}")
        assert approved1 is True

        print("\n--- Testing Coordinator 2 (One Requires Changes) ---")
        approved2, reviews2 = await coordinator2.request_critical_review(
            sample_original_code, sample_new_code, sample_diff, sample_reqs
        )
        print(f"Unanimous Approval: {approved2}")
        print(f"Reviews: {reviews2}")
        assert approved2 is False

        print("\n--- Testing Coordinator 3 (One Errors) ---")
        approved3, reviews3 = await coordinator3.request_critical_review(
            sample_original_code, sample_new_code, sample_diff, sample_reqs
        )
        print(f"Unanimous Approval: {approved3}") # Should be False because one critic had an error
        print(f"Reviews: {reviews3}")
        assert approved3 is False


    asyncio.run(example_main())
