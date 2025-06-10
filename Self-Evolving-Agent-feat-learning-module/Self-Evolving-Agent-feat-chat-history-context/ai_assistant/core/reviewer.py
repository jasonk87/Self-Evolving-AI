# Self-Evolving-Agent-feat-chat-history-context/ai_assistant/core/reviewer.py
import json
from typing import Optional, Dict, Any

from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task

REVIEW_CODE_PROMPT_TEMPLATE = """
You are a meticulous AI code reviewer. Your task is to review the provided code based on the given requirements and related tests (if any).

**Code to Review:**
```
{code_to_review}
```

**Code Diff (Changes Made):**
```
{code_diff}
```

**Original Requirements:**
{original_requirements}

**Related Tests (if provided):**
```
{related_tests}
```

**Review Criteria:**
0.  **Focus of Review**:
    *   If a `Code Diff` is provided and is not empty, focus your review primarily on the *changes* presented in the diff. Assess their correctness, impact on the existing code, and adherence to requirements.
    *   If the `Code Diff` is empty (e.g., "No diff provided..."), or if it represents a completely new file or a very substantial rewrite, then review the entire `Code to Review`.
1.  **Adherence to Original Requirements**: Does the code meet all specified requirements? Are there any deviations or missed functionalities?
2.  **Correctness & Potential Bugs**: Are there any logical errors, potential bugs, or edge cases not handled?
3.  **Alignment with Related Tests**: If tests are provided, how well would the code likely pass them? Does the code address the scenarios covered by the tests?
4.  **Clarity, Readability, and Maintainability**: Is the code clear, well-documented (if applicable), and easy to understand? Are variable names descriptive? (Provide a brief assessment).
5.  **Safety and Security (If Applicable)**: Does the change introduce any potential security vulnerabilities (e.g., injection flaws, unsafe handling of data, exposure of sensitive information), risks, or unintended interactions, especially if this code is part of the AI assistant's own operational logic? If this criterion is not applicable to the given code, you may state 'N/A'.

**Output Structure:**
You *MUST* respond with a single JSON object. Do not include any other text or explanations before or after the JSON object.
The JSON object must contain the following keys:
-   `"status"`: String - One of "approved", "requires_changes", or "rejected".
    -   "approved": Code meets requirements, seems correct, and is well-written.
    -   "requires_changes": Code is largely on track but has issues (e.g., missed requirements, minor bugs, clarity issues) that could be fixed.
    -   "rejected": Code is fundamentally flawed, significantly deviates from requirements, or has critical errors.
-   `"comments"`: String - A detailed textual summary of your review findings, explaining the reasoning for the status. Be specific.
-   `"suggestions"`: String (Optional) - If status is "requires_changes", provide specific, actionable suggestions for improvement. If status is "approved" or "rejected", this can be an empty string or omitted.

**Example JSON Output for "requires_changes":**
```json
{{
  "status": "requires_changes",
  "comments": "The code correctly implements the addition feature but misses the requirement to handle negative numbers. The variable names 'a' and 'b' could be more descriptive. The provided tests only cover positive integers, so test coverage for negative inputs is missing.",
  "suggestions": "1. Add a check for negative input values and clarify how they should be handled based on requirements (e.g., return an error, use absolute values). 2. Rename variable 'a' to 'first_number' and 'b' to 'second_number' for better readability. 3. Consider adding test cases for negative inputs and zero values."
}}
```

**Example JSON Output for "approved":**
```json
{{
  "status": "approved",
  "comments": "The code perfectly meets all specified requirements, including handling of edge cases discussed. It is clear, readable, and the provided tests cover the main functionality effectively.",
  "suggestions": ""
}}
```

**Example JSON Output for "rejected":**
```json
{{
  "status": "rejected",
  "comments": "The proposed change introduces a critical security flaw by exposing raw eval to user input. The approach also fundamentally misunderstands the requirement to sanitize inputs.",
  "suggestions": "Re-evaluate the input handling mechanism entirely. Avoid direct evaluation of user-provided strings. Consider using a safer parsing method or a predefined command structure."
}}
```

Now, please review the provided code.
"""

