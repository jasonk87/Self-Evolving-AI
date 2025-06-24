# ai_assistant/core/reflection.py
import datetime
import re
import json # For the simplistic check in to_serializable_dict
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import traceback # For serializing exception tracebacks
import uuid
import os # For os.path.exists and os.path.getsize
from ai_assistant.config import is_debug_mode

# Import the new functions and filepath from persistent_memory
from ai_assistant.memory.persistent_memory import (
    save_reflection_log_entries,
    load_reflection_log_entries,
    REFLECTION_LOG_FILEPATH # Import the default filepath
)

@dataclass
class ReflectionLogEntry:
    """Represents a single entry in the reflection log."""
    goal_description: str
    plan: List[Dict[str, Any]]
    execution_results: List[Any]
    status: str
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))  # Unique ID for each entry
    notes: Optional[str] = ""
    timestamp: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    traceback_snippet: Optional[str] = None
    # New fields for self-modification attempts
    is_self_modification_attempt: bool = False
    source_suggestion_id: Optional[str] = None
    modification_type: Optional[str] = None # E.g., "MODIFY_TOOL_CODE", "UPDATE_TOOL_DESCRIPTION"
    modification_details: Optional[Dict[str, Any]] = None # E.g., {"module_path": ..., "function_name": ..., "suggested_code_change": ...}
    post_modification_test_passed: Optional[bool] = None
    post_modification_test_details: Optional[Dict[str, Any]] = None # E.g., {"passed": True/False, "stdout": ..., "stderr": ..., "notes": ...}
    commit_info: Optional[Dict[str, Any]] = None # E.g., {"commit_message": ..., "commit_hash": ...}


    def to_serializable_dict(self) -> Dict[str, Any]:
        """Converts the entry to a dictionary suitable for JSON serialization."""
        serializable_results = []
        for res in self.execution_results:
            if isinstance(res, Exception):
                # CHANGE 1: Store a structured dictionary for exceptions
                serializable_results.append({
                    "_is_error_representation_": True,  # Clear marker
                    "error_type_name": type(res).__name__,
                    "error_message_str": str(res),
                    # Optionally add traceback for individual step errors if needed:
                    # "error_traceback_snippet": traceback.format_exception_only(type(res), res)[-1].strip()
                })
            else:
                try:
                    json.dumps(res) # Check if directly serializable
                    serializable_results.append(res)
                except (TypeError, OverflowError):
                    serializable_results.append(str(res)) # Fallback to string

        return {
            "goal_description": self.goal_description,
            "plan": self.plan,
            "execution_results": serializable_results, # Contains structured dicts for errors
            "status": self.status,
            "entry_id": self.entry_id,
            "notes": self.notes,
            "timestamp": self.timestamp.isoformat(),
            "error_type": self.error_type,
            "error_message": self.error_message,
            "traceback_snippet": self.traceback_snippet,
            # New fields
            "is_self_modification_attempt": self.is_self_modification_attempt,
            "source_suggestion_id": self.source_suggestion_id,
            "modification_type": self.modification_type,
            "modification_details": self.modification_details,
            "post_modification_test_passed": self.post_modification_test_passed,
            "post_modification_test_details": self.post_modification_test_details,
            "commit_info": self.commit_info,
        }

    @classmethod
    def from_serializable_dict(cls, data: Dict[str, Any]) -> 'ReflectionLogEntry':
        """Creates a ReflectionLogEntry from a dictionary (e.g., loaded from JSON)."""
        timestamp_str = data.get("timestamp")
        timestamp_obj = None
        if timestamp_str:
            try:
                timestamp_obj = datetime.datetime.fromisoformat(timestamp_str)
            except ValueError:
                print(f"Warning: Could not parse timestamp '{timestamp_str}'. Using current UTC time for this entry.")
                timestamp_obj = datetime.datetime.now(datetime.timezone.utc)
        else:
            timestamp_obj = datetime.datetime.now(datetime.timezone.utc)

        return cls(
            goal_description=data.get("goal_description", ""),
            plan=data.get("plan", []),
            execution_results=data.get("execution_results", []), # Will contain dicts for errors
            status=data.get("status", "UNKNOWN"),
            entry_id=data.get("entry_id", str(uuid.uuid4())),  # Generate new ID if not present
            notes=data.get("notes", ""),
            timestamp=timestamp_obj,
            error_type=data.get("error_type"),
            error_message=data.get("error_message"),
            traceback_snippet=data.get("traceback_snippet"),
            # New fields
            is_self_modification_attempt=data.get("is_self_modification_attempt", False),
            source_suggestion_id=data.get("source_suggestion_id"),
            modification_type=data.get("modification_type"),
            modification_details=data.get("modification_details"),
            post_modification_test_passed=data.get("post_modification_test_passed"),
            post_modification_test_details=data.get("post_modification_test_details"),
            commit_info=data.get("commit_info"),
        )

    def to_formatted_string(self) -> str:
        """Formats the log entry into a human-readable string."""
        formatted_plan_steps = []
        if self.plan:
            for i, step in enumerate(self.plan):
                formatted_plan_steps.append(
                    f"    Step {i+1}: Tool: {step.get('tool_name', 'N/A')}, "
                    f"Args: {step.get('args', 'N/A')}, Kwargs: {step.get('kwargs', 'N/A')}"
                )
        else:
            formatted_plan_steps.append("    No plan executed.")
        formatted_plan = "\n".join(formatted_plan_steps)

        formatted_results_steps = []
        if self.execution_results:
            for i, res_item in enumerate(self.execution_results):
                res_str = ""
                if isinstance(res_item, dict) and res_item.get("_is_error_representation_"):
                    res_str = f"Error: {res_item.get('error_type_name')}: {res_item.get('error_message_str')}"
                else:
                    res_str = str(res_item)

                if len(res_str) > 150:
                    res_str = res_str[:147] + "..."
                formatted_results_steps.append(f"    Step {i+1} Result: {res_str}")
        else:
            formatted_results_steps.append("    No results recorded.")
        formatted_results = "\n".join(formatted_results_steps)

        notes_str = f"Notes: {self.notes}\n" if self.notes else ""

        error_details_str = ""
        if self.error_type:
            error_details_str = (
                f"First Critical Error:\n"
                f"  Type: {self.error_type}\n"
                f"  Message: {self.error_message}\n"
            )
            if self.traceback_snippet:
                error_details_str += f"  Traceback Snippet:\n{self.traceback_snippet}\n"

        timestamp_display = self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
        if self.timestamp.tzinfo:
            timestamp_display = self.timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')

        # Start building output_parts list
        output_parts = [
            "--------------------------------------------------",
            f"Timestamp: {timestamp_display}",
            f"Goal: {self.goal_description}",
            f"Status: {self.status}"
        ]
        if notes_str: # Only add if notes_str is not empty
            output_parts.append(notes_str.strip()) # Remove trailing newline from notes_str if it exists
        if error_details_str: # Only add if error_details_str is not empty
            output_parts.append(error_details_str.strip()) # Remove trailing newline
        output_parts.extend([
            f"Plan:\n{formatted_plan}",
            f"Results (individual steps):\n{formatted_results}"
        ])

        if self.is_self_modification_attempt:
            output_parts.append("--- Self-Modification Attempt Details ---")
            if self.source_suggestion_id:
                output_parts.append(f"Source Suggestion ID: {self.source_suggestion_id}")
            if self.modification_type:
                output_parts.append(f"Modification Type: {self.modification_type}")
            if self.modification_details:
                try:
                    details_str = json.dumps(self.modification_details, indent=2, sort_keys=True)
                    # For very long code changes, maybe just show keys or a summary
                    if len(details_str) > 500: 
                        details_str = f"Keys: {list(self.modification_details.keys())} (Details too long to display fully)"
                except TypeError:
                    details_str = str(self.modification_details) # Fallback
                output_parts.append(f"Modification Details: {details_str}")

            test_passed_str = "N/A"
            if self.post_modification_test_passed is True:
                test_passed_str = "True"
            elif self.post_modification_test_passed is False:
                test_passed_str = "False"
            output_parts.append(f"Post-Modification Test Passed: {test_passed_str}")

            if self.post_modification_test_details:
                test_details_summary = self.post_modification_test_details.get("notes", "No specific notes.")
                if len(test_details_summary) > 200 : test_details_summary = test_details_summary[:197] + "..."
                output_parts.append(f"Post-Modification Test Details: {test_details_summary}")
            
            if self.commit_info:
                commit_message_summary = self.commit_info.get("commit_message", "N/A")
                if len(commit_message_summary) > 200 : commit_message_summary = commit_message_summary[:197] + "..."
                output_parts.append(f"Commit Info: {commit_message_summary}")
            
            output_parts.append("---------------------------------------")
        
        output_parts.append("--------------------------------------------------")
        return "\n".join(output_parts)


