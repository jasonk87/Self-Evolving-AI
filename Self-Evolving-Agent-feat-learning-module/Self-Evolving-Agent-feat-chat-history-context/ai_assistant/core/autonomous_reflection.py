"""
This module implements the AI assistant's self-reflection and autonomous
improvement capabilities. It analyzes past operational logs to identify
recurring failure patterns or areas for enhancement.

Based on these identified patterns, it generates specific improvement suggestions.
Crucially, each suggestion is then evaluated by an LLM to assign quantitative
scores for its potential Impact, associated Risk, and estimated Effort (each on
a 1-5 scale).

These scores are embedded into the suggestion objects. The module then provides
functionality to select a high-priority suggestion for potential autonomous action,
considering these scores alongside the suggestion's action type and details.
This allows the assistant to make informed decisions about which self-improvement
tasks to undertake.
"""
import json 
from typing import List, Dict, Any, Optional
import re # Added for robust JSON extraction
import logging # Added for explicit logger usage
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model 
from ai_assistant.core.reflection import global_reflection_log, ReflectionLogEntry 
from ..memory.event_logger import log_event # Adjusted for consistency, though original might work if core is in path
from ai_assistant.config import get_model_for_task, is_debug_mode # Removed review_reflection_suggestion import
from ai_assistant.learning.evolution import apply_code_modification # Added import
from datetime import datetime, timezone, timedelta 

logger = logging.getLogger(__name__) # Ensure logger is defined for the module

# Define a threshold for what constitutes "enough" entries for analysis
DEFAULT_MIN_ENTRIES_FOR_ANALYSIS = 5 
DEFAULT_MAX_ENTRIES_TO_FETCH = 50    

IDENTIFY_FAILURE_PATTERNS_PROMPT_TEMPLATE = """
You are an AI assistant analyzing a summary of your own past operational reflection logs. Your task is to identify recurring failure patterns, problematic tools or goals, and other insights that could lead to self-improvement.

Here is the summary of recent reflection log entries:
---
{reflection_log_summary}
---

Based on this summary, identify and list any significant patterns or specific issues. Focus on:
1.  **Frequently Failing Tools**: Are there any tools that appear in multiple failed or partially successful plans? Note the tool name and the errors associated with it if possible.
2.  **Common Error Types**: Are there specific error messages or error types (e.g., TypeError, ValueError, ToolNotFoundError) that recur, perhaps with certain tools or types of goals?
3.  **Problematic Goal Types**: Do certain kinds of goals (e.g., goals involving complex calculations, specific external interactions) frequently lead to failures or partial successes?
4.  **Retries & Reliability**: Are there tools or goals that often succeed only after retries? This might indicate unreliability or sensitivity.
5.  **Other Notable Observations**: Any other patterns or anomalies you observe.
6.  **Effectiveness of Past Self-Modifications**: Review any entries explicitly marked as 'SELF-MODIFICATION ATTEMPT' in the log summary. Note whether these modifications were successful (e.g., passed tests, were committed) and if they appear to have resolved prior issues or inadvertently introduced new ones. Include observations about the efficacy of these attempts in your findings if significant patterns emerge (e.g., if a type of self-modification often fails its tests).

Please provide your findings as a JSON object containing a single key "identified_patterns", which is a list of observation objects. Each observation object should detail the pattern and provide brief evidence or examples from the log summary (e.g., including "pattern_type", "details", and "related_entries" as keys).

Example JSON Output Format:
{{
  "identified_patterns": [
    {{
      "pattern_type": "FREQUENTLY_FAILING_TOOL",
      "tool_name": "tool_B",
      "details": "Tool 'tool_B' appeared in 2 failure entries (Entry 2, Entry 5) with errors like 'TypeError' and 'ConnectionTimeout'.",
      "related_entries": ["Entry 2", "Entry 5"]
    }}
  ]
}}

If no significant patterns are found, return an empty list for "identified_patterns".
Focus on clear, data-driven observations based *only* on the provided log summary. Respond ONLY with the JSON object.
"""

GENERATE_IMPROVEMENT_SUGGESTIONS_PROMPT_TEMPLATE = """
You are an AI assistant tasked with generating self-improvement suggestions based on an analysis of your operational patterns. You have been provided with a JSON list of identified issues and patterns from your reflection logs. You also have a list of your currently available tools.

Identified Patterns (JSON list):
---
{identified_patterns_json_list_str}
---

Available Tools (JSON - Name: Description):
---
{available_tools_json_str}
---

Based on the "Identified Patterns" and your "Available Tools":
Generate a list of specific, actionable improvement suggestions. For each suggestion, indicate which pattern(s) it addresses.
Suggestions could include (but are not limited to):
- Modifying the code of an existing tool (e.g., adding error handling, improving input validation, enhancing functionality).
- Adjusting a tool's description for clarity if it seems to be misunderstood or misused.
- Deprecating a problematic tool if a better alternative exists or can be easily created.
- Suggesting the creation of a new tool if a clear gap in capabilities is identified.
- Modifying internal prompts or planning strategies for certain types of goals.
- Alerting a human developer to a complex issue that requires manual intervention.

If `action_type` involves specific parameters (e.g., for `UPDATE_TOOL_DESCRIPTION`, the `tool_name` and `new_description`), include these in a nested 'action_details' object.

Please provide your suggestions as a JSON object containing a single key "improvement_suggestions", which is a list of suggestion objects. Each suggestion object should have:
- "suggestion_id": A unique identifier for the suggestion (e.g., "SUG_001").
- "suggestion_text": The detailed description of the improvement.
- "addresses_patterns": A list of identifiers or descriptions of the patterns it addresses (e.g., references to `pattern_type` and `tool_name` from the input patterns).
- "priority": A suggested priority (e.g., "High", "Medium", "Low") based on perceived impact and urgency.
- "action_type": A proposed type of action (e.g., "MODIFY_TOOL_CODE", "CREATE_NEW_TOOL", "UPDATE_TOOL_DESCRIPTION", "CHANGE_PLANNING_LOGIC", "MANUAL_REVIEW_NEEDED").
- "action_details": (Conditionally present based on action_type) A nested JSON object containing specific parameters needed for the action.
    - For "UPDATE_TOOL_DESCRIPTION": {{"tool_name": "tool_to_update", "new_description": "The new description."}}
    - For "CREATE_NEW_TOOL": {{"tool_description_prompt": "A concise description of the new tool's functionality, suitable for a code generation model.", "suggested_tool_name": "suggested_python_function_name_for_tool"}}
    - For "MODIFY_TOOL_CODE": {{
        "module_path": "path.to.your.module", 
        "function_name": "function_to_modify", 
        "suggested_code_change": "def function_to_modify(param1, param2):\n    # New, complete function code here\n    return result",
        "original_code_snippet": "(Optional) Few lines of the original code for context, if available and relevant for your suggestion.",
        "suggested_change_description": "Detailed textual description of what was changed and why, suitable for a commit message body."
      }}
      (Instruction to LLM: For MODIFY_TOOL_CODE, 'module_path', 'function_name', and 'suggested_code_change' (the new complete function source code) are mandatory. 'original_code_snippet' is optional. 'suggested_change_description' is for the commit message.)

Example JSON Output Format:
{{
  "improvement_suggestions": [
    {{
      "suggestion_id": "SUG_001",
      "suggestion_text": "Add robust input validation to the 'tool_B' function to handle potential TypeErrors.",
      "addresses_patterns": ["FREQUENTLY_FAILING_TOOL: tool_B"],
      "priority": "High",
      "action_type": "MODIFY_TOOL_CODE",
      "action_details": {{
        "module_path": "ai_assistant.custom_tools.tool_utils",
        "function_name": "tool_B",
        "suggested_code_change": "def tool_B(param1):\n    try:\n        # Improved logic with validation\n        num = int(param1)\n        if num == 0:\n            return 'Error: Division by zero not allowed.'\n        return 100 / num\n    except ValueError:\n        return 'Error: Invalid input, expected an integer.'\n    except Exception as e:\n        return f'An unexpected error occurred: {{str(e)}}'",
        "original_code_snippet": "def tool_B(param1):\n    return 100 / param1 # Original potentially unsafe code",
        "suggested_change_description": "Implemented try-except block to handle ValueError for non-integer inputs and check for zero division. This addresses recurrent TypeErrors and potential ZeroDivisionErrors observed in logs."
      }}
    }},
    {{
      "suggestion_id": "SUG_002",
      "suggestion_text": "Clarify the description of 'tool_C' to mention it only accepts positive integers.",
      "addresses_patterns": ["MISUNDERSTOOD_TOOL_USAGE: tool_C"],
      "priority": "Medium",
      "action_type": "UPDATE_TOOL_DESCRIPTION",
      "action_details": {{
        "tool_name": "tool_C",
        "new_description": "This tool performs action X and only accepts positive integers as input."
      }}
    }},
    {{
      "suggestion_id": "SUG_003",
      "suggestion_text": "Identified a recurring need for calculating differences between dates. Suggest creating a new tool for this.",
      "addresses_patterns": ["Problematic Goal Type: Date Calculations", "User query re: date math"],
      "priority": "High",
      "action_type": "CREATE_NEW_TOOL",
      "action_details": {{
        "tool_description_prompt": "A Python function that takes two date strings (e.g., 'YYYY-MM-DD') as input and returns the difference between them in days as an integer. It should handle basic date parsing errors.",
        "suggested_tool_name": "calculate_date_difference"
      }}
    }}
  ]
}}

If no actionable suggestions can be derived from the patterns, return an empty list for "improvement_suggestions".
Focus on practical and impactful suggestions. Respond ONLY with the JSON object.
"""