class ReviewerAgent:
    def __init__(self, llm_model_name: Optional[str] = None):
        """
        Initializes the ReviewerAgent.
        Args:
            llm_model_name: Optional name of the LLM model to use for reviews.
                            If None, it will be determined by `get_model_for_task`.
        """
        self.llm_model_name = llm_model_name if llm_model_name else get_model_for_task("code_reviewer")
        if not self.llm_model_name:
            # Fallback if "code_reviewer" is not defined
            self.llm_model_name = get_model_for_task("general_purpose_llm") # Or another capable model like "code_generation"
            if not self.llm_model_name:
                print("Warning: No model configured for 'code_reviewer' or 'general_purpose_llm'. Review quality may be affected. Consider using a general code-aware model.")
                # As a last resort, one might hardcode a known capable model name if absolutely necessary,
                # but relying on get_model_for_task and config is preferred.
                # self.llm_model_name = "phi3:latest" # Example if ollama has this by default
                pass


    async def review_code(
        self,
        code_to_review: str,
        original_requirements: str,
        related_tests: Optional[str] = None,
        attempt_number: int = 1,
        code_diff: Optional[str] = None # New parameter
    ) -> Dict[str, Any]:
        """
        Reviews the given code against original requirements and related tests.

        Args:
            code_to_review: The source code string to be reviewed.
            original_requirements: A string describing the original requirements for the code.
            related_tests: An optional string containing related test cases or descriptions.
            attempt_number: The attempt number for this review cycle.

        Returns:
            A dictionary containing the review status, comments, and suggestions.
            In case of errors during review (e.g., LLM failure, JSON parsing issues),
            it returns a dictionary with status "error" and relevant comments.
        """
        if not code_to_review:
            return {
                "status": "error",
                "comments": "No code provided for review.",
                "suggestions": ""
            }
        if not original_requirements:
            return {
                "status": "error",
                "comments": "Original requirements were not provided for the review.",
                "suggestions": ""
            }

        tests_for_prompt = related_tests if related_tests and related_tests.strip() else "No specific tests provided for review context."
        code_diff_for_prompt = code_diff if code_diff and code_diff.strip() else "No diff provided. Full code is under review."

        prompt = REVIEW_CODE_PROMPT_TEMPLATE.format(
            code_to_review=code_to_review,
            code_diff=code_diff_for_prompt,
            original_requirements=original_requirements,
            related_tests=tests_for_prompt
        )
        
        # Optional: Use attempt_number in a print statement for clarity during execution
        # Limiting length of requirements in print for brevity
        print(f"ReviewerAgent: Reviewing code for '{original_requirements[:70].replace('\n', ' ')}...' (Attempt #{attempt_number})")

        llm_response_str = "" # Initialize for error reporting
        try:
            llm_response_str = await invoke_ollama_model_async(
                prompt,
                model_name=self.llm_model_name,
                temperature=0.2
            )

            if not llm_response_str or not llm_response_str.strip():
                return {
                    "status": "error",
                    "comments": "LLM returned an empty response.",
                    "suggestions": ""
                }

            # Clean and parse the response
            cleaned_response_str = llm_response_str.strip()
            if cleaned_response_str.startswith("```json"):
                cleaned_response_str = cleaned_response_str[len("```json"):].strip()
                if cleaned_response_str.endswith("```"):
                    cleaned_response_str = cleaned_response_str[:-len("```")].strip()
            elif cleaned_response_str.startswith("```"):
                cleaned_response_str = cleaned_response_str[len("```"):].strip()
                if cleaned_response_str.endswith("```"):
                    cleaned_response_str = cleaned_response_str[:-len("```")].strip()
            
            review_data = json.loads(cleaned_response_str)
            if not review_data:
                return {
                    "status": "error",
                    "comments": "Parsed review data is empty",
                    "suggestions": ""
                }

            # Validate required keys and data types
            if not isinstance(review_data, dict) or "status" not in review_data or "comments" not in review_data:
                error_comment = "LLM response JSON is missing required keys ('status', 'comments') or is not a dictionary. Raw response: " + cleaned_response_str
                return {
                    "status": "error",
                    "comments": error_comment,
                    "suggestions": ""
                }
            
            # Ensure suggestions key exists and has a valid value
            if "suggestions" not in review_data or review_data["suggestions"] is None:
                review_data["suggestions"] = ""
            
            # Validate status value
            valid_statuses = ["approved", "requires_changes", "rejected"]
            if not review_data.get("status") or review_data["status"] not in valid_statuses:
                error_comment = f"LLM response JSON has an invalid 'status' value: {review_data.get('status')}. Expected one of {valid_statuses}. Raw response: " + cleaned_response_str
                return {
                    "status": "error",
                    "comments": error_comment,
                    "suggestions": review_data.get("suggestions") or "" # Ensure suggestions is never None
                }

            # Ensure all dictionary values are not None before returning
            review_data["status"] = review_data["status"] or "error"
            review_data["comments"] = review_data["comments"] or "No comments provided"
            review_data["suggestions"] = review_data["suggestions"] or ""

            return review_data

        except json.JSONDecodeError as e:
            response_preview = llm_response_str[:500] if llm_response_str else "(empty response)"
            error_msg = f"Failed to parse LLM response as JSON. Error: {e}. Raw response snippet: '{response_preview}...'"
            return {
                "status": "error",
                "comments": error_msg,
                "suggestions": ""
            }
        except Exception as e:
            # Catch any other unexpected errors during LLM call or processing
            response_preview = llm_response_str[:500] if llm_response_str else "(empty response)"
            return {
                "status": "error",
                "comments": f"An unexpected error occurred during code review: {e}. Raw LLM response snippet: '{response_preview}...'",
                "suggestions": ""
            }