class ReflectionLog:
    """Manages a log of reflection entries with persistence."""
    def __init__(self, filepath: str = REFLECTION_LOG_FILEPATH):
        self.filepath: str = filepath
        self.log_entries: List[ReflectionLogEntry] = []
        self.load_log()

    def load_log(self):
        """Loads reflection log entries from the persistent file."""
        if is_debug_mode():
            print(f"ReflectionLog: Loading log from '{self.filepath}'...")
        loaded_entry_dicts = load_reflection_log_entries(self.filepath)
        temp_entries = []
        for entry_data in loaded_entry_dicts:
            if not isinstance(entry_data, dict):
                # This is a warning, so it should probably always print or use logger.warning
                print(f"ReflectionLog: Warning - Skipping non-dictionary item in loaded log data: {str(entry_data)[:100]}...")
                continue
            try:
                temp_entries.append(ReflectionLogEntry.from_serializable_dict(entry_data))
            except Exception as e:
                print(f"ReflectionLog: Error deserializing entry data: '{str(entry_data)[:100]}...'. Error: {e}. Skipping.")
        self.log_entries = temp_entries
        if is_debug_mode():
            print(f"ReflectionLog: Loaded {len(self.log_entries)} entries from '{self.filepath}'.")
        if not self.log_entries and os.path.exists(self.filepath) and os.path.getsize(self.filepath) > 0:
             print(f"ReflectionLog: Warning - File '{self.filepath}' exists and is not empty, but no valid log entries were loaded. The file might be corrupted or in an old format.")

    def save_log(self):
        """Saves the current reflection log entries to the persistent file."""
        if is_debug_mode():
            print(f"ReflectionLog: Attempting to save {len(self.log_entries)} log entries to '{self.filepath}'...")
        entries_to_save = []
        for entry in self.log_entries:
            try:
                entries_to_save.append(entry.to_serializable_dict())
            except Exception as e:
                print(f"ReflectionLog: Error serializing entry for goal '{entry.goal_description}'. Error: {e}. Skipping this entry for save.")

        if save_reflection_log_entries(self.filepath, entries_to_save):
            if is_debug_mode():
                print(f"ReflectionLog: Successfully saved {len(entries_to_save)} entries to '{self.filepath}'.")
        else:
            print(f"ReflectionLog: Failed to save log entries to '{self.filepath}'.")

    def add_entry(self, entry: ReflectionLogEntry):
        """Appends a new entry to the in-memory log."""
        self.log_entries.append(entry)
        # Save the log immediately after adding an entry
        self.save_log()

    def get_entries(self, limit: int = 10) -> List[ReflectionLogEntry]:
        """Returns the last 'limit' entries from the in-memory log."""
        if limit <= 0:
            return []
        return self.log_entries[-limit:]

    def log_execution(
        self,
        goal_description: str,
        plan: List[Dict[str, Any]],
        execution_results: List[Any],
        overall_success: bool,
        notes: str = "",
        first_error_type: Optional[str] = None,
        first_error_message: Optional[str] = None,
        first_traceback_snippet: Optional[str] = None,
        status_override: Optional[str] = None,
        # New parameters for self-modification
        is_self_modification_attempt: bool = False,
        source_suggestion_id: Optional[str] = None,
        modification_type: Optional[str] = None,
        modification_details: Optional[Dict[str, Any]] = None,
        post_modification_test_passed: Optional[bool] = None,
        post_modification_test_details: Optional[Dict[str, Any]] = None,
        commit_info: Optional[Dict[str, Any]] = None
    ) -> ReflectionLogEntry: # Add return type
        current_status: str
        if status_override:
            current_status = status_override
        else:
            current_status = "FAILURE"
            if overall_success:
                current_status = "SUCCESS"
            else:
                if plan:
                    successful_steps = 0
                    # CHANGE 2: Check for our error representation in status determination
                    for result in execution_results:
                        is_error_representation = isinstance(result, dict) and result.get("_is_error_representation_")
                        if not isinstance(result, Exception) and not is_error_representation:
                            successful_steps += 1

                    if successful_steps == len(plan) and plan:
                        current_status = "SUCCESS"
                        if not overall_success:
                            if not notes: notes += " "
                            notes += "(Note: overall_success was False but all plan steps completed without error representations)."
                    elif successful_steps > 0 and successful_steps < len(plan):
                        current_status = "PARTIAL_SUCCESS"
                    else:
                        current_status = "FAILURE"
                # else current_status remains "FAILURE"

            if not plan and not execution_results:
                current_status = "SUCCESS" if overall_success else "EMPTY_FAILURE"
                if not notes: notes += " "
                notes += "No plan or results. Marked as " + ("successful." if overall_success else "unsuccessful empty goal.")
            elif overall_success and not execution_results and plan:
                current_status = "ANOMALY_SUCCESS_NO_RESULTS"
                if not notes: notes += " "
                notes += "Marked SUCCESS but no results for non-empty plan."
            elif not plan and execution_results:
                current_status = "ANOMALY_RESULTS_NO_PLAN"
                if not notes: notes += " "
                notes += "Results present without a plan."

        entry = ReflectionLogEntry(
            goal_description=goal_description,
            plan=plan,
            execution_results=execution_results,
            status=current_status,
            notes=notes.strip(),
            error_type=first_error_type,
            error_message=first_error_message,
            traceback_snippet=first_traceback_snippet,
            # Pass new fields
            is_self_modification_attempt=is_self_modification_attempt,
            source_suggestion_id=source_suggestion_id,
            modification_type=modification_type,
            modification_details=modification_details,
            post_modification_test_passed=post_modification_test_passed,
            post_modification_test_details=post_modification_test_details,
            commit_info=commit_info
        )
        self.add_entry(entry)
        return entry # Return the created entry

