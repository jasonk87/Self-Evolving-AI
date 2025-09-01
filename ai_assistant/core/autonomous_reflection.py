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
import re
import logging
import asyncio # Ensure asyncio is imported for __main__
from unittest.mock import patch, AsyncMock, MagicMock # Ensure these are imported for __main__

from ai_assistant.llm_interface.ollama_client import invoke_ollama_model 
from ai_assistant.core.reflection import global_reflection_log, ReflectionLogEntry 
from ..memory.event_logger import log_event, get_recent_events
from ai_assistant.config import get_model_for_task, is_debug_mode
from ai_assistant.learning.evolution import apply_code_modification, apply_internal_prompt_adjustment
from datetime import datetime, timezone, timedelta 
from .notification_manager import NotificationManager
# Import suggestion_manager_module at the top level
import ai_assistant.core.suggestion_manager as suggestion_manager_module


logger = logging.getLogger(__name__)

DEFAULT_MIN_ENTRIES_FOR_ANALYSIS = 5 
DEFAULT_MAX_ENTRIES_TO_FETCH = 50
DEFAULT_MAX_CONVERSATION_EVENTS_FOR_REFLECTION = 20

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

LLM_SELF_CRITIQUE_AND_PATTERNS_PROMPT_TEMPLATE = """You are a Self-Critical AI Assistant reviewing your recent operational and conversational history to identify areas for improvement.
Your goal is to find patterns of suboptimal performance, common mistakes you (the AI) might be making, or missed opportunities.

**Input Data:**
The following summary contains two parts:
1.  Reflection Log Summary: Structured logs of your internal operations, tool usage, successes, and failures.
2.  Recent Conversation Snippets: Raw exchanges between you (AI) and the user.

```text
{combined_interaction_summary}
```

**Your Task:**
Analyze the provided input data and identify:

1.  **AI Performance Gaps / Suboptimal Behavior:**
    *   **Tool Usage:** Instances where a different tool might have been more appropriate, or a tool was used incorrectly (e.g., wrong arguments, misunderstanding its purpose based on user follow-up).
    *   **Response Quality:** Instances where your (AI) responses were unclear, verbose, incomplete, or led to user confusion or requests for clarification.
    *   **Missed Opportunities:** Situations where you could have offered to use a tool, provide more information, or proactively assist but didn't.
    *   **Decision-Making Patterns:** Any recurring patterns in your decision-making (e.g., always defaulting to a specific tool even when alternatives exist, consistently struggling with a certain type of ambiguity).

2.  **Common AI Mistakes / Misunderstandings:**
    *   Patterns where you repeatedly misunderstand a specific type of user query or intent.
    *   Consistent errors when interpreting the output of a particular tool.
    *   Recurring incorrect assumptions you might be making.

**Output Format:**
Please provide your analysis as a single JSON object. This object must contain one key: `"performance_observations"`.
The value of `"performance_observations"` should be a list of JSON objects, where each object represents a distinct observation and includes the following keys:
-   `"observation_id"`: A unique identifier you generate for this observation (e.g., "OBS_001").
-   `"type"`: A string categorizing the observation. Examples: "SUBOPTIMAL_TOOL_CHOICE", "UNCLEAR_AI_RESPONSE", "MISSED_TOOL_OPPORTUNITY", "RECURRING_MISUNDERSTANDING_OF_INTENT", "INEFFICIENT_PLANNING_PATTERN", "COMMON_TOOL_USAGE_ERROR". (Be descriptive).
-   `"description"`: A detailed explanation of the observed issue or pattern.
-   `"evidence"`: A list of short, relevant text snippets (1-3 snippets, max 50-70 characters each) from the input summary (either from reflection logs or conversation snippets) that support your observation. Clearly indicate if evidence is from 'Log Entry X' or 'User/AI Exchange Y'.
-   `"context_keywords"`: A list of 3-5 keywords that best describe the context or topic of the interaction(s) where this observation occurred (e.g., ["date calculation", "file search", "api error"]).
-   `"ai_confidence_in_observation"`: A float (0.0 to 1.0) indicating your confidence that this is a genuine area for improvement.
-   `"potential_impact_if_addressed"`: A string describing the likely positive impact if this observation is addressed (e.g., "Improved user satisfaction", "More efficient task completion", "Reduced errors with Tool X").

Example of a `performance_observation` object:
{{
  "observation_id": "OBS_001",
  "type": "UNCLEAR_AI_RESPONSE",
  "description": "AI's explanation for why a tool failed was overly technical and did not directly answer the user's follow-up question about alternatives.",
  "evidence": ["AI Exchange 3: AI: The foobar_utility returned exit code 255.", "AI Exchange 3: User: Okay, but what can I do instead?"],
  "context_keywords": ["foobar_utility", "tool failure", "alternatives"],
  "ai_confidence_in_observation": 0.85,
  "potential_impact_if_addressed": "Improved user understanding and reduced follow-up questions."
}}

If you find no significant performance gaps or common mistakes, return an empty list for `"performance_observations"`.
Respond ONLY with the JSON object. Do not include any other text or explanations.
"""