LLM_REVIEW_IMPROVEMENT_SUGGESTION_PROMPT_TEMPLATE = """
You are an AI assistant acting as a meta-reviewer. Your task is to critically evaluate an *internally generated improvement suggestion* for the AI system itself.

**Improvement Suggestion to Review:**
- Suggestion ID: {suggestion_id}
- Suggestion Text: {suggestion_text}
- Addresses Patterns: {addresses_patterns}
- Priority (Original): {priority}
- Proposed Action Type: {action_type}
- Proposed Action Details (JSON):
  ```json
  {action_details_json_str}
  ```
- Initial Scores: Impact={impact_score}, Risk={risk_score}, Effort={effort_score}

**Review Criteria:**
1.  **Clarity & Actionability**: Is the suggestion clear, specific, and actionable?
2.  **Relevance**: Does the suggestion directly address the identified patterns?
3.  **Appropriateness of Action**: Is the proposed `action_type` and `action_details` suitable for the suggestion?
    - For `MODIFY_TOOL_CODE`: Are `module_path`, `function_name`, and `suggested_code_change` (the complete new function code) present and plausible? Is `suggested_change_description` adequate for a commit message?
    - For `CREATE_NEW_TOOL`: Is `tool_description_prompt` clear enough for a code generation LLM? Is `suggested_tool_name` Pythonic?
    - For `UPDATE_TOOL_DESCRIPTION`: Are `tool_name` and `new_description` present and sensible?
4.  **Potential Impact vs. Risk/Effort**: Considering the initial scores (Impact, Risk, Effort), does this seem like a worthwhile improvement to pursue?
5.  **Overall Soundness**: Does the suggestion make sense? Are there any obvious flaws or better alternatives?

**Output Structure:**
You *MUST* respond with a single JSON object. Do not include any other text or explanations before or after the JSON object.
The JSON object must contain the following keys:
-   `"review_looks_good"`: Boolean - `true` if the suggestion is generally sound and worth considering for action, `false` otherwise.
-   `"qualitative_review"`: String - A concise textual summary of your review, highlighting strengths and weaknesses.
-   `"confidence_score"`: Float (0.0 to 1.0) - Your confidence that this suggestion, if implemented as proposed, will lead to a net positive outcome.
-   `"suggested_modifications_to_proposal"`: String (Optional) - If the suggestion is promising but could be improved (e.g., clearer action details, different action type), describe the modifications here. If none, use an empty string or omit.

Now, please review the provided improvement suggestion.
"""

EVALUATE_IMPROVEMENT_SUGGESTION_PROMPT_TEMPLATE = """
You are an AI assistant evaluating a proposed improvement suggestion for a software system. Your task is to assess the suggestion based on Impact, Risk, and Effort, each on a scale of 1 to 5.

**Suggestion Details:**
- Suggestion: {suggestion_text}
- Action Type: {suggestion_action_type}
- Action Details (JSON): {suggestion_action_details_json_str}

**Evaluation Criteria:**

1.  **Impact Score (1-5):** How significant is the potential positive effect if this suggestion is implemented successfully?
    - 1: Very Low (Minimal or negligible improvement)
    - 2: Low (Slight improvement, noticeable but not major)
    - 3: Medium (Moderate improvement, clearly beneficial)
    - 4: High (Significant improvement, substantial benefits)
    - 5: Very High (Transformative improvement, game-changing)

2.  **Risk Score (1-5):** What is the potential for negative consequences, or how difficult would it be if the implementation fails or introduces new problems?
    - 1: Very Low (Minimal chance of issues, easy to revert)
    - 2: Low (Slight chance of minor issues, manageable)
    - 3: Medium (Moderate chance of noticeable issues, requires effort to fix)
    - 4: High (Significant chance of major issues, difficult to resolve)
    - 5: Very High (Almost certain to cause critical problems, very hard to recover)

3.  **Effort Score (1-5):** How much work or resources (time, complexity, dependencies) are estimated to be required to implement this suggestion?
    - 1: Very Low (Trivial change, can be done in minutes/hours)
    - 2: Low (Minor change, a few hours to a day)
    - 3: Medium (Moderate change, a few days of work)
    - 4: High (Significant change, a week or more, complex)
    - 5: Very High (Major undertaking, weeks/months, many dependencies)

Based on your assessment of the suggestion against these criteria, provide your evaluation *only* as a JSON object with the following three keys: "impact_score", "risk_score", and "effort_score". The values for these keys must be integers between 1 and 5.

Example JSON Output Format:
{{
  "impact_score": 4,
  "risk_score": 2,
  "effort_score": 3
}}

Respond ONLY with the JSON object.
"""