global_reflection_log = ReflectionLog()

_STOP_WORDS = set([
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should", "can",
    "could", "may", "might", "must", "and", "or", "but", "if", "because", "as",
    "until", "while", "of", "at", "by", "for", "with", "about", "against",
    "between", "into", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "in", "out", "on", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "s", "t", "can", "will", "just", "don", "should", "now", "tool", "function",
    "input", "inputs", "output", "outputs", "args", "str", "int", "float", "list",
    "returns", "calculates", "performs", "executes", "given", "specified"
])

def _extract_keywords(text: str) -> set:
    if not text:
        return set()
    words = re.findall(r'\b\w+\b', text.lower())
    return {word for word in words if word not in _STOP_WORDS and len(word) > 2}

LLM_FAILURE_ANALYSIS_PROMPT = """The AI assistant encountered an issue in its last operation. Please analyze the details and provide insights.
Original Goal: {goal}
Attempted Plan:
{plan_str}
Execution Status: {status}
Error Type: {error_type}
Error Message: {error_message}
Traceback Snippet (if available):
{traceback_snippet}
Available Tools (Name: Description):
{tools_json_str}
Based on this information:
1. Explain the likely cause of the failure in simple terms.
2. If applicable, suggest how the specific tool(s) that failed in the plan might be used differently (e.g., different arguments, fixing input) to achieve their part of the goal.
3. Suggest any alternative tools or sequences of tools from the 'Available Tools' list that could be used to achieve the original goal.
Please structure your response clearly (e.g., using markdown headings for "Cause", "Suggestions for Failed Tool", "Alternative Approaches").
Response:
"""