GENERATE_IMPROVEMENT_SUGGESTIONS_PROMPT_TEMPLATE = """
You are an AI assistant tasked with generating self-improvement suggestions. You have been provided with:
1.  A JSON list of **Identified Structured Patterns** from your reflection logs (tool failures, etc.).
2.  A JSON list of **AI Performance Observations** from a self-critique of recent interactions (suboptimal tool use, unclear responses, missed opportunities, common AI mistakes).
3.  A list of your **Currently Available Tools**.

**Input Data:**

1.  Identified Structured Patterns (JSON list):
    ```json
    {identified_patterns_json_list_str}
    ```

2.  AI Performance Observations (JSON list):
    ```json
    {performance_observations_json_list_str}
    ```

3.  Available Tools (JSON - Name: Description):
    ```json
    {available_tools_json_str}
    ```

**Your Task:**
Based on *all* the provided input data (Structured Patterns, Performance Observations, and Available Tools), generate a list of specific, actionable improvement suggestions.
- Each suggestion should clearly state which pattern(s) or observation(s) it addresses.
- Prioritize suggestions that seem most impactful or address recurring issues.

**Types of Suggestions & `action_type` values:**

*   **Related to Tools (from Structured Patterns or Performance Observations):**
    *   `MODIFY_TOOL_CODE`: If an existing tool needs code changes (bug fix, enhancement).
        *   `action_details`: `{{ "module_path": "path.to.module", "function_name": "func_name", "suggested_code_change": "full new code for function", "original_code_snippet": "(optional)", "suggested_change_description": "commit message body" }}`
    *   `CREATE_NEW_TOOL`: If a new tool is needed.
        *   `action_details`: `{{ "tool_description_prompt": "description for LLM to generate tool", "suggested_tool_name": "python_tool_name" }}`
    *   `UPDATE_TOOL_DESCRIPTION`: If a tool's description needs clarification.
        *   `action_details`: `{{ "tool_name": "tool_to_update", "new_description": "The new description." }}`
    *   `DEPRECATE_TOOL`: If a tool is problematic and should be removed (rare).
        *   `action_details`: `{{ "tool_name": "tool_to_deprecate", "reason": "Why it should be deprecated." }}`

*   **Related to AI Behavior/Performance (Primarily from Performance Observations):**
    *   `ADJUST_INTERNAL_PROMPT`: If an internal AI prompt (e.g., for planning, or for how it generates its own conversational responses) needs adjustment.
        *   `action_details`: `{{ "prompt_area_identifier": "e.g., general_qa_response_prompt, tool_selection_logic_prompt", "suggested_change_summary": "Briefly, what to change. E.g., 'Make explanations more concise for topic X', 'Emphasize Y when Z occurs.'" }}`
    *   `UPDATE_DECISION_HEURISTIC`: If the AI's internal logic/heuristics for making decisions (e.g., when to use a tool, how to interpret user ambiguity) needs an update. This is more abstract.
        *   `action_details`: `{{ "heuristic_identifier": "e.g., tool_selection_for_file_search, user_intent_clarification_threshold", "change_description": "Describe the change. E.g., 'Consider 'find_large_files_tool' before 'basic_search_tool' if query mentions 'large files'.", "reasoning": "Why this change is needed based on observation." }}`
    *   `FLAG_FOR_KNOWLEDGE_GAP`: If the AI identifies a recurring misunderstanding due to missing knowledge it cannot directly fix. This flags it for potential external update or learning.
        *   `action_details`: `{{ "topic": "e.g., Quantum Entanglement Details", "missing_information_summary": "e.g., AI struggles to explain practical applications.", "example_queries": ["query1", "query2"] }}`

*   **General:**
    *   `MANUAL_REVIEW_NEEDED`: If an issue is too complex for an automated suggestion or requires human developer intervention.
        *   `action_details`: `{{ "issue_summary": "Brief summary of the complex issue.", "relevant_data_pointers": ["e.g., Log Entry X", "User/AI Exchange Y"] }}`

**Output JSON Structure:**
Provide your suggestions as a JSON object containing a single key `"improvement_suggestions"`. This key holds a list of suggestion objects. Each suggestion object *must* have:
- `"suggestion_id"`: A unique identifier (e.g., "SUG_001").
- `"suggestion_text"`: Detailed description of the improvement.
- `"addresses_observations_or_patterns"`: A list of strings, referencing `observation_id` from Performance Observations or describing the Structured Pattern addressed (e.g., ["OBS_001", "FREQUENTLY_FAILING_TOOL: some_tool"]).
- `"priority"`: Suggested priority ("High", "Medium", "Low").
- `"action_type"`: One of the `action_type` values listed above.
- `"action_details"`: A nested JSON object with parameters specific to the `action_type` (see examples above).

Example JSON Output:
```json
{{
  "improvement_suggestions": [
    {{
      "suggestion_id": "SUG_001",
      "suggestion_text": "AI responses regarding API error codes are often too technical. Adjust internal prompt for explaining API errors to be more user-friendly.",
      "addresses_observations_or_patterns": ["OBS_005_UnclearApiResponseExplanation"],
      "priority": "Medium",
      "action_type": "ADJUST_INTERNAL_PROMPT",
      "action_details": {{
        "prompt_area_identifier": "api_error_explanation_prompt",
        "suggested_change_summary": "When explaining API errors, provide a brief non-technical summary first, then offer technical details if requested."
      }}
    }},
    // ... other suggestions for tool modifications, new tools, etc.
  ]
}}
```

If no actionable suggestions can be derived, return an empty list for `"improvement_suggestions"`.
Respond ONLY with the JSON object.
"""

