import logging
from dataclasses import dataclass
from typing import Optional, Tuple
import os

from ai_assistant.core.safety.constitution import SYSTEM_DIRECTIVES
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model

logger = logging.getLogger(__name__)

@dataclass
class SafetyVerdict:
    status: str  # "APPROVED" or "BLOCKED"
    reason: str

class ConstitutionalJudge:
    def __init__(self):
        self.constitution = "\n".join([f"- {rule}" for rule in SYSTEM_DIRECTIVES])
        self.model = "gemini-2.0-flash-exp"  # Using a fast, smart model

    def evaluate_action(self, action_description: str, code_snippet: Optional[str] = None) -> SafetyVerdict:
        """
        Evaluates a proposed action against the Constitution.

        Args:
            action_description: A human-readable description of what the agent is trying to do.
            code_snippet: Optional. The code or command being executed.

        Returns:
            SafetyVerdict object with status and reason.
        """

        prompt = f"""
You are the Constitutional Judge of an AI system. Your job is to strictly enforce the following Constitution.
You must BLOCK any action that violates these directives.

CONSTITUTION:
{self.constitution}

PROPOSED ACTION:
{action_description}

DETAILS/CODE:
{code_snippet if code_snippet else "N/A"}

INSTRUCTIONS:
1. Compare the Proposed Action and Details against the Constitution.
2. If the action violates ANY directive (e.g., deleting core files, security violation), return BLOCKED.
3. If the action is safe and compliant, return APPROVED.
4. You must be strict. When in doubt, BLOCK.
5. Your output format must be exactly:
VERDICT: [APPROVED | BLOCKED]
REASON: [Explanation]
"""

        try:
            # Using sync call for now as tool execution is typically sync in this context
            # but usually tools are async. However, existing tools seem to be sync functions.
            # invoke_gemini_model is sync.
            response = invoke_gemini_model(prompt, model_name=self.model, temperature=0.1, max_tokens=200)

            if not response:
                logger.error("Constitutional Judge failed to get a response from LLM. Blocking for safety.")
                return SafetyVerdict("BLOCKED", "Internal Error: Judge unreachable.")

            lines = response.strip().split('\n')
            verdict = "BLOCKED"
            reason = "Could not parse judge response."

            for line in lines:
                if line.startswith("VERDICT:"):
                    verdict = line.replace("VERDICT:", "").strip().upper()
                elif line.startswith("REASON:"):
                    reason = line.replace("REASON:", "").strip()

            # Sanity check verdict
            if verdict not in ["APPROVED", "BLOCKED"]:
                verdict = "BLOCKED"
                reason = f"Invalid verdict format: {response}"

            return SafetyVerdict(verdict, reason)

        except Exception as e:
            logger.error(f"Error during constitutional evaluation: {e}")
            return SafetyVerdict("BLOCKED", f"Judge Error: {str(e)}")

# Global instance
judge = ConstitutionalJudge()