def analyze_last_failure(tool_registry: Dict[str, str], ollama_model_name: Optional[str] = None) -> Optional[str]:
    from ai_assistant.llm_interface.ollama_client import invoke_ollama_model
    from ai_assistant.config import get_model_for_task

    model_to_use = ollama_model_name if ollama_model_name is not None else get_model_for_task("reflection")

    if not global_reflection_log.log_entries:
        return "No actions logged yet to analyze."
    last_entry = global_reflection_log.log_entries[-1]

    is_truly_failed_for_analysis = False
    if last_entry.status != "SUCCESS" and last_entry.error_type:
        is_truly_failed_for_analysis = True
    if not is_truly_failed_for_analysis:
        return "No critical failure (with error details) found in the last action to analyze with LLM."

    try:
        plan_str = json.dumps(last_entry.plan, indent=2) if last_entry.plan else "No plan was executed or plan was empty."
    except TypeError:
        plan_str = str(last_entry.plan) + " (Note: Plan contained non-serializable data)"
    try:
        tools_json_str = json.dumps(tool_registry, indent=2)
    except TypeError:
        tools_json_str = str(tool_registry) + " (Note: Tool registry contained non-serializable data)"

    formatted_prompt = LLM_FAILURE_ANALYSIS_PROMPT.format(
        goal=last_entry.goal_description,
        plan_str=plan_str,
        status=last_entry.status,
        error_type=last_entry.error_type or "N/A",
        error_message=str(last_entry.error_message) if last_entry.error_message else "N/A",
        traceback_snippet=last_entry.traceback_snippet or "N/A",
        tools_json_str=tools_json_str
    )
    if is_debug_mode():
        print(f"\nReflectionAnalysis: Sending failure analysis prompt to LLM (model: {model_to_use})...")
    llm_analysis = invoke_ollama_model(formatted_prompt, model_name=model_to_use)

    if llm_analysis and llm_analysis.strip():
        return f"--- LLM Failure Analysis ---\n{llm_analysis.strip()}"
    else:
        # Fallback logic for analyze_last_failure
        if is_debug_mode():
            print("ReflectionAnalysis: LLM analysis was unsuccessful or returned empty. Fallback analysis follows.")
        failed_tool_name: Optional[str] = None
        # Try to find the failed tool by checking execution_results for our error dict
        if last_entry.plan and last_entry.execution_results and len(last_entry.plan) == len(last_entry.execution_results):
            for i, res_item in enumerate(last_entry.execution_results):
                if isinstance(res_item, dict) and res_item.get("_is_error_representation_"):
                    if i < len(last_entry.plan):
                        step_info = last_entry.plan[i]
                        if isinstance(step_info, dict):
                            failed_tool_name = step_info.get('tool_name')
                            break
        # If not found, try from error message (less reliable)
        if not failed_tool_name and last_entry.error_message:
            match = re.search(r"Tool '([^']*)'", str(last_entry.error_message))
            if match:
                failed_tool_name = match.group(1)

        error_msg_display = str(last_entry.error_message) if last_entry.error_message else last_entry.error_type
        if failed_tool_name and failed_tool_name in tool_registry:
            failed_tool_keywords = _extract_keywords(tool_registry.get(failed_tool_name, "") + " " + failed_tool_name)
            alternatives = []
            if failed_tool_keywords:
                for name, description in tool_registry.items():
                    if name == failed_tool_name: continue
                    common_keywords = failed_tool_keywords.intersection(_extract_keywords(description + " " + name))
                    if common_keywords:
                        alternatives.append({"name": name, "description": description, "score": len(common_keywords), "common": list(common_keywords)})
                alternatives.sort(key=lambda x: x["score"], reverse=True)
            if alternatives:
                suggestion_str = f"LLM analysis failed. Fallback keyword analysis for tool '{failed_tool_name}' (Error: {error_msg_display}):\n"
                suggestion_str += "Possible alternatives:\n"
                for alt in alternatives[:2]:
                    suggestion_str += f"  - '{alt['name']}': {alt['description']} (Common: {alt['common']})\n"
                return suggestion_str.strip()
            else:
                return f"LLM analysis failed. Fallback: Tool '{failed_tool_name}' failed (Error: {error_msg_display}). No keyword-based alternatives found."
        else:
            return f"LLM analysis failed. Fallback: The operation failed (Error: {error_msg_display}). Review the plan and tool descriptions for alternatives."

