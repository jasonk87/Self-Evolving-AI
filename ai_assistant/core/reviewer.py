# Self-Evolving-Agent-feat-chat-history-context/ai_assistant/core/reviewer.py
import json
from typing import Optional, Dict, Any

from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task

REVIEW_CODE_PROMPT_TEMPLATE = """
You are a **PRAGMATIC** AI code reviewer. Your goal is to ensure code is SAFE, FUNCTIONAL, and BETTER than before. You should catch bugs and security issues, but **avoid** blocking progress for minor style preferences.

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

**Review Criteria (Be strictly "nitpicky"):**
0.  **Focus of Review**:
    *   If a `Code Diff` is provided and is not empty, focus primarily on the *changes*, but also verify they integrate correctly with the surrounding code.
    *   If the `Code Diff` is empty or represents a new file, review the entire `Code to Review`.

1.  **Functional Correctness & 100% Accuracy**:
    *   Does the code meet ALL requirements?
    *   Are there ANY logical errors, off-by-one errors, or unhandled edge cases?
    *   Does it handle invalid inputs gracefully?

2.  **Imports & Dependencies**:
    *   Are all imports actually used? Report any unused imports as "requires_changes".
    *   Are imports missing?
    *   Are imports sorted and grouped correctly (standard library, third-party, local)?
    *   Are there circular dependency risks?

3.  **Code Quality, Style & Formatting**:
    *   Is the code sloppy? Are there duplicates?
    *   Is it PEP8 compliant (indentation, variable naming, spacing)?
    *   Are variable names descriptive (e.g., `user_id` instead of `x`)?
    *   Are there docstrings for functions/classes?
    *   Are type hints used where appropriate?

4.  **Safety & Self-Modification**:
    *   **CRITICAL**: If this code modifies the AI system itself (self-modification), is it safe?
    *   Are there risks of infinite loops, data loss, or breaking core functionality?
    *   Are file operations (read/write) safe and error-handled?

5.  **Alignment with Tests**:
    *   Will the code pass the provided tests?
    *   Are existing tests sufficient?

6.  **Preservation of Functionality (CRITICAL)**:
    *   **REJECT** any code that replaces real logic (API calls, database queries) with "mocks", "stubs", "hardcoded data", or "simulations" unless the requirements EXPLICITLY ask for a mock/simulation.
    *   **REJECT** if the code deletes core logic instead of fixing it.
    *   **REJECT** if the code implementation is lazily simplified (e.g. returning `True` blindly).

**Actionable Feedback**:
*   **Prioritize SAFETY and FUNCTIONALITY.**
*   If the code is SAFE, FIXES THE ISSUE, and is REASONABLY CLEAN, you should **APPROVE** it.
*   Do NOT block critical self-correction for minor style nitpicks (like import ordering, docstring phrasing, or variable naming preferences).
*   Use "requires_changes" ONLY for:
    *   Functional bugs.
    *   Security risks.
    *   Syntax errors.
    *   Missing critical requirements.
*   If there are minor style suggestions, use "approved" and list them in the comments as "suggestions for future cleanup" rather than rejecting the change.
*   Your goal is to IMPROVE the system, not to paralyzed it with perfectionism.

**Output Structure:**
You *MUST* respond with a single JSON object.
The JSON object must contain the following keys:
-   `"status"`: String - One of "approved", "requires_changes", or "rejected".
    -   "approved": Code is flawless.
    -   "requires_changes": Code works but has issues (unused imports, sloppy formatting, edge cases, partial requirements).
    -   "rejected": Code is fundamentally flawed, dangerous, or completely misses the goal.
-   `"comments"`: String - Detailed summary of findings.
-   `"suggestions"`: String (Optional) - **Crucial**: Provide exact instructions on how to fix the code.

**Example JSON Output for "requires_changes" (Nitpicking):**
```json
{{
  "status": "requires_changes",
  "comments": "Functionality is correct, but code quality is lacking. 1. Unused import 'sys'. 2. Variable 'x' is unclear. 3. Missing type hint for return value. 4. No docstring provided.",
  "suggestions": "1. Remove 'import sys'. 2. Rename 'x' to 'input_filepath'. 3. Add '-> bool' return type hint. 4. Add docstring explaining the function."
}}
```

Now, please review the provided code with extreme attention to detail.
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
        requirements_preview = original_requirements[:70].replace('\n', ' ')
        print(f"ReviewerAgent: Reviewing code for '{requirements_preview}...' (Attempt #{attempt_number})")

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

            # Clean and parse the response with regex to handle CoT blocks
            import re
            cleaned_response_str = llm_response_str.strip()

            # 1. Robustly remove <think>...</think> blocks and stray tags
            # Remove matched pairs first
            cleaned_response_str = re.sub(r'<think>.*?</think>', '', cleaned_response_str, flags=re.DOTALL)
            # Remove stray closing tags (common if context is truncated or model is chatty)
            cleaned_response_str = cleaned_response_str.replace('</think>', '')
            cleaned_response_str = cleaned_response_str.strip()
            
            review_data = None

            # 2. Strategy A: Markdown Code Block Extraction (Priority)
            # We explicitly ask for ```json in the prompt, so this should be the first place we look.
            try:
                if "```json" in cleaned_response_str:
                    temp_clean = cleaned_response_str.split("```json")[1]
                    if "```" in temp_clean:
                        temp_clean = temp_clean.split("```")[0]
                    review_data = json.loads(temp_clean.strip())
                elif "```" in cleaned_response_str:
                    # Fallback for generic code blocks
                    content_parts = cleaned_response_str.split("```")
                    # Should probably look for the one that looks like JSON or check the second part
                    if len(content_parts) >= 2:
                        potential_json_block = content_parts[1]
                        try:
                            review_data = json.loads(potential_json_block.strip())
                        except json.JSONDecodeError:
                            pass
            except (json.JSONDecodeError, IndexError):
                pass

            # 3. Strategy B: Naive JSON Block Searching (Fallback)
            # Only do this if Strategy A failed.
            if not review_data and "{" in cleaned_response_str and "}" in cleaned_response_str:
                # Find the *first* { and *last* }
                # Note: This is risky if the intro text contains {}, e.g. "I checked the {code}."
                # To mitigte, we could look for the LAST { if the first one fails? 
                # For now, keep simple but strict.
                potential_json = cleaned_response_str[cleaned_response_str.find("{"):cleaned_response_str.rfind("}") + 1]
                try:
                    review_data = json.loads(potential_json)
                except json.JSONDecodeError:
                    # If this fails, maybe there are multiple JSON-like objects. 
                    # Try to find the *last* complete JSON object if possible, or just fail.
                    pass

            if not review_data:
                return {
                    "status": "error",
                    "comments": f"Failed to parse review data from LLM response. Raw snippet: {cleaned_response_str[:100]}...",
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

    async def evaluate_auto_approval_request(
        self,
        request_type: str,
        description: str,
        request_data: Any
    ) -> Dict[str, Any]:
        """
        Evaluates a pending request for autonomous approval.
        
        Args:
            request_type: The type of request (e.g., 'suggestion', 'architect_proposal').
            description: The human-readable description.
            request_data: The full data payload of the request.
            
        Returns:
            Dict with keys:
            - status: "approved" or "rejected"
            - reason: Explanation
            - safety_score: 0-10
            - optimization_score: 0-10
        """
        prompt = f"""
        You are the 'Gatekeeper' AI. Your job is to deeply evaluate whether a pending autonomous action should be allowed to proceed without human intervention.
        
        **Request Type**: {request_type}
        **Description**: {description}
        **Data Payload**:
        ```json
        {json.dumps(request_data, default=str)[:2000]} 
        ```
        (Payload truncated to 2000 chars)

        **Evaluation Matrix**:
        Please grade the request on the following dimensions (0-10):

        1.  **Safety (Critical)**: Risk of breaking the system, data loss, or infinite loops.
            *   10 = Perfectly Safe / No Risk.
            *   0 = High Danger / System Critical Risk.
        
        2.  **Necessity & Impact**: Does this actually improve the system? Is it needed?
            *   10 = Critical improvement / solving a known bug.
            *   0 = Useless / Change for the sake of change.
        
        3.  **Redundancy (Inverse Score)**: Do we already have this functionality?
            *   10 = Completely Novel / New capability.
            *   0 = Exact Duplicate of existing feature (Bad).
        
        4.  **Efficiency/Proportionality**: Is the solution 'Overkill'? 
            *   10 = Elegant, simple, proportional solution.
            *   0 = Bloated, Rube Goldberg machine, or using a sledgehammer to crack a nut.

        **Decision Logic**:
        - **REJECT** if Safety < 8.
        - **REJECT** if Necessity < 5 (Why do it?).
        - **REJECT** if Redundancy < 4 (It's a duplicate).
        - **REJECT** if Efficiency < 5 (It's messy or overkill).
        - **APPROVE** only if it passes all gates and seemingly benefits the user.

        **Output JSON**:
        {{
            "decision": "APPROVED" or "REJECTED",
            "reason": "Detailed reasoning, referencing specific scores...",
            "safety_score": 8,
            "necessity_score": 7,
            "redundancy_score": 9,
            "efficiency_score": 8
        }}
        """
        
        print(f"ReviewerAgent: Evaluating auto-approval for '{description}'...")
        
        try:
            response_str = await invoke_ollama_model_async(
                prompt,
                model_name=self.llm_model_name, # Use same model or specific one
                temperature=0.3
            )
            
            # Basic parsing of JSON from Markdown
            if "```json" in response_str:
                response_str = response_str.split("```json")[1].split("```")[0]
            elif "```" in response_str:
                response_str = response_str.split("```")[1].split("```")[0]
                
            result = json.loads(response_str.strip())
            return {
                "status": "approved" if result.get("decision") == "APPROVED" else "rejected",
                "reason": result.get("reason", "No reason provided"),
                "safety_score": result.get("safety_score", 0),
                "optimization_score": result.get("necessity_score", 0) # Mapping Necessity to legacy 'optimization_score' field for logging transparency, or just adding new keys if needed
            }
        except Exception as e:
            print(f"ReviewerAgent: Error during auto-approval evaluation: {e}")
            return {
                "status": "rejected",
                "reason": f"Evaluation failed due to error: {e}",
                "safety_score": 0,
                "optimization_score": 0
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
