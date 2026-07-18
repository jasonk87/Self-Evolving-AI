from types import SimpleNamespace

import pytest

from ai_assistant.core.critical_reviewer import CriticalReviewCoordinator
from ai_assistant.core.reviewer import ReviewerAgent


@pytest.mark.asyncio
async def test_council_security_finding_vetoes_approving_judge():
    responses = iter(
        [
            "The color regex is permissive of expression(), an old CSS XSS vector.",
            "Status: APPROVED\nReasoning: DOMPurify should block it in modern browsers.",
        ]
    )

    async def invoke(*args, **kwargs):
        return next(responses)

    coordinator = CriticalReviewCoordinator(ReviewerAgent("test-reviewer"))
    approved, reasoning = await coordinator.execute_council_debate(
        proposed_code="def unsafe_color(value): return True",
        proposal_description="Generate safe inline HTML",
        original_code="",
        module_path="generated.chart",
        llm_provider=SimpleNamespace(invoke_ollama_model_async=invoke),
    )

    assert approved is False
    assert "security veto" in reasoning.casefold()


@pytest.mark.asyncio
async def test_council_allows_approval_when_skeptic_finds_no_security_issue():
    responses = iter(
        [
            "No security or reliability flaws found; values are escaped and colors are allowlisted.",
            "Status: APPROVED\nReasoning: The implementation is safe and correct.",
        ]
    )

    async def invoke(*args, **kwargs):
        return next(responses)

    coordinator = CriticalReviewCoordinator(ReviewerAgent("test-reviewer"))
    approved, _ = await coordinator.execute_council_debate(
        proposed_code="def safe(value): return value",
        proposal_description="Generate safe inline HTML",
        original_code="",
        module_path="generated.chart",
        llm_provider=SimpleNamespace(invoke_ollama_model_async=invoke),
    )

    assert approved is True