_LEGACY_GENERATE_IMPROVEMENT_SUGGESTIONS_PROMPT_TEMPLATE = """
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
    entries: List[ReflectionLogEntry] = global_reflection_log.get_entries(limit=max_entries)

    # formatted_summary_parts will collect reflection log strings
    formatted_summary_parts: List[str] = []
    if len(entries) >= min_entries_for_analysis:
        formatted_summary_parts.append("Recent Reflection Log Summary for Analysis:\n")
        relevant_entry_count = 0
        for i, entry in enumerate(entries):
            # ... (existing formatting logic for reflection entries)
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
                    commit_success = entry.commit_info.get('status')
                    commit_msg_snippet = str(entry.commit_info.get('message', ''))[:50]
                    commit_err_snippet = str(entry.commit_info.get('error', ''))[:50]

                    if commit_success is True:
                        commit_status_str = f"Committed (Msg: {commit_msg_snippet}{'...' if len(commit_msg_snippet) == 50 else ''})"
                    elif commit_success is False:
                        commit_status_str = f"Commit FAILED ({commit_err_snippet}{'...' if len(commit_err_snippet) == 50 else ''})"
                    else:
                        commit_status_str = f"Commit status unknown (Info: {commit_msg_snippet}{'...' if len(commit_msg_snippet) == 50 else ''})"
                entry_details.append(f"    Commit Status: {commit_status_str}")
                entry_details.append("  ---------------------------------")
            
            formatted_summary_parts.append("\n".join(entry_details))
            relevant_entry_count += 1
        if relevant_entry_count == 0: # If loop ran but no entries were actually formatted (e.g. due to some filter if added)
            formatted_summary_parts.append("No reflection log entries met the criteria for this summary section.")
    else:
        formatted_summary_parts.append("Not enough reflection log entries for detailed analysis in this section.")


    # --- Fetch and Format Recent Conversation Events ---
    conversation_summary_parts: List[str] = ["\n\n--- Recent Conversation Snippets (Last up to {DEFAULT_MAX_CONVERSATION_EVENTS_FOR_REFLECTION} User/AI exchanges) ---\n"]
    recent_conv_events = get_recent_events(limit=DEFAULT_MAX_CONVERSATION_EVENTS_FOR_REFLECTION * 2)

    user_ai_pairs = []
    temp_user_event = None
    for event in reversed(recent_conv_events):
        event_type = event.get("event_type", "")
        if event_type == "USER_INPUT_RECEIVED":
            temp_user_event = event
        elif event_type == "AI_RESPONSE_GENERATED" and temp_user_event:
            user_ai_pairs.append({"user": temp_user_event, "ai": event})
            temp_user_event = None
        elif event_type == "AI_TOOL_EXECUTION_RESPONSE" and temp_user_event:
            user_ai_pairs.append({"user": temp_user_event, "ai": event})
            temp_user_event = None

    interaction_count = 0
    for pair in reversed(user_ai_pairs):
        if interaction_count >= DEFAULT_MAX_CONVERSATION_EVENTS_FOR_REFLECTION:
            break
        user_event = pair["user"]
        ai_event = pair["ai"]
        user_ts = datetime.fromisoformat(user_event['timestamp']).strftime('%Y-%m-%d %H:%M') if user_event.get('timestamp') else 'Unknown Time'
        ai_ts = datetime.fromisoformat(ai_event['timestamp']).strftime('%Y-%m-%d %H:%M') if ai_event.get('timestamp') else 'Unknown Time'
        user_desc = user_event.get("description", "N/A")
        ai_desc = ai_event.get("description", "N/A")
        if ai_event.get("event_type") == "AI_TOOL_EXECUTION_RESPONSE":
            ai_meta = ai_event.get("metadata", {})
            ai_desc = f"Tool '{ai_meta.get('tool_name', 'UnknownTool')}' executed. Result preview: {str(ai_desc)[:100]}"
        conversation_summary_parts.append(f"Exchange {interaction_count + 1}:")
        conversation_summary_parts.append(f"  [{user_ts}] User: {user_desc[:200]}{'...' if len(user_desc) > 200 else ''}")
        conversation_summary_parts.append(f"  [{ai_ts}] AI:   {ai_desc[:200]}{'...' if len(ai_desc) > 200 else ''}")
        conversation_summary_parts.append("  ---")
        interaction_count += 1

    if interaction_count == 0:
        conversation_summary_parts.append("No recent user/AI conversation exchanges found in event logs.")

    # --- Combine Summaries ---
    # Only include reflection log summary if it has more than just the header
    final_summary_str = ""
    if len(formatted_summary_parts) > 1: # Has more than just the initial header
        final_summary_str += "\n".join(formatted_summary_parts)

    # Add conversation summary
    final_summary_str += "\n" + "\n".join(conversation_summary_parts)


    if not final_summary_str.strip() or (len(formatted_summary_parts) <=1 and interaction_count == 0) :
        logger.info("No reflection log entries or recent conversation events found for analysis.")
        return None

    return final_summary_str.strip()


def _invoke_pattern_identification_llm(log_summary_str: str, llm_model_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("reflection")
    prompt = IDENTIFY_FAILURE_PATTERNS_PROMPT_TEMPLATE.format(reflection_log_summary=log_summary_str)
    llm_response_str = invoke_ollama_model(prompt, model_name=model_to_use)

    if not llm_response_str:
        logger.warning(f"Received no response from LLM ({model_to_use}) for pattern identification.")
        return None
    
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
            logger.warning(f"LLM response for pattern identification was not a dictionary. Response: {cleaned_response}")
            return None
        if "identified_patterns" not in data or not isinstance(data["identified_patterns"], list):
            logger.warning(f"LLM response for pattern identification missing 'identified_patterns' list or incorrect type. Response: {cleaned_response}")
            return None
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from pattern identification LLM: {e}. Raw response snippet:\n---\n{llm_response_str[:1000]}...\n---")
        return None

def _invoke_suggestion_generation_llm(
    identified_patterns_json_list_str: str,
    performance_observations_json_list_str: str,
    available_tools_json_str: str,
    llm_model_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("reflection")
    prompt = GENERATE_IMPROVEMENT_SUGGESTIONS_PROMPT_TEMPLATE.format(
        identified_patterns_json_list_str=identified_patterns_json_list_str,
        performance_observations_json_list_str=performance_observations_json_list_str,
        available_tools_json_str=available_tools_json_str
    )
    llm_response_str = invoke_ollama_model(prompt, model_name=model_to_use)

    if not llm_response_str:
        logger.warning(f"Received no response from LLM ({model_to_use}) for suggestion generation.")
        return None
        
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
        
        return {
            "impact_score": data["impact_score"],
            "risk_score": data["risk_score"],
            "effort_score": data["effort_score"],
        }
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from suggestion scoring LLM: {e}. Raw response snippet:\n---\n{llm_response_str[:1000]}...\n---")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during suggestion scoring validation: {e}. Response: {cleaned_response}")
        return None

def _invoke_suggestion_review_llm(suggestion: Dict[str, Any], llm_model_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    suggestion_id = suggestion.get("suggestion_id", "N/A")
    suggestion_text = suggestion.get("suggestion_text", "")
    addresses_patterns = suggestion.get("addresses_patterns", []) # This will now be addresses_observations_or_patterns
    priority = suggestion.get("priority", "N/A")
    action_type = suggestion.get("action_type", "N/A")
    action_details = suggestion.get("action_details", {})
    impact_score = suggestion.get("impact_score", "N/A")
    risk_score = suggestion.get("risk_score", "N/A")
    effort_score = suggestion.get("effort_score", "N/A")

    try:
        action_details_json_str = json.dumps(action_details)
    except TypeError:
        action_details_json_str = str(action_details)

    prompt = LLM_REVIEW_IMPROVEMENT_SUGGESTION_PROMPT_TEMPLATE.format(
        suggestion_id=suggestion_id,
        suggestion_text=suggestion_text,
        addresses_patterns=str(suggestion.get("addresses_observations_or_patterns", addresses_patterns)), # Use new key
        priority=priority,
        action_type=action_type,
        action_details_json_str=action_details_json_str,
        impact_score=impact_score,
        risk_score=risk_score,
        effort_score=effort_score
    )

    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("reflection")
    llm_response_str = invoke_ollama_model(prompt, model_name=model_to_use)

    if not llm_response_str:
        logger.warning(f"Received no response from LLM for suggestion review (model: {model_to_use}). Suggestion ID: {suggestion_id}")
        return None

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
    llm_model_name: Optional[str] = None,
    max_log_entries: int = DEFAULT_MAX_ENTRIES_TO_FETCH, 
    min_entries_for_analysis: int = DEFAULT_MIN_ENTRIES_FOR_ANALYSIS,
    notification_manager: Optional[NotificationManager] = None
) -> Optional[List[Dict[str, Any]]]:
    logger.info("\n--- Starting Self-Reflection Cycle ---")
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
        logger.info("Self-Reflection Cycle: Aborted due to insufficient log data (neither reflection entries nor conversation events found).")
        log_event(
            event_type="AUTONOMOUS_REFLECTION_CYCLE_ABORTED",
            description="Self-reflection cycle aborted: Insufficient data from combined summary.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"reason": "Insufficient data from get_reflection_log_summary_for_analysis"}
        )
        return None

    if is_debug_mode():
        logger.debug(f"Combined interaction summary for analysis (first 1000 chars): {log_summary[:1000]}")

    # --- Step 1 (New): Self-Critique and Performance Pattern Identification ---
    logger.info("Self-Reflection Cycle: Performing self-critique and identifying performance patterns...")
    model_for_critique = llm_model_name or get_model_for_task("reflection")
    critique_prompt = LLM_SELF_CRITIQUE_AND_PATTERNS_PROMPT_TEMPLATE.format(combined_interaction_summary=log_summary)

    critique_llm_response_str = invoke_ollama_model(critique_prompt, model_name=model_for_critique)
    performance_observations_list = []

    if critique_llm_response_str:
        critique_json_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", critique_llm_response_str, re.DOTALL)
        critique_cleaned_response = critique_llm_response_str.strip()
        if critique_json_match:
            critique_cleaned_response = critique_json_match.group(1).strip()
        else:
            first_brace = critique_llm_response_str.find('{')
            last_brace = critique_llm_response_str.rfind('}')
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                critique_cleaned_response = critique_llm_response_str[first_brace : last_brace+1].strip()

        try:
            critique_data = json.loads(critique_cleaned_response)
            if isinstance(critique_data, dict) and "performance_observations" in critique_data and isinstance(critique_data["performance_observations"], list):
                performance_observations_list = critique_data["performance_observations"]
                logger.info(f"Self-Reflection Cycle: LLM self-critique identified {len(performance_observations_list)} performance observation(s).")
                log_event(
                    event_type="AUTONOMOUS_SELF_CRITIQUE_RESULTS",
                    description=f"Self-critique process completed, found {len(performance_observations_list)} observations.",
                    source="autonomous_reflection.run_self_reflection_cycle",
                    metadata={"num_observations": len(performance_observations_list), "observations_preview": performance_observations_list[:3], "model_used": model_for_critique}
                )
            else:
                logger.warning(f"Self-Reflection Cycle: LLM self-critique response missing 'performance_observations' list or incorrect type. Response: {critique_cleaned_response}")
        except json.JSONDecodeError as e:
            logger.error(f"Self-Reflection Cycle: Error decoding JSON from LLM self-critique: {e}. Raw response snippet:\n---\n{critique_llm_response_str[:1000]}...\n---")
    else:
        logger.warning(f"Self-Reflection Cycle: Received no response from LLM for self-critique (model: {model_for_critique}).")

    logger.info("Self-Reflection Cycle: Identifying failure patterns from structured reflection log summary (within combined summary)...")
    patterns_data = _invoke_pattern_identification_llm(log_summary, llm_model_name=llm_model_name) 
    
    identified_patterns_list = []
    if patterns_data and patterns_data.get("identified_patterns") is not None:
        identified_patterns_list = patterns_data.get("identified_patterns", [])
        log_event(
            event_type="AUTONOMOUS_REFLECTION_PATTERNS_IDENTIFIED",
            description=f"Structured pattern identification complete. Found {len(identified_patterns_list)} pattern(s).",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"num_patterns": len(identified_patterns_list), "patterns_preview": identified_patterns_list[:3], "model_used": llm_model_name or get_model_for_task("reflection")}
        )
    else:
        logger.warning("Self-Reflection Cycle: Could not identify any significant structured patterns from logs (LLM call failed or invalid format).")
        log_event(
            event_type="AUTONOMOUS_REFLECTION_PATTERN_ID_FAILED",
            description="Structured pattern identification failed or returned invalid format from LLM.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"llm_model_name": llm_model_name or get_model_for_task("reflection")}
        )

    if not identified_patterns_list: 
        logger.info("Self-Reflection Cycle: No specific structured patterns were identified by the LLM from logs.")

    if not identified_patterns_list and not performance_observations_list:
        logger.info("Self-Reflection Cycle: No patterns from logs and no performance observations. No suggestions will be generated.")
        log_event(
            event_type="AUTONOMOUS_REFLECTION_CYCLE_COMPLETED",
            description="Self-reflection cycle finished. No patterns or observations found to generate suggestions.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"num_suggestions_produced": 0}
        )
        return []

    logger.info(f"Self-Reflection Cycle: Identified {len(identified_patterns_list)} structured pattern(s) and {len(performance_observations_list)} performance observation(s). Generating improvement suggestions...")

    try:
        patterns_json_list_str = json.dumps(identified_patterns_list, indent=2)
        performance_observations_json_list_str = json.dumps(performance_observations_list, indent=2)
        available_tools_json_str = json.dumps(available_tools, indent=2)
    except TypeError as e:
        logger.error(f"Error serializing patterns, observations, or tools to JSON for suggestion generation: {e}")
        log_event(
            event_type="AUTONOMOUS_REFLECTION_SERIALIZATION_ERROR",
            description="Error serializing data to JSON for suggestion generation.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"error": str(e)}
        )
        return None

    suggestions_data = _invoke_suggestion_generation_llm(
        identified_patterns_json_list_str=patterns_json_list_str,
        performance_observations_json_list_str=performance_observations_json_list_str,
        available_tools_json_str=available_tools_json_str,
        llm_model_name=llm_model_name
    )

    if not suggestions_data: 
        logger.warning("Self-Reflection Cycle: Could not generate improvement suggestions (LLM call failed or invalid format).")
        log_event(
            event_type="AUTONOMOUS_REFLECTION_SUGGESTION_GEN_FAILED",
            description="Suggestion generation failed or returned invalid format from LLM.",
            source="autonomous_reflection.run_self_reflection_cycle",
            metadata={"llm_model_name": llm_model_name or get_model_for_task("reflection"), "num_patterns_input": len(identified_patterns_list), "num_observations_input": len(performance_observations_list)}
        )
        return None
        
    final_suggestions = suggestions_data.get("improvement_suggestions")
    if final_suggestions is None: 
        logger.warning("Self-Reflection Cycle: 'improvement_suggestions' key missing in LLM response for suggestions.")
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
    
    saved_suggestion_objects = [] # To return
    if not final_suggestions: 
        logger.info("Self-Reflection Cycle: No improvement suggestions were generated by the LLM.")
    else:
        logger.info(f"Self-Reflection Cycle: Generated {len(final_suggestions)} improvement suggestion(s). Scoring, reviewing, and saving them now...")
        saved_count = 0
        for suggestion_data_from_llm in final_suggestions: # Renamed variable for clarity
            if not isinstance(suggestion_data_from_llm, dict):
                logger.warning(f"Skipping processing for an invalid suggestion item: {suggestion_data_from_llm}")
                continue

            # Scoring
            scores = _invoke_suggestion_scoring_llm(suggestion_data_from_llm, llm_model_name=llm_model_name)
            if scores:
                suggestion_data_from_llm["impact_score"] = scores.get("impact_score")
                suggestion_data_from_llm["risk_score"] = scores.get("risk_score")
                suggestion_data_from_llm["effort_score"] = scores.get("effort_score")
            else:
                logger.warning(f"Failed to score suggestion ID: {suggestion_data_from_llm.get('suggestion_id', 'Unknown ID')}. Assigning default error scores (-1).")
                suggestion_data_from_llm["impact_score"] = -1
                suggestion_data_from_llm["risk_score"] = -1
                suggestion_data_from_llm["effort_score"] = -1

            # Reviewing
            review_data = _invoke_suggestion_review_llm(suggestion_data_from_llm, llm_model_name=llm_model_name)
            if review_data:
                suggestion_data_from_llm["review_looks_good"] = review_data.get("review_looks_good")
                suggestion_data_from_llm["qualitative_review"] = review_data.get("qualitative_review")
                suggestion_data_from_llm["reviewer_confidence"] = review_data.get("confidence_score")
                suggestion_data_from_llm["reviewer_modifications"] = review_data.get("suggested_modifications_to_proposal")
            else:
                logger.warning(f"Failed to review suggestion ID: {suggestion_data_from_llm.get('suggestion_id', 'Unknown ID')}. Assigning default review error values.")
                suggestion_data_from_llm["review_looks_good"] = False
                suggestion_data_from_llm["qualitative_review"] = "Review process failed."
                suggestion_data_from_llm["reviewer_confidence"] = 0.0
                suggestion_data_from_llm["reviewer_modifications"] = ""

            # Saving (This is the part that needs to pass action_details)
            try:
                sug_type = suggestion_data_from_llm.get("action_type", "self_improvement_idea")
                sug_desc = suggestion_data_from_llm.get("suggestion_text", "No description provided.")
                sug_source_id = suggestion_data_from_llm.get("suggestion_id") # This is the ID from the LLM's list
                sug_action_details = suggestion_data_from_llm.get("action_details", {})
                # Add other fields from suggestion_data_from_llm to be stored by suggestion_manager if necessary
                # For example, pass the full suggestion_data_from_llm to a field in add_new_suggestion,
                # or expand add_new_suggestion to take all these fields.
                # For now, we focus on getting action_details through.

                # The suggestion_manager.add_new_suggestion should be modified to accept these fields
                # and store them. For now, we're just passing them.
                # A more complete solution would be for add_new_suggestion to take the whole suggestion_data_from_llm
                # or specific fields like priority, scores, review status.
                added_suggestion_to_store = suggestion_manager_module.add_new_suggestion(
                    type=sug_type,
                    description=sug_desc,
                    source_reflection_id=sug_source_id,
                    action_details=sug_action_details, # Passing action_details
                    # Pass other fields from suggestion_data_from_llm if add_new_suggestion supports them
                    # e.g., priority=suggestion_data_from_llm.get("priority"),
                    # extras_to_store=suggestion_data_from_llm # or specific scores/review fields
                    notification_manager=notification_manager
                )
                if added_suggestion_to_store:
                    saved_count += 1
                    saved_suggestion_objects.append(added_suggestion_to_store)
            except Exception as e_add_sug:
                logger.error(f"Error saving suggestion '{suggestion_data_from_llm.get('suggestion_id')}': {e_add_sug}", exc_info=True)

        logger.info(f"Self-Reflection Cycle: Scoring, reviewing, and saving attempt completed for {len(final_suggestions)} suggestions. Successfully saved {saved_count}.")


    logger.info("--- Self-Reflection Cycle Finished ---")
    log_event(
        event_type="AUTONOMOUS_REFLECTION_CYCLE_COMPLETED",
        description=f"Self-reflection cycle finished. Produced {len(final_suggestions) if final_suggestions is not None else 0} suggestions, attempted scoring, review, and saving.",
        source="autonomous_reflection.run_self_reflection_cycle",
        metadata={"num_suggestions_produced": len(final_suggestions) if final_suggestions is not None else 0, "num_suggestions_saved": len(saved_suggestion_objects)}
    )
    return saved_suggestion_objects # Return the list of dicts that were actually saved by suggestion_manager

async def select_suggestion_for_autonomous_action( # Made async
    suggestions: List[Dict[str, Any]],
    supported_action_types: Optional[List[str]] = None,
    notification_manager: Optional[NotificationManager] = None
) -> Optional[Dict[str, Any]]:
    if supported_action_types is None: 
        supported_action_types = [
            "UPDATE_TOOL_DESCRIPTION",
            "CREATE_NEW_TOOL",
            "MODIFY_TOOL_CODE",
            "ADJUST_INTERNAL_PROMPT" # Added new type
        ]

    if not suggestions:
        logger.debug("No suggestions provided to select_suggestion_for_autonomous_action.")
        return None

    actionable_suggestions = [s for s in suggestions if s.get("action_type") in supported_action_types]
    if not actionable_suggestions:
        logger.debug(f"No suggestions match supported action types: {supported_action_types}")
        return None
    
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

    reviewed_and_approved_suggestions = []
    for s in valid_scored_suggestions:
        if s.get("review_looks_good") is True and s.get("reviewer_confidence", 0.0) >= 0.6:
            reviewed_and_approved_suggestions.append(s)
        else:
            logger.debug(f"Suggestion {s.get('suggestion_id', 'N/A')} filtered out due to review_looks_good ({s.get('review_looks_good')}) or low confidence ({s.get('reviewer_confidence', 0.0)}). Review: '{s.get('qualitative_review', 'N/A')}'")

    if not reviewed_and_approved_suggestions:
        logger.debug("No suggestions remaining after filtering by review outcome and confidence.")
        return None

    logger.debug(f"{len(reviewed_and_approved_suggestions)} suggestions remaining after review filter.")

    for s in reviewed_and_approved_suggestions:
        impact_score = s["impact_score"]
        risk_score = s["risk_score"]
        effort_score = s["effort_score"]
        
        s["_priority_score"] = impact_score - risk_score - (effort_score * 0.5)
        logger.debug(f"Suggestion {s.get('suggestion_id', 'N/A')} (Action: {s.get('action_type')}) calculated priority_score: {s['_priority_score']} (I:{impact_score}, R:{risk_score}, E:{effort_score}) Reviewer Confidence: {s.get('reviewer_confidence', 'N/A')}")

    sorted_suggestions = sorted(reviewed_and_approved_suggestions, key=lambda x: x["_priority_score"], reverse=True)
    
    logger.debug(f"{len(sorted_suggestions)} suggestions sorted by priority_score.")
    if sorted_suggestions:
        logger.debug(f"Top sorted suggestion ID {sorted_suggestions[0].get('suggestion_id', 'N/A')} with score {sorted_suggestions[0]['_priority_score']}")

    for suggestion in sorted_suggestions:
        action_type = suggestion.get("action_type")
        action_details = suggestion.get("action_details")

        priority_score_for_log = suggestion.pop("_priority_score", None) # Remove temp score

        if action_type == "UPDATE_TOOL_DESCRIPTION":
            if isinstance(action_details, dict) and \
               isinstance(action_details.get("tool_name"), str) and action_details.get("tool_name") and \
               action_details.get("new_description") is not None and isinstance(action_details.get("new_description"), str):
                logger.info(f"Selected suggestion ID {suggestion.get('suggestion_id', 'N/A')} (Update Tool Desc) with priority score {priority_score_for_log}.")
                # Placeholder for actual execution - for now, just mark as PENDING
                suggestion['_action_result'] = {
                    'status': 'PENDING_EXECUTION',
                    'message': 'Action selected for UPDATE_TOOL_DESCRIPTION, but not executed by select_suggestion_for_autonomous_action. Execution should be handled by caller.',
                    'details': action_details
                }
                return suggestion
        
        elif action_type == "CREATE_NEW_TOOL":
            if isinstance(action_details, dict) and \
               isinstance(action_details.get("tool_description_prompt"), str) and action_details.get("tool_description_prompt"):
                logger.info(f"Selected suggestion ID {suggestion.get('suggestion_id', 'N/A')} (Create New Tool) with priority score {priority_score_for_log}.")
                # Placeholder for actual execution
                suggestion['_action_result'] = {
                    'status': 'PENDING_EXECUTION',
                    'message': 'Action selected for CREATE_NEW_TOOL, but not executed by select_suggestion_for_autonomous_action. Execution should be handled by caller.',
                    'details': action_details
                }
                return suggestion

        elif action_type == "ADJUST_INTERNAL_PROMPT":
            if isinstance(action_details, dict) and \
               action_details.get("prompt_area_identifier") and isinstance(action_details.get("prompt_area_identifier"), str) and \
               action_details.get("suggested_change_summary") and isinstance(action_details.get("suggested_change_summary"), str):

                # Specific criteria for ADJUST_INTERNAL_PROMPT
                passes_specific_criteria = (
                    suggestion.get("reviewer_confidence", 0.0) >= 0.75 and
                    suggestion.get("impact_score", 0) >= 3 and
                    suggestion.get("risk_score", 5) <= 2 and
                    suggestion.get("effort_score", 5) <= 2
                )

                if passes_specific_criteria:
                    logger.info(f"Attempting to apply internal prompt adjustment for suggestion ID {suggestion.get('suggestion_id', 'N/A')} (Priority: {priority_score_for_log}). Details: {action_details}")

                    prompt_area_identifier = action_details.get("prompt_area_identifier")
                    suggested_change_summary = action_details.get("suggested_change_summary")

                    adjustment_result = await apply_internal_prompt_adjustment(
                        prompt_identifier=prompt_area_identifier,
                        change_summary=suggested_change_summary,
                        llm_model_name=get_model_for_task("prompt_refinement") # Or pass specific model if configured
                    )

                    suggestion['_action_result'] = adjustment_result # Store the full result from apply_internal_prompt_adjustment

                    global_reflection_log.log_execution(
                        goal_description=f"Self-modification attempt (prompt adjustment) for suggestion {suggestion.get('suggestion_id', 'N/A')}",
                        plan=[{
                            "tool_name": "apply_internal_prompt_adjustment",
                            "args": {"prompt_identifier": prompt_area_identifier, "change_summary": suggested_change_summary},
                            "status": "attempted"
                        }],
                        execution_results=[adjustment_result],
                        overall_success=adjustment_result.get('success', False),
                        notes=adjustment_result.get('message', 'No message from prompt adjustment execution.'),
                        is_self_modification_attempt=True,
                        source_suggestion_id=suggestion.get('suggestion_id'),
                        modification_type="ADJUST_INTERNAL_PROMPT",
                        modification_details={
                            "prompt_identifier": prompt_area_identifier,
                            "change_summary": suggested_change_summary,
                            "original_prompt_preview": adjustment_result.get('original_prompt_preview'),
                            "refined_prompt_preview": adjustment_result.get('refined_prompt_preview'),
                            "llm_reasoning": adjustment_result.get('llm_reasoning')
                        },
                        # No direct test/commit for prompt adjustments in this model
                        post_modification_test_passed=None,
                        post_modification_test_details=None,
                        commit_info=None
                    )

                    logger.info(f"Internal prompt adjustment attempt for suggestion {suggestion.get('suggestion_id', 'N/A')} finished. Success: {adjustment_result.get('success')}. Message: {adjustment_result.get('message')}")
                    log_event(
                        event_type="AUTONOMOUS_ACTION_ADJUST_PROMPT_ATTEMPT",
                        description=adjustment_result.get('message', 'Prompt adjustment attempt executed.'),
                        source="autonomous_reflection.select_suggestion_for_autonomous_action",
                        metadata={
                            "suggestion_id": suggestion.get("suggestion_id"),
                            "prompt_identifier": prompt_area_identifier,
                            "success": adjustment_result.get('success'),
                            "priority_score": priority_score_for_log,
                            "llm_model_used": adjustment_result.get("llm_model_used")
                        }
                    )
                    return suggestion
                else:
                    logger.debug(f"Suggestion {suggestion.get('suggestion_id', 'N/A')} of type ADJUST_INTERNAL_PROMPT did not meet specific execution criteria (confidence, I/R/E). Confidence: {suggestion.get('reviewer_confidence', 0.0)}, I: {suggestion.get('impact_score', 0)}, R: {suggestion.get('risk_score', 5)}, E: {suggestion.get('effort_score', 5)}.")
            else:
                logger.warning(f"Skipping ADJUST_INTERNAL_PROMPT suggestion {suggestion.get('suggestion_id', 'N/A')} due to missing/invalid action_details: {action_details}")


        elif action_type == "MODIFY_TOOL_CODE":
            if isinstance(action_details, dict) and \
               isinstance(action_details.get("module_path"), str) and action_details.get("module_path") and \
               isinstance(action_details.get("function_name"), str) and action_details.get("function_name") and \
               isinstance(action_details.get("suggested_code_change"), str) and action_details.get("suggested_code_change"):
                
                code_mod_params = {
                    "module_path": action_details["module_path"],
                    "function_name": action_details["function_name"],
                    "suggested_code_change": action_details["suggested_code_change"]
                }

                logger.info(f"Attempting to apply code modification for tool '{code_mod_params['function_name']}' "+
                            f"in module '{code_mod_params['module_path']}' based on suggestion "+
                            f"{suggestion.get('suggestion_id', 'N/A')} (Priority: {priority_score_for_log}).")
                
                code_mod_result = await apply_code_modification(code_mod_params)
                suggestion['_action_result'] = code_mod_result # Store the result
                
                if code_mod_result is None:
                    logger.error(f"apply_code_modification returned None for suggestion {suggestion.get('suggestion_id', 'N/A')}. Cannot log detailed outcome.")
                    return suggestion

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
                    "suggested_change_description": suggestion.get("action_details", {}).get("suggested_change_description"),
                    "original_code_snippet": suggestion.get("action_details", {}).get("original_code_snippet"),
                }

                global_reflection_log.log_execution(
                    goal_description=f"Self-modification attempt for suggestion {suggestion.get('suggestion_id', 'N/A')}",
                    plan=[{
                        "tool_name": "apply_code_modification", 
                        "args": [code_mod_params],
                        "status": "attempted"
                    }],
                    execution_results=[code_mod_result],
                    overall_success=overall_success_from_apply,
                    notes=detailed_message_from_apply,
                    is_self_modification_attempt=True,
                    source_suggestion_id=suggestion.get('suggestion_id'),
                    modification_type="MODIFY_TOOL_CODE",
                    modification_details=modification_details_for_log_dict,
                    post_modification_test_passed=test_passed_for_log,
                    post_modification_test_details=test_outcome_details,
                    commit_info=commit_info_for_log
                )
                
                logger.info(detailed_message_from_apply)
                log_event(
                    event_type="AUTONOMOUS_ACTION_MODIFY_TOOL_CODE_ATTEMPT",
                    description=detailed_message_from_apply,
                    source="autonomous_reflection.select_suggestion_for_autonomous_action",
                    metadata={
                        "suggestion_id": suggestion.get("suggestion_id"),
                        "tool_name": code_mod_params['function_name'],
                        "module_path": code_mod_params['module_path'],
                        "overall_outcome_success": overall_success_from_apply,
                        "edit_status": code_mod_result.get("edit_outcome", {}).get("status"),
                        "test_status": test_passed_for_log,
                        "revert_status": (code_mod_result.get("revert_outcome") or {}).get("status"),
                        "commit_status": commit_info_for_log.get("status") if commit_info_for_log else None,
                        "priority_score": priority_score_for_log
                    }
                )
                return suggestion # Return the suggestion with the action_result
            else:
                logger.warning(f"Skipping MODIFY_TOOL_CODE suggestion {suggestion.get('suggestion_id', 'N/A')} due to missing/invalid action_details: {action_details}")
        
    logger.debug("No suggestion passed action_details validation or other criteria after sorting by priority_score.")
    return None

if __name__ == '__main__':
    # Setup basic logging for the test run if not already configured
    if not logging.getLogger().handlers: # pragma: no cover
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    print("--- Testing Reflection Log Analysis Function ---")
    mock_suggestions_for_select_test = [
        {
            "suggestion_id": "MTC001", "action_type": "MODIFY_TOOL_CODE", "priority": "High",
            "impact_score": 4, "risk_score": 1, "effort_score": 2, "review_looks_good": True, "reviewer_confidence": 0.8,
            "action_details": {
                "module_path": "ai_assistant.tools.sample_tool", 
                "function_name": "do_something", 
                "suggested_code_change": "def do_something(new_param):\n  pass"
            }
        },
        {
            "suggestion_id": "UTD001", "action_type": "UPDATE_TOOL_DESCRIPTION", "priority": "Medium", 
            "impact_score": 3, "risk_score": 1, "effort_score": 1, "review_looks_good": True, "reviewer_confidence": 0.7,
            "action_details": {"tool_name": "tool_A", "new_description": "New desc for A"}
        },
        {
            "suggestion_id": "MTC002", "action_type": "MODIFY_TOOL_CODE", "priority": "High",
            "impact_score": 4, "risk_score": 2, "effort_score": 2, "review_looks_good": True, "reviewer_confidence": 0.75,
            "action_details": {"module_path": "ai_assistant.tools.another_tool", "function_name": "another_func"}
        },
        {
            "suggestion_id": "CNT001", "action_type": "CREATE_NEW_TOOL", "priority": "High",
            "impact_score": 5, "risk_score": 2, "effort_score": 3, "review_looks_good": True, "reviewer_confidence": 0.85,
            "action_details": {"tool_description_prompt": "A tool to do X."}
        },
        {
            "suggestion_id": "MTC003_REJECTED", "action_type": "MODIFY_TOOL_CODE", "priority": "High",
            "impact_score": 5, "risk_score": 1, "effort_score": 1, "review_looks_good": False, "reviewer_confidence": 0.2,
            "qualitative_review": "Reviewer found potential issues.",
            "action_details": {
                "module_path": "ai_assistant.tools.rejected_tool", 
                "function_name": "rejected_func", 
                "suggested_code_change": "def rejected_func():\n  # risky change\n  pass"
            }
        },
        # Suggestion for ADJUST_INTERNAL_PROMPT
        {
            "suggestion_id": "AIP001", "action_type": "ADJUST_INTERNAL_PROMPT", "priority": "High",
            "impact_score": 4, "risk_score": 1, "effort_score": 1, # Meets specific criteria
            "review_looks_good": True, "reviewer_confidence": 0.8,
            "action_details": {
                "prompt_area_identifier": "general_qa_response_prompt",
                "suggested_change_summary": "Make responses more concise."
            }
        },
        {
            "suggestion_id": "AIP002_LOW_CONFIDENCE", "action_type": "ADJUST_INTERNAL_PROMPT", "priority": "Medium",
            "impact_score": 4, "risk_score": 1, "effort_score": 1,
            "review_looks_good": True, "reviewer_confidence": 0.7, # Below 0.75 confidence
            "action_details": {
                "prompt_area_identifier": "planning_prompt",
                "suggested_change_summary": "Consider more alternatives."
            }
        },
        {
            "suggestion_id": "AIP003_HIGH_RISK", "action_type": "ADJUST_INTERNAL_PROMPT", "priority": "Low",
            "impact_score": 4, "risk_score": 3, "effort_score": 1, # Risk > 2
            "review_looks_good": True, "reviewer_confidence": 0.9,
            "action_details": {
                "prompt_area_identifier": "tool_usage_prompt",
                "suggested_change_summary": "Be more aggressive in tool use."
            }
        }
    ]

    async def run_select_suggestion_test_mtc_success():
        with patch('ai_assistant.learning.evolution.apply_code_modification', new_callable=AsyncMock) as mock_apply_code, \
             patch('ai_assistant.core.autonomous_reflection.global_reflection_log.log_execution') as mock_log_execution, \
             patch('ai_assistant.core.autonomous_reflection.log_event') as mock_log_event:

            mock_apply_code.return_value = {
                "overall_status": True, "overall_message": "Mocked successful code application",
                "edit_outcome": {"status": True, "message": "Edit success", "backup_path": "/tmp/backup.bak"},
                "test_outcome": {"passed": True, "notes": "Tests passed"},
                "commit_outcome": {"status": True, "commit_message_generated": "Mock commit"}
            }

            # Create a subset of suggestions for this test to ensure MTC001 is highest priority among valid MODIFY_TOOL_CODE
            mtc_test_suggestions = [s for s in mock_suggestions_for_select_test if s["suggestion_id"] in ["MTC001", "UTD001", "MTC002"]]

            selected_action = await select_suggestion_for_autonomous_action(
                mtc_test_suggestions,
                supported_action_types=["MODIFY_TOOL_CODE", "UPDATE_TOOL_DESCRIPTION"],
                notification_manager=None
            )
            
            assert selected_action is not None, "Expected a suggestion to be selected"
            assert selected_action.get('suggestion_id') == "MTC001", f"Expected MTC001, got {selected_action.get('suggestion_id')}"
            expected_call_params = {
                "module_path": "ai_assistant.tools.sample_tool",
                "function_name": "do_something",
                "suggested_code_change": "def do_something(new_param):\n  pass"
            }
            mock_apply_code.assert_called_once_with(expected_call_params)
            mock_log_execution.assert_called_once()
            mock_log_event.assert_called_once()
            assert selected_action['_action_result']['overall_status'] is True
            print(f"run_select_suggestion_test_mtc_success: Passed. Selected: {selected_action.get('suggestion_id')}")

    asyncio.run(run_select_suggestion_test_mtc_success())

    async def run_select_suggestion_test_mtc_failure():
        with patch('ai_assistant.learning.evolution.apply_code_modification', new_callable=AsyncMock) as mock_apply_code_fail, \
             patch('ai_assistant.core.autonomous_reflection.global_reflection_log.log_execution') as mock_log_execution, \
             patch('ai_assistant.core.autonomous_reflection.log_event') as mock_log_event:

            mock_apply_code_fail.return_value = {
                "overall_status": False, "overall_message": "Mocked application failure",
                "edit_outcome": {"status": True}, "test_outcome": {"passed": False, "notes": "Test failed"},
                "revert_outcome": {"status": True, "message": "Reverted"}
            }
            
            # Test with a suggestion that would be chosen if MTC was the only type
            mtc_failure_suggestions = [s for s in mock_suggestions_for_select_test if s["suggestion_id"] == "MTC001"]

            selected_action_fail = await select_suggestion_for_autonomous_action(
                mtc_failure_suggestions,
                supported_action_types=["MODIFY_TOOL_CODE"],
                notification_manager=None
            )
            assert selected_action_fail is not None, "Expected a suggestion to be selected even on failure for logging"
            assert selected_action_fail.get('suggestion_id') == "MTC001"
            mock_apply_code_fail.assert_called_once()
            mock_log_execution.assert_called_once()
            mock_log_event.assert_called_once()
            assert selected_action_fail['_action_result']['overall_status'] is False
            print(f"run_select_suggestion_test_mtc_failure: Passed. Selected: {selected_action_fail.get('suggestion_id')}")

    asyncio.run(run_select_suggestion_test_mtc_failure())

    async def run_select_suggestion_test_rejected_mtc_selects_next_best():
         with patch('ai_assistant.learning.evolution.apply_code_modification', new_callable=AsyncMock) as mock_apply_code, \
              patch('ai_assistant.learning.evolution.apply_internal_prompt_adjustment', new_callable=AsyncMock) as mock_apply_prompt_adj:

            # MTC003_REJECTED has review_looks_good = False
            # UTD001 is a valid UPDATE_TOOL_DESCRIPTION, should be selected.
            # AIP001 is a valid ADJUST_INTERNAL_PROMPT, but UTD001 has higher calculated priority due to MTC003_REJECTED being filtered out first.
            # (Assuming default scoring leads to UTD001 > AIP001 if MTC001 is not present)
            # Let's make UTD001 have a slightly better score than AIP001
            # UTD001: I3, R1, E1 => Score = 3 - 1 - 0.5 = 1.5
            # AIP001: I4, R1, E1 => Score = 4 - 1 - 0.5 = 2.5
            # So AIP001 should be selected if MTC003 is rejected.

            suggestions_for_this_test = [
                next(s for s in mock_suggestions_for_select_test if s["suggestion_id"] == "MTC003_REJECTED"), # review_looks_good = False
                next(s for s in mock_suggestions_for_select_test if s["suggestion_id"] == "UTD001"),          # Prio = 1.5
                next(s for s in mock_suggestions_for_select_test if s["suggestion_id"] == "AIP001")           # Prio = 2.5, should be selected
            ]

            mock_apply_prompt_adj.return_value = {"success": True, "message": "Prompt adjusted"}


            selected_action = await select_suggestion_for_autonomous_action(
                suggestions_for_this_test,
                supported_action_types=["MODIFY_TOOL_CODE", "UPDATE_TOOL_DESCRIPTION", "ADJUST_INTERNAL_PROMPT"],
                notification_manager=None
            )
            assert selected_action is not None
            assert selected_action.get('suggestion_id') == "AIP001", f"Expected AIP001, got {selected_action.get('suggestion_id')}"
            mock_apply_code.assert_not_called() # MTC003 was rejected
            mock_apply_prompt_adj.assert_called_once() # AIP001 was selected and actioned
            print(f"run_select_suggestion_test_rejected_mtc_selects_next_best: Passed. Selected: {selected_action.get('suggestion_id')}")

    asyncio.run(run_select_suggestion_test_rejected_mtc_selects_next_best())

    async def run_select_suggestion_test_aip_success():
        with patch('ai_assistant.learning.evolution.apply_internal_prompt_adjustment', new_callable=AsyncMock) as mock_apply_prompt_adj, \
             patch('ai_assistant.core.autonomous_reflection.global_reflection_log.log_execution') as mock_log_execution, \
             patch('ai_assistant.core.autonomous_reflection.log_event') as mock_log_event, \
             patch('ai_assistant.config.get_model_for_task', return_value="test_model_refinement") as mock_get_model:

            mock_apply_prompt_adj.return_value = {
                "success": True,
                "message": "Mocked successful prompt adjustment",
                "original_prompt_preview": "Old prompt...",
                "refined_prompt_preview": "New prompt...",
                "llm_reasoning": "Because reasons.",
                "llm_model_used": "test_model_refinement"
            }

            aip_suggestion = next(s for s in mock_suggestions_for_select_test if s["suggestion_id"] == "AIP001")
            # Ensure it's the only one to guarantee selection if valid
            selected_action = await select_suggestion_for_autonomous_action(
                [aip_suggestion],
                supported_action_types=["ADJUST_INTERNAL_PROMPT"],
                notification_manager=None
            )

            assert selected_action is not None, "Expected AIP001 to be selected"
            assert selected_action.get('suggestion_id') == "AIP001"

            mock_get_model.assert_called_with("prompt_refinement")
            mock_apply_prompt_adj.assert_called_once_with(
                prompt_identifier="general_qa_response_prompt",
                change_summary="Make responses more concise.",
                llm_model_name="test_model_refinement"
            )
            mock_log_execution.assert_called_once()
            log_execution_args = mock_log_execution.call_args[1]
            assert log_execution_args['modification_type'] == "ADJUST_INTERNAL_PROMPT"
            assert log_execution_args['overall_success'] is True
            assert log_execution_args['modification_details']['prompt_identifier'] == "general_qa_response_prompt"

            mock_log_event.assert_called_once()
            log_event_args = mock_log_event.call_args[1]
            assert log_event_args['event_type'] == "AUTONOMOUS_ACTION_ADJUST_PROMPT_ATTEMPT"
            assert log_event_args['metadata']['success'] is True
            assert log_event_args['metadata']['prompt_identifier'] == "general_qa_response_prompt"

            assert selected_action['_action_result']['success'] is True
            print(f"run_select_suggestion_test_aip_success: Passed. Selected: {selected_action.get('suggestion_id')}")

    asyncio.run(run_select_suggestion_test_aip_success())

    async def run_select_suggestion_test_aip_fail_criteria():
        with patch('ai_assistant.learning.evolution.apply_internal_prompt_adjustment', new_callable=AsyncMock) as mock_apply_prompt_adj:

            # AIP002_LOW_CONFIDENCE (conf 0.7 < 0.75)
            # AIP003_HIGH_RISK (risk 3 > 2)
            # UTD001 (should be selected as fallback as the AIPs fail criteria)
            suggestions_for_this_test = [
                next(s for s in mock_suggestions_for_select_test if s["suggestion_id"] == "AIP002_LOW_CONFIDENCE"),
                next(s for s in mock_suggestions_for_select_test if s["suggestion_id"] == "AIP003_HIGH_RISK"),
                next(s for s in mock_suggestions_for_select_test if s["suggestion_id"] == "UTD001")
            ]
            # Order them so UTD001 is last to ensure priority logic is tested if AIPs were valid
            # AIP002 prio: 4-1-0.5 = 2.5 (but fails confidence)
            # AIP003 prio: 4-3-0.5 = 0.5 (but fails risk)
            # UTD001 prio: 3-1-0.5 = 1.5 (should be chosen)

            # Re-sort based on how select_suggestion_for_autonomous_action would sort them by _priority_score initially
            # This requires mock_suggestions_for_select_test to have these scores or calculate them
            # For simplicity, let's assume the order above is what select_suggestion would process after initial sorting.
            # The key is that AIP002 and AIP003 will be iterated first due to higher initial _priority_score
            # but should be skipped due to specific criteria, then UTD001 should be picked.

            # To make this test robust, let's ensure AIP002 would be picked if it passed criteria
            # by making UTD001 less attractive temporarily for the sorting part of the test
            temp_utd = next(s for s in suggestions_for_this_test if s["suggestion_id"] == "UTD001").copy()
            temp_utd["impact_score"] = 1 # Lower its prio score significantly for sorting

            # Construct the list so AIP002 (higher initial prio) comes before the modified UTD001
            sorted_test_suggestions = [
                 next(s for s in suggestions_for_this_test if s["suggestion_id"] == "AIP002_LOW_CONFIDENCE"), # Highest prio before criteria
                 next(s for s in suggestions_for_this_test if s["suggestion_id"] == "AIP003_HIGH_RISK"),   # Mid prio before criteria
                 temp_utd # Lowest prio before criteria
            ]


            selected_action = await select_suggestion_for_autonomous_action(
                sorted_test_suggestions, # Use this carefully constructed order
                supported_action_types=["ADJUST_INTERNAL_PROMPT", "UPDATE_TOOL_DESCRIPTION"],
                notification_manager=None
            )

            assert selected_action is not None
            assert selected_action.get('suggestion_id') == "UTD001", f"Expected UTD001, got {selected_action.get('suggestion_id')}"
            mock_apply_prompt_adj.assert_not_called() # Both AIPs should have failed criteria
            print(f"run_select_suggestion_test_aip_fail_criteria: Passed. Selected: {selected_action.get('suggestion_id')}")

    asyncio.run(run_select_suggestion_test_aip_fail_criteria())
            
    print("\n--- select_suggestion_for_autonomous_action tests complete ---")