def get_learnings_from_reflections(max_entries: int = 50) -> List[str]:
    entries = global_reflection_log.get_entries(limit=max_entries)
    if not entries:
        return ["No reflection log entries found to derive learnings."]

    learning_points: List[str] = []
    for entry in entries:
        timestamp_str = entry.timestamp.strftime('%Y-%m-%d %H:%M')

        if entry.status == "SUCCESS" and entry.notes and "Succeeded on retry" in entry.notes:
            speculative_tool_mention = ""
            if entry.plan and len(entry.plan) == 1:
                 speculative_tool_mention = f" (tool '{entry.plan[0].get('tool_name', 'Unknown Tool')}' was involved)"
            learning = (
                f"Learning [{timestamp_str}]: For goal '{entry.goal_description}', "
                f"a successful outcome was achieved after initial difficulties (retries were needed){speculative_tool_mention}. "
                f"This suggests that the involved tool(s) might be sensitive to transient issues or require specific argument tuning."
            )
            learning_points.append(learning)

        elif entry.status != "SUCCESS" and entry.error_type:
            first_failed_tool_name: Optional[str] = None
            specific_error_message_for_learning: str = entry.error_message or entry.error_type or "Unknown error"

            # CHANGE 3: Use the structured error representation to find the failed tool and its message
            if entry.plan and entry.execution_results and len(entry.plan) == len(entry.execution_results):
                for i, res_item in enumerate(entry.execution_results):
                    if isinstance(res_item, dict) and res_item.get("_is_error_representation_"):
                        if i < len(entry.plan):
                            step_info = entry.plan[i]
                            if isinstance(step_info, dict):
                                first_failed_tool_name = step_info.get('tool_name', 'Unknown Tool in plan')
                                specific_error_message_for_learning = f"Type: {res_item.get('error_type_name')}, Message: {res_item.get('error_message_str')}"
                        break # Focus on the first error found in execution_results

            if first_failed_tool_name and first_failed_tool_name != 'Unknown Tool in plan':
                learning = (
                    f"Learning [{timestamp_str}]: For goal '{entry.goal_description}', "
                    f"tool '{first_failed_tool_name}' likely failed with error details: '{specific_error_message_for_learning}'. "
                    f"Consider reviewing its usage or alternatives."
                )
            else: # General failure or error not tied to a specific tool in execution_results
                learning = (
                     f"Learning [{timestamp_str}]: Goal '{entry.goal_description}' resulted in status '{entry.status}' "
                     f"with primary error: '{specific_error_message_for_learning}'. "
                     f"This might indicate an issue in planning, pre-execution setup, or a tool failure not detailed in step results."
                )
            learning_points.append(learning)

    if not learning_points:
        return ["No specific learning points identified from the recent activity based on current criteria."]
    return learning_points

# Original __main__ block is removed to prevent accidental execution with test data
# and because it would need significant updates to test persistence.