def review_reflection_suggestion(suggestion: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    (Placeholder) Reviews a single reflection-generated improvement suggestion.
    This function needs to be fully implemented to provide qualitative review.
    """
    print(f"INFO: Placeholder review_reflection_suggestion called for suggestion ID: {suggestion.get('suggestion_id', 'N/A')}")
    # For now, let's assume the review is positive but indicates it's a placeholder
    return {
        "review_looks_good": True, # Default to True for placeholder
        "qualitative_review": "Placeholder review: Suggestion logged. Actual review logic not yet implemented.",
        "confidence_score": 0.5, # Neutral confidence
        "suggested_modifications": ""
    }

if __name__ == '__main__':
    # Example Usage (requires Ollama server running with a suitable model)
    async def main():
        # Ensure you have a model configured for "code_reviewer" or "general_purpose_llm"
        # For testing, you might explicitly pass a known model if config isn't set up:
        # reviewer = ReviewerAgent(llm_model_name="your_local_ollama_code_model:latest") 
        reviewer = ReviewerAgent() 

        # Test Case 1: Code that needs changes
        code1 = """
def add(a, b):
    # This function adds two numbers
    return a + b
"""
        reqs1 = "Create a Python function called 'add' that takes two numbers and returns their sum. It should also handle string inputs by trying to convert them to numbers if they represent valid numbers."
        tests1 = """
Test 1: add(5, 10) should return 15
Test 2: add("5", "10") should return 15
Test 3: add(-1, 1) should return 0
Test 4: add("abc", "10") should ideally raise an error or return a specific value indicating failure.
"""
        sample_diff_for_code1 = """--- a/example.py
+++ b/example.py

 def add(a, b):
     # This function adds two numbers
-    return a + b
+    return int(a) + int(b) # Attempt to handle strings
"""
        print("\n--- Reviewing Code 1 (Needs Changes) ---")
        review1 = await reviewer.review_code(code1, reqs1, tests1, attempt_number=1, code_diff=sample_diff_for_code1)
        print(json.dumps(review1, indent=2))

        # Test Case 2: Good code
        code2 = """
def multiply(x: int, y: int) -> int:
    \"\"\"Multiplies two integers and returns the result.
    Handles positive and negative integers.
    \"\"\"
    return x * y
"""
        reqs2 = "Create a Python function 'multiply' that takes two integers, x and y, and returns their product. Include type hints and a docstring. Ensure it works for positive and negative integers."
        tests2 = "Test: multiply(3, 4) == 12; multiply(-2, 5) == -10; multiply(0, 100) == 0"
        print("\n--- Reviewing Code 2 (Good Code) ---")
        review2 = await reviewer.review_code(code2, reqs2, tests2, attempt_number=1)
        print(json.dumps(review2, indent=2))

        # Test Case 3: Flawed code (e.g. NameError)
        code3 = "def divide(a,b): return a/c # obvious error: c is not defined"
        reqs3 = "Function to divide number a by number b."
        print("\n--- Reviewing Code 3 (Flawed Code) ---")
        review3 = await reviewer.review_code(code3, reqs3, attempt_number=1) # No tests
        print(json.dumps(review3, indent=2))
        
        # Test Case 4: Empty code
        print("\n--- Reviewing Code 4 (Empty Code) ---")
        review4 = await reviewer.review_code("", reqs1, attempt_number=1) # Using reqs1 for consistency
        print(json.dumps(review4, indent=2))
        
        # Test Case 5: No requirements
        print("\n--- Reviewing Code 5 (No Requirements) ---")
        review5 = await reviewer.review_code(code1, "", attempt_number=1)
        print(json.dumps(review5, indent=2))

    import asyncio
    asyncio.run(main())

# To make this module's contents (like ReviewerAgent) easily importable
__all__ = ['ReviewerAgent', 'REVIEW_CODE_PROMPT_TEMPLATE']