def get_reflection_log_summary_for_analysis(
    max_entries: int = DEFAULT_MAX_ENTRIES_TO_FETCH,
    min_entries_for_analysis: int = DEFAULT_MIN_ENTRIES_FOR_ANALYSIS
) -> Optional[str]:
    """
    Retrieves recent reflection log entries and formats them into a summary string
    suitable for LLM analysis to identify patterns or suggest improvements.

    Args:
        max_entries: The maximum number of recent log entries to fetch.
        min_entries_for_analysis: Minimum entries required to proceed with analysis.

    Returns:
        A formatted string summarizing relevant log entries, or None if
        there are not enough entries for meaningful analysis.
    """
    entries: List[ReflectionLogEntry] = global_reflection_log.get_entries(limit=max_entries)

    if len(entries) < min_entries_for_analysis:
        print(f"Info: Not enough reflection log entries ({len(entries)}) for analysis. Minimum required: {min_entries_for_analysis}.")
        return None

    formatted_summary_parts: List[str] = ["Recent Reflection Log Summary for Analysis:\n"]
    relevant_entry_count = 0

    for i, entry in enumerate(entries): 
        entry_details = []
        entry_details.append(f"Entry {relevant_entry_count + 1} (Timestamp: {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')})")
        entry_details.append(f"  Goal: {entry.goal_description}")
        entry_details.append(f"  Status: {entry.status}")

        if entry.error_type or entry.error_message:
            entry_details.append(f"  Error: {entry.error_type} - {entry.error_message}")
        
        if entry.notes:
            entry_details.append(f"  Notes: {entry.notes}")

        if entry.plan:
            plan_steps_summary = []
            for step_idx, step in enumerate(entry.plan):
                tool_name = step.get('tool_name', 'N/A')
                args_preview = str(step.get('args', 'N/A'))[:50] 
                step_result_preview = ""
                if entry.execution_results and step_idx < len(entry.execution_results):
                    res = entry.execution_results[step_idx]
                    if isinstance(res, Exception):
                        step_result_preview = f" -> Failed: {type(res).__name__}"
                plan_steps_summary.append(f"    Step {step_idx + 1}: Tool: {tool_name}, Args: {args_preview}{step_result_preview}")
            
            if plan_steps_summary:
                entry_details.append("  Plan:")
                entry_details.extend(plan_steps_summary)

        # Add self-modification details if applicable
        if entry.is_self_modification_attempt:
            entry_details.append("  --- SELF-MODIFICATION ATTEMPT ---")
            if entry.source_suggestion_id:
                entry_details.append(f"    Source Suggestion ID: {entry.source_suggestion_id}")
            if entry.modification_type:
                entry_details.append(f"    Modification Type: {entry.modification_type}")
            
            test_outcome_str = "N/A"
            if entry.post_modification_test_passed is True:
                test_outcome_str = "PASSED"
            elif entry.post_modification_test_passed is False:
                test_outcome_str = "FAILED"
            entry_details.append(f"    Test Outcome: {test_outcome_str}")

            if entry.post_modification_test_details and isinstance(entry.post_modification_test_details, dict):
                test_notes = entry.post_modification_test_details.get('notes', '')
                entry_details.append(f"    Test Notes: {test_notes[:100]}{'...' if len(test_notes) > 100 else ''}")
            
            commit_status_str = "N/A"
            if entry.commit_info and isinstance(entry.commit_info, dict):
                commit_success = entry.commit_info.get('status') # Assuming 'status' key from previous structure
                commit_msg_snippet = str(entry.commit_info.get('message', ''))[:50] # 'message' key for commit message
                commit_err_snippet = str(entry.commit_info.get('error', ''))[:50]

                if commit_success is True:
                    commit_status_str = f"Committed (Msg: {commit_msg_snippet}{'...' if len(commit_msg_snippet) == 50 else ''})"
                elif commit_success is False:
                    commit_status_str = f"Commit FAILED ({commit_err_snippet}{'...' if len(commit_err_snippet) == 50 else ''})"
                else: # Status is None or not present
                    commit_status_str = f"Commit status unknown (Info: {commit_msg_snippet}{'...' if len(commit_msg_snippet) == 50 else ''})"
            entry_details.append(f"    Commit Status: {commit_status_str}")
            entry_details.append("  ---------------------------------")
        
        formatted_summary_parts.append("\n".join(entry_details))
        relevant_entry_count += 1
    
    if relevant_entry_count == 0 :
        return "No relevant reflection log entries found for analysis based on current criteria."

    return "\n\n".join(formatted_summary_parts)

