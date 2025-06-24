# Self-Evolving-Agent-feat-chat-history-context/ai_assistant/core/refinement.py
import re
from typing import Dict, Any, Optional

from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task, is_debug_mode

REFINE_CODE_PROMPT_TEMPLATE = """
You are an AI assistant tasked with refining Python code based on a review.

Original Requirements:
{requirements}

The following Python code was generated to meet these requirements:
```python
{original_code}
```

This code was reviewed, and the review outcome was:
Status: {review_status}
Comments: {review_comments}
Suggestions for Improvement: {review_suggestions}

Your task is to carefully analyze the review feedback (comments and suggestions)
and rewrite the original code to address all the points raised.
The refined code must still meet the original requirements.

Respond ONLY with the complete, new, refined Python code block.
Do not include any explanations, apologies, or markdown formatting like ```python.
"""

class RefinementAgent:
    def __init__(self, llm_model_name: Optional[str] = None):
        """
        Initializes the RefinementAgent.
        Args:
            llm_model_name: Optional name of the LLM model to use for refinement.
                            If None, it will be determined by `get_model_for_task`.
        """
        self.model_name = llm_model_name if llm_model_name else get_model_for_task("code_refinement")
        if not self.model_name:
            # Fallback if "code_refinement" is not defined
            self.model_name = get_model_for_task("code_generation") # Or another capable model
            if not self.model_name:
                print("Warning: No model configured for 'code_refinement' or 'code_generation'. Refinement quality may be affected.")
                # self.model_name = "phi3:latest" # Example, if a known capable model exists
                pass

    async def refine_code(
        self,
        original_code: str,
        requirements: str,
        review_feedback: Dict[str, Any]
    ) -> str:
        """
        Refines the given code based on review feedback.

        Args:
            original_code: The original Python code string.
            requirements: The original requirements for the code.
            review_feedback: A dictionary containing review details like
                             "status", "comments", and "suggestions".

        Returns:
            The refined code string. Returns an empty string if LLM fails to generate
            a response or if the response is empty after cleaning.
        """
        review_status = review_feedback.get("status", "N/A")
        review_comments = review_feedback.get("comments", "No comments provided.")
        review_suggestions = review_feedback.get("suggestions") # Can be None
        if review_suggestions is None or not str(review_suggestions).strip():
            review_suggestions = "No specific suggestions provided."


        prompt = REFINE_CODE_PROMPT_TEMPLATE.format(
            original_code=original_code,
            requirements=requirements,
            review_status=review_status,
            review_comments=review_comments,
            review_suggestions=review_suggestions
        )

        if is_debug_mode():
            print(f"[DEBUG] RefinementAgent: requirements={requirements}")
            print(f"[DEBUG] RefinementAgent: original_code (first 200 chars)={original_code[:200]}")
            print(f"[DEBUG] RefinementAgent: review_status={review_status}")
            print(f"[DEBUG] RefinementAgent: review_comments={review_comments}")
            print(f"[DEBUG] RefinementAgent: review_suggestions={review_suggestions}")

        llm_response_str = await invoke_ollama_model_async(
            prompt,
            model_name=self.model_name,
            temperature=0.4 # Slightly lower for refinement to be less creative than initial gen
        )

        if not llm_response_str or not llm_response_str.strip():
            print("Warning: LLM returned an empty response during code refinement.")
            return "" # Return empty string if no response

        # Clean LLM output (remove markdown fences)
        cleaned_code = re.sub(r"^\s*```python\s*\n?", "", llm_response_str, flags=re.IGNORECASE | re.MULTILINE)
        cleaned_code = re.sub(r"\n?\s*```\s*$", "", cleaned_code, flags=re.IGNORECASE | re.MULTILINE).strip()
        
        if not cleaned_code:
            print("Warning: LLM response was empty after cleaning markdown for code refinement.")
            return ""

        if is_debug_mode():
            print(f"[DEBUG] LLM refinement response (first 200 chars): {llm_response_str[:200]}")

        return cleaned_code

if __name__ == '__main__':
    # Example Usage (requires Ollama server running with a suitable model)
    async def main():
        # Ensure a model for "code_refinement" or "code_generation" is configured
        # For testing, you might explicitly pass a model:
        # refinement_agent = RefinementAgent(llm_model_name="your_local_ollama_code_model:latest")
        refinement_agent = RefinementAgent()

        original_code_sample = """
def calculate_sum(a, b):
    # This function just adds two numbers
    result = a + b
    return result
"""
        requirements_sample = "Create a Python function `calculate_sum` that takes two numerical inputs, `a` and `b`, and returns their sum. The function should also include a docstring explaining its purpose, arguments, and return value. Input `a` must be positive."

        review_feedback_sample = {
            "status": "requires_changes",
            "comments": "The function correctly adds two numbers, but it's missing the docstring. Also, the requirement that 'a' must be positive is not enforced.",
            "suggestions": "1. Add a comprehensive docstring. 2. Add a check at the beginning of the function to ensure 'a' is positive; if not, raise a ValueError."
        }
        
        empty_suggestions_feedback = {
            "status": "requires_changes",
            "comments": "Missing docstring and validation for 'a'.",
            "suggestions": None # Test None suggestions
        }
        
        blank_suggestions_feedback = {
            "status": "requires_changes",
            "comments": "Missing docstring and validation for 'a'.",
            "suggestions": "   " # Test blank suggestions
        }


        print("\n--- Refining Code Sample 1 (with suggestions) ---")
        refined_code1 = await refinement_agent.refine_code(original_code_sample, requirements_sample, review_feedback_sample)
        print("Refined Code 1:")
        print(refined_code1)
        
        print("\n--- Refining Code Sample 2 (with empty suggestions) ---")
        refined_code2 = await refinement_agent.refine_code(original_code_sample, requirements_sample, empty_suggestions_feedback)
        print("Refined Code 2 (should be similar to 1 if LLM is good):")
        print(refined_code2)

        print("\n--- Refining Code Sample 3 (with blank suggestions) ---")
        refined_code3 = await refinement_agent.refine_code(original_code_sample, requirements_sample, blank_suggestions_feedback)
        print("Refined Code 3 (should be similar to 1 if LLM is good):")
        print(refined_code3)

        # Test with LLM returning empty response (mocked if possible, or hope it doesn't happen)
        # To truly test this, you'd mock invoke_ollama_model_async to return "" or None
        print("\n--- Refining Code Sample 4 (expecting empty from LLM - manual test or mock needed) ---")
        # This part is harder to test without mocking the LLM call to return empty
        # For now, assume it might generate something or we'd mock `invoke_ollama_model_async` in a real test suite
        # Example of how you might test a direct empty response from LLM:
        # with patch('ai_assistant.core.refinement.invoke_ollama_model_async', AsyncMock(return_value="")):
        #     refined_code_empty_llm = await refinement_agent.refine_code(original_code_sample, requirements_sample, review_feedback_sample)
        #     print(f"Refined Code (Empty LLM): '{refined_code_empty_llm}'") # Expect empty string


    import asyncio
    asyncio.run(main())

# For potential import into __init__.py or other modules
__all__ = ['RefinementAgent', 'REFINE_CODE_PROMPT_TEMPLATE']
