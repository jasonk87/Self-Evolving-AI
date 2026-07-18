import asyncio
import re
from typing import List, Dict, Any, Tuple, Optional

# Attempt to import ReviewerAgent with fallback for different execution contexts
try:
    from ai_assistant.core.reviewer import ReviewerAgent
    from ai_assistant.core.events import emit_system_event
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
    # Mock emitter if fallback
    def emit_system_event(*args, **kwargs): pass


class CriticalReviewCoordinator:
    def __init__(self, critic: ReviewerAgent):
        """
        Initializes the CriticalReviewCoordinator with a single ReviewerAgent.
        Args:
            critic: The ReviewerAgent instance.
        """
        if not isinstance(critic, ReviewerAgent):
            raise TypeError("Critic must be an instance of ReviewerAgent.")
        self.critic = critic

    async def request_critical_review(
        self,
        original_code: str,
        new_code_string: str,
        code_diff: str,
        original_requirements: str,
        related_tests: Optional[str] = None
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Requests a review from the single critic.

        Args:
            original_code: The original code string (used for context if needed by reviewers).
            new_code_string: The new code string to be reviewed (SHOULD BE FULL FILE CONTENT).
            code_diff: The diff string showing changes from original to new code.
            original_requirements: Description of the original requirements or the goal of the change.
            related_tests: Optional string of related test cases.

        Returns:
            A tuple: (is_approved: bool, reviews: List[Dict[str, Any]]).
            'reviews' contains the single review dictionary (list for backward compatibility).
        """

        emit_system_event("review_stage_started", {
            "stage": "critical_review",
            "message": "Initializing critical review session."
        })

        emit_system_event("critic_thinking", {"critic": "Main Critic", "message": "Analyzing code..."})

        review = await self.critic.review_code(
            code_to_review=new_code_string,
            original_requirements=original_requirements,
            related_tests=related_tests,
            code_diff=code_diff,
            attempt_number=1
        )

        emit_system_event("critic_verdict", {
            "critic": "Main Critic",
            "status": review.get("status"),
            "comments": review.get("comments"),
            "suggestions": review.get("suggestions")
        })

        collected_reviews = [review]

        is_approved = review.get("status") == "approved"

        # Consider a review invalid if it's an error status
        if review.get("status") == "error":
            is_approved = False
            # Potentially log this error
            print(f"Warning: Critic returned an error status: {review.get('comments')}")

        emit_system_event("review_round_completed", {
            "unanimous_approval": is_approved,
            "approved_count": 1 if is_approved else 0,
            "total_critics": 1
        })

        return is_approved, collected_reviews

    async def execute_council_debate(
        self,
        proposed_code: str,
        proposal_description: str,
        original_code: str,
        module_path: str,
        llm_provider: Any # Using Any to avoid import cycles, assumes it matches provider interface
    ) -> Tuple[bool, str]:
        """
        Executes an adversarial 'Council Debate' for high-risk self-modification.

        Roles:
        - The Skeptic: Attacks the proposal looking for safety/security risks.
        - The Judge: Decides whether to proceed based on the proposal and critique.

        Returns:
            Tuple[bool, str]: (is_approved, reasoning_summary)
        """
        from ai_assistant.config import get_model_for_task # Imported here to avoid circular imports if moved

        emit_system_event("council_session_started", {
            "message": "High-risk task detected. Convening The Council for adversarial debate."
        })

        # 1. The Skeptic
        skeptic_model = get_model_for_task("council_skeptic")
        skeptic_prompt = f"""
        You are 'The Skeptic', a highly critical security and reliability auditor for an AI system.

        Context: The AI is attempting to modify its own code (Self-Modification).
        Module: `{module_path}`
        Proposal: {proposal_description}

        Existing Code:
        ```python
        {original_code}
        ```

        Proposed New Code:
        ```python
        {proposed_code}
        ```

        Your Goal: Find CRITICAL flaws (security, infinite loops), but prioritization SUCCESS.
        - SECURITY: Check for execution of arbitrary code from external strings. Standard function calls are SAFE.
        - INFINITE LOOPS: Check for while loops without exits.
        - ROBUSTNESS: If the change fixes a crash or bug, it is HIGH VALUE. Approve it even if there are minor style issues or if you think the caller should be fixed instead.
        - DIFFS: Do NOT reject based on "incomplete diffs" if the intention is clear.
        - FUNCTIONALITY: If the code runs and fixes the problem, biased towards APPROVAL.
        - Nitpicking is allowed but should result in APPROVAL unless the flaw is fatal.

        Output your critique concisely.
        """

        emit_system_event("council_skeptic_thinking", {"message": "The Skeptic is analyzing risks..."})
        if hasattr(llm_provider, 'invoke_ollama_model_async'):
             skeptic_response = await llm_provider.invoke_ollama_model_async(skeptic_prompt, model_name=skeptic_model, temperature=0.7)
        else: # Fallback or mock
             skeptic_response = "Skeptic analysis unavailable."

        emit_system_event("council_skeptic_verdict", {"critique": skeptic_response})

        # 2. The Judge
        judge_model = get_model_for_task("council_judge")
        judge_prompt = f"""
        You are 'The Judge', the final decision maker for an AI system's self-evolution.

        Proposal: {proposal_description}

        The Skeptic's Critique:
        {skeptic_response}

        Your Goal: Weigh the proposal against the critique.
        - If the critique highlights any unresolved security flaw (including XSS, injection, unsafe CSS/HTML, arbitrary code execution, or path traversal), REJECT.
        - Do not rely on a downstream browser, sanitizer, caller, or deprecated-browser assumption to excuse unsafe output from the proposed code.
        - If the critique is minor, nitpicky, or theoretical (e.g. "caller should be fixed"), APPROVE.
        - If the change improves ROBUSTNESS (e.g. handling more inputs, fixing crashes), APPROVE IT.
        - If the Skeptic complains about "security" for standard input handling, OVERRULE and APPROVE.
        - If the code looks safe and correct, APPROVE.

        Output Format:
        Status: [APPROVED | REJECTED]
        Reasoning: <Your explanation>
        """

        emit_system_event("council_judge_thinking", {"message": "The Judge is deliberating..."})
        if hasattr(llm_provider, 'invoke_ollama_model_async'):
             judge_response = await llm_provider.invoke_ollama_model_async(judge_prompt, model_name=judge_model, temperature=0.3, task_name="council_judge")
        else:
             judge_response = "Status: REJECTED\nReasoning: LLM provider unavailable for judgment."

        # Robust parsing for Judge's verdict
        is_approved = False
        if "Status: APPROVED" in judge_response or "Status: APPROVE" in judge_response:
             is_approved = True

        unresolved_security_patterns = (
            r"\bxss\s+(?:risk|vector|vulnerability)",
            r"\bcross-site scripting\b",
            r"\b(?:command|sql|code|html|css) injection\b",
            r"\barbitrary code execution\b",
            r"\bpath traversal\b",
            r"\bpermissive\b.{0,80}\bexpression\s*\(",
            r"\bunsafe\s+(?:eval|exec|html|css|deserialization)\b",
        )
        security_veto = any(
            re.search(pattern, str(skeptic_response or ""), flags=re.IGNORECASE | re.DOTALL)
            for pattern in unresolved_security_patterns
        )
        if security_veto:
            is_approved = False

        # Clean reasoning extraction
        reasoning = judge_response.replace("Status: APPROVED", "").replace("Status: APPROVE", "").replace("Status: REJECTED", "").strip()
        if reasoning.startswith("Reasoning:"):
            reasoning = reasoning[10:].strip()
        if security_veto:
            reasoning = (
                "Deterministic security veto: the Skeptic identified an unresolved security risk. "
                "The proposal must remove that risk before approval. " + reasoning
            ).strip()

        emit_system_event("council_judge_verdict", {
            "approved": is_approved,
            "reasoning": reasoning
        })

        return is_approved, reasoning

if __name__ == '__main__': # pragma: no cover
    # Example Usage (requires ReviewerAgent and a running LLM for ReviewerAgent)
    async def example_main():
        class MockReviewerAgent(ReviewerAgent):
            def __init__(self, name: str, mock_review_response: Dict[str, Any]):
                super().__init__(llm_model_name="mock_model_for_coordinator_test") # Avoids config/LLM issues for this direct run
                self.name = name
                self.mock_response = mock_review_response
                print(f"MockReviewerAgent '{self.name}' initialized.")

            async def review_code(self, **kwargs) -> Dict[str, Any]:
                print(f"MockReviewerAgent '{self.name}' review_code called. Returning mock response.")
                await asyncio.sleep(0.01)
                return self.mock_response

        critic_response_approved = {"status": "approved", "comments": "Looks good!", "suggestions": ""}
        critic_response_changes = {"status": "requires_changes", "comments": "Needs tweaks.", "suggestions": "Fix line 10."}
        critic_response_error = {"status": "error", "comments": "LLM failed.", "suggestions": ""}

        coordinator1 = CriticalReviewCoordinator(
            MockReviewerAgent("Alpha", critic_response_approved)
        )
        coordinator2 = CriticalReviewCoordinator(
            MockReviewerAgent("Beta", critic_response_changes)
        )
        coordinator3 = CriticalReviewCoordinator(
            MockReviewerAgent("Gamma", critic_response_error)
        )

        sample_code = "def func(): return 1"
        sample_diff = "diff"

        print("\n--- Testing Coordinator 1 (Approved) ---")
        approved1, reviews1 = await coordinator1.request_critical_review(sample_code, sample_code, sample_diff, "reqs")
        print(f"Approved: {approved1}")
        assert approved1 is True

        print("\n--- Testing Coordinator 2 (Requires Changes) ---")
        approved2, reviews2 = await coordinator2.request_critical_review(sample_code, sample_code, sample_diff, "reqs")
        print(f"Approved: {approved2}")
        assert approved2 is False

        print("\n--- Testing Coordinator 3 (Error) ---")
        approved3, reviews3 = await coordinator3.request_critical_review(sample_code, sample_code, sample_diff, "reqs")
        print(f"Approved: {approved3}")
        assert approved3 is False

    asyncio.run(example_main())