def _invoke_pattern_identification_llm(log_summary_str: str, llm_model_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("reflection")
    prompt = IDENTIFY_FAILURE_PATTERNS_PROMPT_TEMPLATE.format(reflection_log_summary=log_summary_str)
    llm_response_str = invoke_ollama_model(prompt, model_name=model_to_use)

    if not llm_response_str:
        logger.warning(f"Received no response from LLM ({model_to_use}) for pattern identification.")
        return None
    
    # More robust JSON extraction for objects
    json_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", llm_response_str, re.DOTALL)
    if json_match:
        cleaned_response = json_match.group(1).strip()
    else:
        # Fallback to find first '{' and last '}'
        first_brace = llm_response_str.find('{')
        last_brace = llm_response_str.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned_response = llm_response_str[first_brace : last_brace+1].strip()
        else:
            cleaned_response = llm_response_str.strip() # Use as is if no clear JSON object found
    
    try:
        data = json.loads(cleaned_response)
        if not isinstance(data, dict):
            logger.warning(f"LLM response for pattern identification was not a dictionary. Response: {cleaned_response}")
            return None
        if "identified_patterns" not in data or not isinstance(data["identified_patterns"], list):
            logger.warning(f"LLM response for pattern identification missing 'identified_patterns' list or incorrect type. Response: {cleaned_response}")
            return None
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from pattern identification LLM: {e}. Raw response snippet:\n---\n{llm_response_str[:1000]}...\n---")
        return None

def _invoke_suggestion_generation_llm(identified_patterns_json_list_str: str, available_tools_json_str: str, llm_model_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("reflection")
    prompt = GENERATE_IMPROVEMENT_SUGGESTIONS_PROMPT_TEMPLATE.format(
        identified_patterns_json_list_str=identified_patterns_json_list_str,
        available_tools_json_str=available_tools_json_str
    )
    llm_response_str = invoke_ollama_model(prompt, model_name=model_to_use)

    if not llm_response_str:
        logger.warning(f"Received no response from LLM ({model_to_use}) for suggestion generation.")
        return None
        
    # More robust JSON extraction for objects
    json_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", llm_response_str, re.DOTALL)
    if json_match:
        cleaned_response = json_match.group(1).strip()
    else:
        first_brace = llm_response_str.find('{')
        last_brace = llm_response_str.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned_response = llm_response_str[first_brace : last_brace+1].strip()
        else:
            cleaned_response = llm_response_str.strip()

    try:
        data = json.loads(cleaned_response)
        if not isinstance(data, dict):
            logger.warning(f"LLM response for suggestion generation was not a dictionary. Response: {cleaned_response}")
            return None
        if "improvement_suggestions" not in data or not isinstance(data["improvement_suggestions"], list):
            logger.warning(f"LLM response for suggestion generation missing 'improvement_suggestions' list or incorrect type. Response: {cleaned_response}")
            return None
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from suggestion generation LLM: {e}. Raw response snippet:\n---\n{llm_response_str[:1000]}...\n---")
        return None

def _invoke_suggestion_scoring_llm(suggestion: Dict[str, Any], llm_model_name: Optional[str] = None) -> Optional[Dict[str, int]]:
    """
    Invokes an LLM to score an improvement suggestion based on impact, risk, and effort.

    Args:
        suggestion: A dictionary containing the suggestion details.
                    Expected keys: "suggestion_text", "action_type", and optionally "action_details".
        llm_model_name: The name of the Ollama model to use.

    Returns:
        A dictionary containing "impact_score", "risk_score", and "effort_score" as integers,
        or None if the LLM call, JSON parsing, or validation fails.
    """
    suggestion_text = suggestion.get("suggestion_text", "")
    action_type = suggestion.get("action_type", "")
    action_details = suggestion.get("action_details")

    try:
        if action_details is None:
            action_details_json_str = "{}"
        else:
            action_details_json_str = json.dumps(action_details)
    except TypeError as e:
        logger.warning(f"Could not serialize action_details to JSON for suggestion scoring. Error: {e}. Details: {action_details}")
        action_details_json_str = "{}"

    prompt = EVALUATE_IMPROVEMENT_SUGGESTION_PROMPT_TEMPLATE.format(
        suggestion_text=suggestion_text,
        suggestion_action_type=action_type,
        suggestion_action_details_json_str=action_details_json_str
    )

    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("reflection")
    llm_response_str = invoke_ollama_model(prompt, model_name=model_to_use)

    if not llm_response_str:
        logger.warning(f"Received no response from LLM for suggestion scoring (model: {model_to_use}). Suggestion ID: {suggestion.get('suggestion_id', 'N/A')}")
        return None

    # More robust JSON extraction for objects
    json_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", llm_response_str, re.DOTALL)
    if json_match:
        cleaned_response = json_match.group(1).strip()
    else:
        first_brace = llm_response_str.find('{')
        last_brace = llm_response_str.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned_response = llm_response_str[first_brace : last_brace+1].strip()
        else:
            cleaned_response = llm_response_str.strip()

    try:
        data = json.loads(cleaned_response)
        if not isinstance(data, dict):
            logger.warning(f"LLM response for suggestion scoring was not a dictionary. Response: {cleaned_response}")
            return None

        required_keys = ["impact_score", "risk_score", "effort_score"]
        for key in required_keys:
            if key not in data:
                logger.warning(f"LLM response for suggestion scoring missing key '{key}'. Response: {cleaned_response}")
                return None
            if not isinstance(data[key], int):
                logger.warning(f"LLM response for suggestion scoring key '{key}' is not an integer. Value: {data[key]}. Response: {cleaned_response}")
                return None
        
        # Optional: could add range validation here (1-5) if strictly needed by callers immediately.
        # For now, type and presence are the primary validation.

        return {
            "impact_score": data["impact_score"],
            "risk_score": data["risk_score"],
            "effort_score": data["effort_score"],
        }
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from suggestion scoring LLM: {e}. Raw response snippet:\n---\n{llm_response_str[:1000]}...\n---")
        return None
    except Exception as e: # Catch any other unexpected errors during validation
        logger.error(f"An unexpected error occurred during suggestion scoring validation: {e}. Response: {cleaned_response}")
        return None

def _invoke_suggestion_review_llm(suggestion: Dict[str, Any], llm_model_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Invokes an LLM to review an improvement suggestion.
    Helper for review_reflection_suggestion.
    """
    suggestion_id = suggestion.get("suggestion_id", "N/A")
    suggestion_text = suggestion.get("suggestion_text", "")
    addresses_patterns = suggestion.get("addresses_patterns", [])
    priority = suggestion.get("priority", "N/A")
    action_type = suggestion.get("action_type", "N/A")
    action_details = suggestion.get("action_details", {})
    impact_score = suggestion.get("impact_score", "N/A")
    risk_score = suggestion.get("risk_score", "N/A")
    effort_score = suggestion.get("effort_score", "N/A")

    try:
        action_details_json_str = json.dumps(action_details)
    except TypeError:
        action_details_json_str = str(action_details) # Fallback

    prompt = LLM_REVIEW_IMPROVEMENT_SUGGESTION_PROMPT_TEMPLATE.format(
        suggestion_id=suggestion_id,
        suggestion_text=suggestion_text,
        addresses_patterns=str(addresses_patterns), # Convert list to string for prompt
        priority=priority,
        action_type=action_type,
        action_details_json_str=action_details_json_str,
        impact_score=impact_score,
        risk_score=risk_score,
        effort_score=effort_score
    )

    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("reflection") # Or a new "suggestion_review" task type
    llm_response_str = invoke_ollama_model(prompt, model_name=model_to_use)

    if not llm_response_str:
        logger.warning(f"Received no response from LLM for suggestion review (model: {model_to_use}). Suggestion ID: {suggestion_id}")
        return None

    # More robust JSON extraction for objects
    json_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", llm_response_str, re.DOTALL)
    if json_match:
        cleaned_response = json_match.group(1).strip()
    else:
        first_brace = llm_response_str.find('{')
        last_brace = llm_response_str.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned_response = llm_response_str[first_brace : last_brace+1].strip()
        else:
            cleaned_response = llm_response_str.strip()
            
    try:
        data = json.loads(cleaned_response)
        if not isinstance(data, dict) or \
           "review_looks_good" not in data or not isinstance(data["review_looks_good"], bool) or \
           "qualitative_review" not in data or not isinstance(data["qualitative_review"], str) or \
           "confidence_score" not in data or not isinstance(data["confidence_score"], float):
            logger.warning(f"LLM response for suggestion review has missing/invalid keys. Response: {cleaned_response}")
            return None
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from suggestion review LLM: {e}. Raw response snippet:\n---\n{llm_response_str[:1000]}...\n---")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during suggestion review LLM response processing: {e}. Raw response snippet:\n---\n{llm_response_str[:1000]}...\n---")
        return None

def run_self_reflection_cycle(
    available_tools: Dict[str, str], 
    llm_model_name: Optional[str] = None, # Changed default to None
    max_log_entries: int = DEFAULT_MAX_ENTRIES_TO_FETCH, 
    min_entries_for_analysis: int = DEFAULT_MIN_ENTRIES_FOR_ANALYSIS 
) -> Optional[List[Dict[str, Any]]]:
    """
    Runs a full self-reflection cycle:
    1. Retrieves and summarizes reflection log entries.
    2. Invokes an LLM to identify patterns from the summary.
    3. Invokes an LLM to generate improvement suggestions based on patterns and available tools.
    4. Invokes an LLM to score each generated suggestion for impact, risk, and effort.
    5. Embeds these scores (impact_score, risk_score, effort_score) into each suggestion dictionary.
       If scoring fails for a suggestion, error values (-1) are assigned for its scores.

    Args:
        available_tools: A dictionary of available tools (name: description) for the AI.
        llm_model_name: The name of the Ollama model to use for all LLM invocations.
        max_log_entries: Max number of recent log entries to fetch for analysis.
        min_entries_for_analysis: Min log entries required to proceed with analysis.

    Returns:
        A list of suggestion dictionaries, each augmented with "impact_score",
        "risk_score", and "effort_score" keys. Returns None if a critical step
        (like log summary or pattern identification) fails, or an empty list
        if no suggestions are generated.
    """
    logger.info("\n--- Starting Self-Reflection Cycle ---") # Changed to logger
    log_event(
        event_type="AUTONOMOUS_REFLECTION_CYCLE_STARTED",
        description="Self-reflection cycle initiated.",
        source="autonomous_reflection.run_self_reflection_cycle",
        metadata={"max_log_entries": max_log_entries, "min_entries_for_analysis": min_entries_for_analysis}
    )
    
    log_summary = get_reflection_log_summary_for_analysis(
        max_entries=max_log_entries, 
        min_entries_for_analysis=min_entries_for_analysis
    )
    if not log_summary:
        logger.info("Self-Reflection Cycle: Aborted due to insufficient log data or no relevant entries found.") # Changed to logger
        log_event(
            event_type="AUTONOMOUS_REFLECTION_CYCLE_ABORTED",
            description="Self-reflection cycle aborted: Insufficient log data.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"reason": "Insufficient log data from get_reflection_log_summary_for_analysis"}
        )
        return None

    if is_debug_mode():
        logger.debug(f"Reflection log summary for analysis: {log_summary}") # Changed to logger

    logger.info("Self-Reflection Cycle: Identifying failure patterns from log summary...") # Changed to logger
    # Pass llm_model_name to _invoke_pattern_identification_llm if it's provided, otherwise it will use the default from config
    patterns_data = _invoke_pattern_identification_llm(log_summary, llm_model_name=llm_model_name) 
    
    if not patterns_data: 
        logger.warning("Self-Reflection Cycle: Could not identify any significant patterns (LLM call failed or invalid format).") # Changed to logger
        log_event(
            event_type="AUTONOMOUS_REFLECTION_PATTERN_ID_FAILED",
            description="Pattern identification failed or returned invalid format from LLM.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"llm_model_name": llm_model_name or get_model_for_task("reflection")} # Log actual model used
        )
        return None
        
    identified_patterns_list = patterns_data.get("identified_patterns")
    if identified_patterns_list is None: 
        logger.warning("Self-Reflection Cycle: 'identified_patterns' key missing in LLM response for patterns.") # Changed to logger
        log_event(
            event_type="AUTONOMOUS_REFLECTION_PATTERN_ID_ERROR",
            description="'identified_patterns' key missing in LLM response.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"llm_model_name": llm_model_name or get_model_for_task("reflection"), "response_preview": str(patterns_data)[:200]}
        )
        return None
    
    log_event(
        event_type="AUTONOMOUS_REFLECTION_PATTERNS_IDENTIFIED",
        description=f"Pattern identification complete. Found {len(identified_patterns_list)} pattern(s).",
        source="autonomous_reflection.run_self_reflection_cycle",
        metadata={"num_patterns": len(identified_patterns_list), "patterns_preview": identified_patterns_list[:3], "model_used": llm_model_name or get_model_for_task("reflection")}
    )

    if not identified_patterns_list: 
        logger.info("Self-Reflection Cycle: No specific patterns were identified by the LLM.") # Changed to logger
        pass 

    logger.info(f"Self-Reflection Cycle: Identified {len(identified_patterns_list)} pattern(s). Generating improvement suggestions...") # Changed to logger

    try:
        patterns_json_list_str = json.dumps(identified_patterns_list, indent=2)
        available_tools_json_str = json.dumps(available_tools, indent=2)
    except TypeError as e:
        logger.error(f"Error serializing patterns or tools to JSON for suggestion generation: {e}") # Changed to logger
        log_event(
            event_type="AUTONOMOUS_REFLECTION_SERIALIZATION_ERROR",
            description="Error serializing patterns or tools to JSON.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"error": str(e)}
        )
        return None

    suggestions_data = _invoke_suggestion_generation_llm(
        patterns_json_list_str, 
        available_tools_json_str, 
        llm_model_name=llm_model_name # Pass through llm_model_name
    )

    if not suggestions_data: 
        logger.warning("Self-Reflection Cycle: Could not generate improvement suggestions (LLM call failed or invalid format).") # Changed to logger
        log_event(
            event_type="AUTONOMOUS_REFLECTION_SUGGESTION_GEN_FAILED",
            description="Suggestion generation failed or returned invalid format from LLM.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"llm_model_name": llm_model_name or get_model_for_task("reflection"), "num_patterns_input": len(identified_patterns_list)}
        )
        return None
        
    final_suggestions = suggestions_data.get("improvement_suggestions")
    if final_suggestions is None: 
        logger.warning("Self-Reflection Cycle: 'improvement_suggestions' key missing in LLM response for suggestions.") # Changed to logger
        log_event(
            event_type="AUTONOMOUS_REFLECTION_SUGGESTION_GEN_ERROR",
            description="'improvement_suggestions' key missing in LLM response.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"llm_model_name": llm_model_name or get_model_for_task("reflection"), "response_preview": str(suggestions_data)[:200]}
        )
        return None
    
    log_event(
        event_type="AUTONOMOUS_REFLECTION_SUGGESTIONS_GENERATED",
        description=f"Suggestion generation complete. Generated {len(final_suggestions)} suggestion(s).",
        source="autonomous_reflection.run_self_reflection_cycle",
        metadata={"num_suggestions": len(final_suggestions), "suggestions_preview": final_suggestions[:3], "model_used": llm_model_name or get_model_for_task("reflection")} 
    )
    
    if not final_suggestions: 
        logger.info("Self-Reflection Cycle: No improvement suggestions were generated by the LLM.") # Changed to logger
        # No suggestions to score, so we can pass, and the function will return the empty list or None.
    else:
        logger.info(f"Self-Reflection Cycle: Generated {len(final_suggestions)} improvement suggestion(s). Scoring them now...") # Changed to logger
        for suggestion in final_suggestions:
            if not isinstance(suggestion, dict): # Should not happen with current generation logic
                logger.warning(f"Skipping scoring for an invalid suggestion item: {suggestion}") # Changed to logger
                continue

            scores = _invoke_suggestion_scoring_llm(suggestion, llm_model_name=llm_model_name) # Pass through llm_model_name
            if scores:
                suggestion["impact_score"] = scores.get("impact_score")
                suggestion["risk_score"] = scores.get("risk_score")
                suggestion["effort_score"] = scores.get("effort_score")
                # print(f"DEBUG: Scored suggestion {suggestion.get('suggestion_id', 'N/A')}: Impact={scores.get('impact_score')}, Risk={scores.get('risk_score')}, Effort={scores.get('effort_score')}")
            else:
                logger.warning(f"Failed to score suggestion ID: {suggestion.get('suggestion_id', 'Unknown ID')}. Assigning default error scores (-1).") # Changed to logger
                suggestion["impact_score"] = -1
                suggestion["risk_score"] = -1
                suggestion["effort_score"] = -1
        logger.info(f"Self-Reflection Cycle: Scoring completed for {len(final_suggestions)} suggestions.") # Changed to logger

        # --- Add Review Step ---
        logger.info(f"Self-Reflection Cycle: Reviewing {len(final_suggestions)} scored suggestion(s)...") # Changed to logger
        for suggestion in final_suggestions:
            if not isinstance(suggestion, dict): continue

            review_data = _invoke_suggestion_review_llm(suggestion, llm_model_name=llm_model_name)
            if review_data:
                suggestion["review_looks_good"] = review_data.get("review_looks_good")
                suggestion["qualitative_review"] = review_data.get("qualitative_review")
                suggestion["reviewer_confidence"] = review_data.get("confidence_score") # Use a distinct key
                suggestion["reviewer_modifications"] = review_data.get("suggested_modifications_to_proposal") # Match key from prompt
            else:
                logger.warning(f"Failed to review suggestion ID: {suggestion.get('suggestion_id', 'Unknown ID')}. Assigning default review error values.")
                suggestion["review_looks_good"] = False # Default to False if review fails
                suggestion["qualitative_review"] = "Review process failed."
                suggestion["reviewer_confidence"] = 0.0
                suggestion["reviewer_modifications"] = ""
        logger.info(f"Self-Reflection Cycle: Reviewing completed for {len(final_suggestions)} suggestions.") # Changed to logger


    logger.info("--- Self-Reflection Cycle Finished ---") # Changed to logger
    log_event(
        event_type="AUTONOMOUS_REFLECTION_CYCLE_COMPLETED",
        description=f"Self-reflection cycle finished. Produced {len(final_suggestions) if final_suggestions is not None else 0} suggestions, attempted scoring and review.",
        source="autonomous_reflection.run_self_reflection_cycle",
        metadata={"num_suggestions_produced": len(final_suggestions) if final_suggestions is not None else 0}
    )
    return final_suggestions

def select_suggestion_for_autonomous_action(
    suggestions: List[Dict[str, Any]],
    supported_action_types: Optional[List[str]] = None,
    # Add tool_system if needed by future action types, not currently used directly by MODIFY_TOOL_CODE handler
) -> Optional[Dict[str, Any]]:
    """
    Selects a suitable suggestion for an initial phase of autonomous action based on
    calculated priority and validation of action details.

    The selection process involves:
    1. Filtering suggestions by `supported_action_types`.
    2. Filtering out suggestions that lack valid `impact_score`, `risk_score`, and
       `effort_score` (e.g., if scoring failed and they have -1 values).
    3. Calculating a `_priority_score` for each valid suggestion using the formula:
       `impact_score - risk_score - (effort_score * 0.5)`.
       This prioritizes suggestions that are impactful, lower-risk, and require
       reasonably low effort.
    4. Sorting suggestions by this `_priority_score` in descending order.
    5. Iterating through the sorted list and selecting the first suggestion that
       passes validation of its `action_details` (e.g., required fields for
       the specific `action_type`).

    Args:
        suggestions: A list of suggestion dictionaries, typically from
                     `run_self_reflection_cycle`, which should include
                     `impact_score`, `risk_score`, `effort_score`, and `review_looks_good`.
        supported_action_types: A list of `action_type` strings to consider.
                                 Defaults to include "UPDATE_TOOL_DESCRIPTION", 
                                 "CREATE_NEW_TOOL", and "MODIFY_TOOL_CODE".

    Returns:
        The selected suggestion dictionary if a suitable and valid one is found
        and successfully actioned (or attempt was made for MODIFY_TOOL_CODE),
        otherwise None.
    """
    if supported_action_types is None: 
        supported_action_types = ["UPDATE_TOOL_DESCRIPTION", "CREATE_NEW_TOOL", "MODIFY_TOOL_CODE"]

    if not suggestions:
        logger.debug("No suggestions provided to select_suggestion_for_autonomous_action.")
        return None

    # 1. Filter by Supported Action Types
    actionable_suggestions = [s for s in suggestions if s.get("action_type") in supported_action_types]
    if not actionable_suggestions:
        logger.debug(f"No suggestions match supported action types: {supported_action_types}")
        return None
    
    # 2. Filter out Suggestions with Failed Scoring
    valid_scored_suggestions = []
    for s in actionable_suggestions:
        impact = s.get("impact_score")
        risk = s.get("risk_score")
        effort = s.get("effort_score")
        
        if isinstance(impact, int) and impact != -1 and \
           isinstance(risk, int) and risk != -1 and \
           isinstance(effort, int) and effort != -1:
            valid_scored_suggestions.append(s)
        else:
            logger.debug(f"Suggestion {s.get('suggestion_id', 'N/A')} filtered out due to missing/failed I/R/E scores (Impact: {impact}, Risk: {risk}, Effort: {effort}).")
            
    if not valid_scored_suggestions:
        logger.debug("No suggestions remaining after filtering for valid I/R/E scores.")
        return None

    # 3. Filter by Review Outcome
    reviewed_and_approved_suggestions = []
    for s in valid_scored_suggestions:
        # Consider suggestion if review_looks_good is True and confidence is above a threshold
        # Example threshold: 0.6
        if s.get("review_looks_good") is True and s.get("reviewer_confidence", 0.0) >= 0.6:
            reviewed_and_approved_suggestions.append(s)
        else:
            logger.debug(f"Suggestion {s.get('suggestion_id', 'N/A')} filtered out due to review_looks_good ({s.get('review_looks_good')}) or low confidence ({s.get('reviewer_confidence', 0.0)}). Review: '{s.get('qualitative_review', 'N/A')}'")

    if not reviewed_and_approved_suggestions:
        logger.debug("No suggestions remaining after filtering by review outcome and confidence.")
        return None

    logger.debug(f"{len(reviewed_and_approved_suggestions)} suggestions remaining after review filter.")

    # 4. Calculate a Priority Metric
    for s in reviewed_and_approved_suggestions:
        impact_score = s["impact_score"] # Already validated
        risk_score = s["risk_score"]
        effort_score = s["effort_score"]
        
        s["_priority_score"] = impact_score - risk_score - (effort_score * 0.5)
        logger.debug(f"Suggestion {s.get('suggestion_id', 'N/A')} (Action: {s.get('action_type')}) calculated priority_score: {s['_priority_score']} (I:{impact_score}, R:{risk_score}, E:{effort_score}) Reviewer Confidence: {s.get('reviewer_confidence', 'N/A')}")

    # 5. Sort Suggestions
    sorted_suggestions = sorted(reviewed_and_approved_suggestions, key=lambda x: x["_priority_score"], reverse=True)
    
    logger.debug(f"{len(sorted_suggestions)} suggestions sorted by priority_score.")
    if sorted_suggestions:
        logger.debug(f"Top sorted suggestion ID {sorted_suggestions[0].get('suggestion_id', 'N/A')} with score {sorted_suggestions[0]['_priority_score']}")

    # 6. Select the Best Valid Suggestion and attempt action
    for suggestion in sorted_suggestions:
        action_type = suggestion.get("action_type")
        action_details = suggestion.get("action_details") # This is the dict for apply_code_modification

        # Clean up temporary score before returning or further processing
        priority_score_for_log = suggestion.pop("_priority_score", None)

        if action_type == "UPDATE_TOOL_DESCRIPTION":
            if isinstance(action_details, dict) and \
               isinstance(action_details.get("tool_name"), str) and action_details.get("tool_name") and \
               action_details.get("new_description") is not None and isinstance(action_details.get("new_description"), str):
                logger.info(f"Selected suggestion ID {suggestion.get('suggestion_id', 'N/A')} (Update Tool Desc) with priority score {priority_score_for_log}.")
                # Actual update logic would be called here if this function did more than selection.
                # For now, returning the selected suggestion is the "action".
                return suggestion # Actionable suggestion found
        
        elif action_type == "CREATE_NEW_TOOL":
            if isinstance(action_details, dict) and \
               isinstance(action_details.get("tool_description_prompt"), str) and action_details.get("tool_description_prompt"):
                logger.info(f"Selected suggestion ID {suggestion.get('suggestion_id', 'N/A')} (Create New Tool) with priority score {priority_score_for_log}.")
                # Actual tool creation logic would be called here.
                return suggestion # Actionable suggestion found

        elif action_type == "MODIFY_TOOL_CODE":
            if isinstance(action_details, dict) and \
               isinstance(action_details.get("module_path"), str) and action_details.get("module_path") and \
               isinstance(action_details.get("function_name"), str) and action_details.get("function_name") and \
               isinstance(action_details.get("suggested_code_change"), str) and action_details.get("suggested_code_change"):
                
                # Prepare the dictionary for apply_code_modification
                # It expects "module_path", "function_name", "suggested_code_change" directly.
                # The 'action_details' from the suggestion should directly map to this.
                code_mod_params = {
                    "module_path": action_details["module_path"],
                    "function_name": action_details["function_name"],
                    "suggested_code_change": action_details["suggested_code_change"]
                }

                logger.info(f"Attempting to apply code modification for tool '{code_mod_params['function_name']}' "+
                            f"in module '{code_mod_params['module_path']}' based on suggestion "+
                            f"{suggestion.get('suggestion_id', 'N/A')} (Priority: {priority_score_for_log}).")
                
                # apply_code_modification now returns a detailed dictionary
                code_mod_result = apply_code_modification(code_mod_params) 
                
                overall_success_from_apply = code_mod_result['overall_status']
                detailed_message_from_apply = code_mod_result['overall_message']
                
                test_outcome_details = code_mod_result.get('test_outcome')
                test_passed_for_log = test_outcome_details.get('passed') if test_outcome_details else None
                
                commit_outcome_details = code_mod_result.get('commit_outcome')
                commit_info_for_log = None
                if commit_outcome_details:
                    commit_info_for_log = {
                        "message": commit_outcome_details.get("commit_message_generated"),
                        "status": commit_outcome_details.get("status"),
                        "error": commit_outcome_details.get("error_message")
                    }

                modification_details_for_log_dict = {
                    "module": code_mod_params["module_path"],
                    "function": code_mod_params["function_name"],
                    # Using the original suggestion's action_details for the 'why' part
                    "suggested_change_description": suggestion.get("action_details", {}).get("suggested_change_description"),
                    "original_code_snippet": suggestion.get("action_details", {}).get("original_code_snippet"),
                    # Storing the actual code change applied might be too verbose for this log entry,
                    # but could be logged elsewhere or inferred from commit if needed.
                    # "applied_code_change": code_mod_params["suggested_code_change"] 
                }

                # Log the detailed self-modification attempt to ReflectionLog
                global_reflection_log.log_execution(
                    goal_description=f"Self-modification attempt for suggestion {suggestion.get('suggestion_id', 'N/A')}",
                    plan=[{
                        "tool_name": "apply_code_modification", 
                        "args": [code_mod_params], # Be mindful of logging sensitive data if code_mod_params contains raw code
                        "status": "attempted"
                    }],
                    execution_results=[code_mod_result], # Log the entire detailed result from apply_code_modification
                    overall_success=overall_success_from_apply,
                    notes=detailed_message_from_apply,
                    is_self_modification_attempt=True,
                    source_suggestion_id=suggestion.get('suggestion_id'),
                    modification_type="MODIFY_TOOL_CODE",
                    modification_details=modification_details_for_log_dict,
                    post_modification_test_passed=test_passed_for_log,
                    post_modification_test_details=test_outcome_details, # Log the full test outcome
                    commit_info=commit_info_for_log
                )
                
                # Update the event log with more details
                logger.info(detailed_message_from_apply) # The overall message from apply_code_modification is now the main log
                log_event(
                    event_type="AUTONOMOUS_ACTION_MODIFY_TOOL_CODE_ATTEMPT",
                    description=detailed_message_from_apply, # Use the detailed message
                    source="autonomous_reflection.select_suggestion_for_autonomous_action",
                    metadata={
                        "suggestion_id": suggestion.get("suggestion_id"),
                        "tool_name": code_mod_params['function_name'],
                        "module_path": code_mod_params['module_path'],
                        "overall_outcome_success": overall_success_from_apply,
                        "edit_status": code_mod_result.get("edit_outcome", {}).get("status"),
                        "test_status": test_passed_for_log,
                        "revert_status": code_mod_result.get("revert_outcome", {}).get("status"),
                        "commit_status": commit_info_for_log.get("status") if commit_info_for_log else None,
                        "priority_score": priority_score_for_log # Keep this for selection context
                    }
                )
                # This suggestion is considered "actioned" regardless of success/failure of the modification itself.
                return suggestion # Return the original suggestion that was actioned.
            else:
                logger.warning(f"Skipping MODIFY_TOOL_CODE suggestion {suggestion.get('suggestion_id', 'N/A')} due to missing/invalid action_details: {action_details}")
        
    logger.debug("No suggestion passed action_details validation or other criteria after sorting by priority_score.")
    return None

if __name__ == '__main__':
    # Setup basic logging for the test run if not already configured by resilience.py or other imports
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    print("--- Testing Reflection Log Analysis Function ---")
    # Mock data for testing select_suggestion_for_autonomous_action
    # Ensure this mock data includes impact_score, risk_score, effort_score, and review_looks_good
    mock_suggestions_for_select_test = [
        { # Valid MODIFY_TOOL_CODE
            "suggestion_id": "MTC001", "action_type": "MODIFY_TOOL_CODE", "priority": "High",
            "impact_score": 4, "risk_score": 1, "effort_score": 2, "review_looks_good": True, # review_looks_good is True
            "action_details": {
                "module_path": "ai_assistant.tools.sample_tool", 
                "function_name": "do_something", 
                "suggested_code_change": "def do_something(new_param):\n  pass"
            }
        },
        { # Valid UPDATE_TOOL_DESCRIPTION
            "suggestion_id": "UTD001", "action_type": "UPDATE_TOOL_DESCRIPTION", "priority": "Medium", 
            "impact_score": 3, "risk_score": 1, "effort_score": 1, "review_looks_good": True,
            "action_details": {"tool_name": "tool_A", "new_description": "New desc for A"}
        },
        { # Invalid MODIFY_TOOL_CODE (missing suggested_code_change)
            "suggestion_id": "MTC002", "action_type": "MODIFY_TOOL_CODE", "priority": "High",
            "impact_score": 4, "risk_score": 2, "effort_score": 2, "review_looks_good": True,
            "action_details": {"module_path": "ai_assistant.tools.another_tool", "function_name": "another_func"}
        },
        { # Valid CREATE_NEW_TOOL
            "suggestion_id": "CNT001", "action_type": "CREATE_NEW_TOOL", "priority": "High",
            "impact_score": 5, "risk_score": 2, "effort_score": 3, "review_looks_good": True,
            "action_details": {"tool_description_prompt": "A tool to do X."}
        },
        { # MODIFY_TOOL_CODE with review_looks_good = False
            "suggestion_id": "MTC003_REJECTED", "action_type": "MODIFY_TOOL_CODE", "priority": "High",
            "impact_score": 5, "risk_score": 1, "effort_score": 1, "review_looks_good": False, 
            "qualitative_review": "Reviewer found potential issues.",
            "action_details": {
                "module_path": "ai_assistant.tools.rejected_tool", 
                "function_name": "rejected_func", 
                "suggested_code_change": "def rejected_func():\n  # risky change\n  pass"
            }
        }
    ]

    from unittest.mock import patch, MagicMock

    # Test 1: Select MODIFY_TOOL_CODE suggestion
    # We need to mock 'apply_code_modification'
    with patch('ai_assistant.learning.evolution.apply_code_modification') as mock_apply_code:
        mock_apply_code.return_value = True # Simulate successful application
        
        # Use a logger that captures messages for assertion
        test_logger = logging.getLogger('ai_assistant.core.autonomous_reflection')
        # If using Python 3.10+, can use assertLogs context manager more easily.
        # For now, simple check of called_once_with for apply_code_modification
        
        selected_mtc = select_suggestion_for_autonomous_action(
            mock_suggestions_for_select_test, 
            supported_action_types=["MODIFY_TOOL_CODE", "UPDATE_TOOL_DESCRIPTION", "CREATE_NEW_TOOL"]
        )
        
        if selected_mtc:
            print(f"Selected suggestion (MTC Test): {selected_mtc.get('suggestion_id')}")
            assert selected_mtc.get('suggestion_id') == "MTC001", f"Expected MTC001, got {selected_mtc.get('suggestion_id')}"
            
            expected_call_params = {
                "module_path": "ai_assistant.tools.sample_tool", 
                "function_name": "do_something", 
                "suggested_code_change": "def do_something(new_param):\n  pass"
            }
            mock_apply_code.assert_called_once_with(expected_call_params)
            # Add log assertion here if capturing logs
        else:
            print("No suggestion selected for MTC test (unexpected).")
            assert False, "Expected MTC001 to be selected and processed."

    # Test 2: MODIFY_TOOL_CODE suggestion fails application
    with patch('ai_assistant.learning.evolution.apply_code_modification') as mock_apply_code_fail:
        mock_apply_code_fail.return_value = False # Simulate failed application
        
        selected_mtc_fail = select_suggestion_for_autonomous_action(
            [mock_suggestions_for_select_test[0]], # Only the MTC001 suggestion
            supported_action_types=["MODIFY_TOOL_CODE"]
        )
        if selected_mtc_fail:
            print(f"Selected suggestion (MTC Fail Test): {selected_mtc_fail.get('suggestion_id')}")
            assert selected_mtc_fail.get('suggestion_id') == "MTC001"
            mock_apply_code_fail.assert_called_once() 
            # Add log assertion here for failure message
        else:
            assert False, "Expected MTC001 to be selected even if application fails, for logging."
            
    # Test 3: Ensure MTC003_REJECTED is not selected due to review_looks_good=False
    with patch('ai_assistant.learning.evolution.apply_code_modification') as mock_apply_code_rejected:
        suggestions_for_rejected_test = [
             mock_suggestions_for_select_test[4], # MTC003_REJECTED (review_looks_good=False)
             mock_suggestions_for_select_test[1]  # UTD001 (review_looks_good=True, lower priority than MTC003 if it were good)
        ]
        selected_rejected = select_suggestion_for_autonomous_action(
            suggestions_for_rejected_test,
            supported_action_types=["MODIFY_TOOL_CODE", "UPDATE_TOOL_DESCRIPTION"]
        )
        if selected_rejected:
            print(f"Selected suggestion (Rejected Test): {selected_rejected.get('suggestion_id')}")
            assert selected_rejected.get('suggestion_id') == "UTD001", "Expected UTD001 to be selected, not the rejected MTC003."
            mock_apply_code_rejected.assert_not_called() # MTC003 should not have been attempted
        else:
            assert False, "Expected UTD001 to be selected in the rejected test."
            
    print("\n--- select_suggestion_for_autonomous_action tests complete ---")
