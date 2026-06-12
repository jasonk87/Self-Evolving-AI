"""
This module is central to the AI assistant's ability to learn and evolve from its experiences.
Its primary responsibilities include:

1.  **Processing Reflection Data:**
    *   Analyzing `ReflectionLogEntry` objects provided by the `core.reflection` module.
    *   Leveraging outputs from `reflection.analyze_last_failure()` and `reflection.get_learnings_from_reflections()`.

2.  **Identifying Actionable Insights:**
    *   Distilling concrete, actionable insights from the processed reflection data.
    *   Recognizing patterns of failures, successes, or inefficiencies.
    *   Identifying needs for new knowledge or capabilities.

3.  **Formulating Improvement Proposals:**
    *   Translating insights into specific, testable proposals for improvement. This can include:
        *   **Tool Modification:** Suggesting changes to the source code of existing tools (to be implemented via `core.self_modification`). This could be to fix bugs, enhance functionality, or improve reliability.
        *   **Tool Description Enhancement:** Proposing updates to tool descriptions to make them clearer for the planning module.
        *   **New Tool Suggestion:** Identifying the need for entirely new tools and potentially outlining their desired functionality.
        *   **Knowledge Base Update:** Formulating new facts to be added to the agent's persistent memory (`memory.persistent_memory.save_learned_facts()`).
        *   **Planning Heuristic Refinement:** (Future Goal) Suggesting improvements to the planning strategies or heuristics used by the `planning.planning` module.

4.  **Managing and Prioritizing Insights:**
    *   Storing these actionable insights persistently.
    *   Developing a mechanism to prioritize which insights to act upon first.

5.  **Initiating Improvement Actions:**
    *   Triggering the actual implementation of high-priority improvements. This might involve:
        *   Creating tasks for self-modification using `core.self_modification`.
        *   Adding new facts to its knowledge base.
        *   (In the future) Interacting with a human developer for complex changes or approvals.

This module aims to close the loop in the agent's operational cycle:
Plan -> Execute -> Reflect -> Learn -> Evolve.
"""
import datetime
import os
import asyncio
import uuid # Added for entry_id in MockReflectionLogEntry
from typing import Optional, Dict, Any, List, Tuple # TYPE_CHECKING removed
from dataclasses import asdict
from ..core.task_manager import TaskManager
from ..core.notification_manager import NotificationManager # Made unconditional

from ai_assistant.core.reflection import ReflectionLogEntry, InsightType, ActionableInsight
from ai_assistant.memory.persistent_memory import save_actionable_insights, load_actionable_insights, ACTIONABLE_INSIGHTS_FILEPATH
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.tools.tool_system import get_tool
from ai_assistant.core.chat_manager import ChatSessionManager
from ai_assistant.core import self_modification # For code reading
from ai_assistant.core.failure_freshness import (
    SUPERSEDED_FAILURE_STATUS,
    annotate_failure_metadata,
    is_failure_stale,
)
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async


class LearningAgent:
    def __init__(self, insights_filepath: Optional[str] = None,
                 task_manager: Optional[TaskManager] = None,
                 notification_manager: Optional[NotificationManager] = None,
                 memory_manager: Any = None): # Type hint updated
        self.insights: List[ActionableInsight] = []
        self.insights_filepath = insights_filepath if insights_filepath is not None else ACTIONABLE_INSIGHTS_FILEPATH
        self.task_manager = task_manager
        self.notification_manager = notification_manager # Store it
        self.memory_manager = memory_manager # Store it
        self.action_executor = ActionExecutor(
            learning_agent=self,
            task_manager=self.task_manager,
            notification_manager=self.notification_manager # Pass it
        )
        from ai_assistant.learning.conversation_analyst import ConversationalAnalyst
        self.conversational_analyst = ConversationalAnalyst()
        # Path to chat sessions - Needs to be consistent with web_app.py
        # Web App uses: os.path.join(project_root, "_memory_", "chat_sessions")
        # We should probably get this from config or pass it in. 
        # For now, let's derive it relative to a known location or use a default.
        # Assuming project_root is parent of ai_assistant.
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.chat_storage_dir = os.path.join(base_dir, "_memory_", "chat_sessions")
        self.chat_manager = ChatSessionManager(self.chat_storage_dir)

        self._load_insights()

    async def scan_recent_conversations(self) -> int:
        """
        Scans recent chat sessions for insights using the ConversationalAnalyst.
        Returns the number of new insights found.
        """
        print(f"LearningAgent: Scanning chat sessions in {self.chat_storage_dir}...")
        sessions = self.chat_manager.list_sessions()
        # Limit to recent or specific count to save tokens?
        # For now, just analyze the most recent one if it hasn't been analyzed recently.
        # Ideally, we track 'last_analyzed' per session.
        
        count = 0
        for session_summary in sessions[:3]: # Look at top 3 active sessions
            session_id = session_summary.get("id")
            session_data = self.chat_manager.get_session(session_id)
            if not session_data: continue

            # Optimization: Check if we already analyzed this session state?
            # We could store a hash of history in metadata of an insight?
            # Or just rely on the Analyst to be somewhat idempotent or okay with redundant partial insights.
            # Let's run it.
            
            new_insights = await self.conversational_analyst.analyze_session_transcript(session_data)
            
            for insight in new_insights:
                # Deduplicate based on description/source
                is_duplicate = False
                for existing in self.insights:
                     # Check if similar description
                     # Crude check
                     if insight.description == existing.description:
                         is_duplicate = True
                         break
                
                if not is_duplicate:
                    self.insights.append(insight)
                    count += 1
                    print(f"LearningAgent: Found new conversational insight: {insight.description}")

        if count > 0:
            self._save_insights()
        
        return count


    def _load_insights(self):
        print(f"LearningAgent: Loading insights from '{self.insights_filepath}'...")
        insights_data = load_actionable_insights(filepath=self.insights_filepath)
        loaded_count = 0
        for data in insights_data:
            if not isinstance(data, dict): # pragma: no cover
                print(f"LearningAgent: Warning - skipping non-dictionary item in loaded insights data: {data}")
                continue
            try:
                if 'type' in data and isinstance(data['type'], str):
                    try:
                        data['type'] = InsightType[data['type']]
                    except KeyError: # pragma: no cover
                        print(f"LearningAgent: Warning - Invalid InsightType string '{data['type']}' in loaded data. Skipping insight: {data.get('insight_id')}")
                        continue

                required_fields = ['insight_id', 'type', 'description', 'source_reflection_entry_ids']
                if not all(field_name in data for field_name in required_fields): # pragma: no cover
                     print(f"LearningAgent: Warning - Missing required fields in loaded insight data {data.get('insight_id', '')}. Skipping insight.")
                     continue

                self.insights.append(ActionableInsight(**data))
                loaded_count += 1
            except Exception as e: # pragma: no cover
                print(f"LearningAgent: Error deserializing insight data: '{str(data)[:100]}...'. Error: {e}. Skipping.")
        print(f"LearningAgent: Loaded {loaded_count} actionable insights from '{self.insights_filepath}'.")
        if not self.insights and insights_data: # pragma: no cover
             print(f"LearningAgent: Warning - Insights data file '{self.insights_filepath}' was not empty, but no valid insights were loaded. File might be corrupted or in an old format.")

    def _save_insights(self):
        print(f"LearningAgent: Saving {len(self.insights)} insights to '{self.insights_filepath}'...")
        insights_as_dicts = []
        for insight in self.insights:
            insight_dict = asdict(insight)
            insight_dict['type'] = insight.type.name
            insights_as_dicts.append(insight_dict)

        if save_actionable_insights(insights_as_dicts, filepath=self.insights_filepath):
            print("LearningAgent: Successfully saved insights.")
        else: # pragma: no cover
            print("LearningAgent: Failed to save insights.")

    def add_insight(self, insight: ActionableInsight) -> bool:
        """Adds and persists an insight if it is not already present."""
        if any(existing.insight_id == insight.insight_id for existing in self.insights):
            return False

        for existing in self.insights:
            if (
                existing.type == insight.type
                and existing.description == insight.description
                and existing.related_tool_name == insight.related_tool_name
            ):
                return False

        self.insights.append(insight)
        self._save_insights()
        return True

    def _supersede_stale_failure_insights(self) -> int:
        """Keep historical failures while preventing repairs based on obsolete runtime state."""
        superseded_count = 0
        for insight in self.insights:
            if insight.type not in {InsightType.TOOL_BUG_SUSPECTED, InsightType.SELF_CORRECTION_FAILURE}:
                continue
            annotate_failure_metadata(insight.metadata, insight.creation_timestamp)
            if (
                insight.status != SUPERSEDED_FAILURE_STATUS
                and is_failure_stale(insight.metadata, insight.creation_timestamp)
            ):
                insight.status = SUPERSEDED_FAILURE_STATUS
                insight.metadata["superseded_reason"] = "Runtime source or configuration changed after this failure was recorded."
                insight.metadata["superseded_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                superseded_count += 1
        if superseded_count:
            self._save_insights()
        return superseded_count

    def ingest_reflection_suggestions(self, suggestions: List[Dict[str, Any]]) -> int:
        """
        Ingests improvement suggestions from the autonomous reflection cycle.
        Converts approved suggestions into ActionableInsights for execution.
        """
        count = 0
        for suggestion in suggestions:
            # Only ingest approved suggestions (and ensure they have a Score)
            if not suggestion.get("review_looks_good"):
                continue
            
            # Additional filter: High confidence only
            if suggestion.get("reviewer_confidence", 0.0) < 0.8:
                continue

            # Check if already ingested (duplicate check by ID)
            s_id = suggestion.get("suggestion_id")
            if any(i.metadata.get("source_suggestion_id") == s_id for i in self.insights):
                continue

            action_type = suggestion.get("action_type")
            insight_type = InsightType.TOOL_ENHANCEMENT_SUGGESTED
            
            # Map action types to InsightTypes
            # Map action types to InsightTypes
            if action_type == "MODIFY_TOOL_CODE" or action_type == "CORE_SYSTEM_MODIFICATION":
                 insight_type = InsightType.TOOL_BUG_SUSPECTED # Treat code mods as high priority bugs/fixes
            elif action_type == "CREATE_NEW_TOOL":
                insight_type = InsightType.NEW_TOOL_SUGGESTED

            # Map fields
            details = suggestion.get("action_details", {})
            
            # Restore source reflection IDs
            source_ref_id = suggestion.get("source_reflection_id")
            source_ids = [source_ref_id] if source_ref_id else []

            metadata = {
                "source_suggestion_id": s_id,
                "action_type_from_reflection": action_type,
                "module_path": details.get("module_path"),
                "function_name": details.get("function_name"),
                "reviewer_confidence": suggestion.get("reviewer_confidence"),
                # Critical for testing: Restore the original log entry ID if it was preserved in details
                "original_reflection_entry_ref_id": details.get("original_reflection_entry_ref_id") 
            }
            
            # Determine priority
            # Core system mods approved by user are high priority
            priority = 3
            if action_type == "CORE_SYSTEM_MODIFICATION":
                priority = 5

            new_insight = ActionableInsight(
                type=insight_type,
                description=suggestion.get("suggestion_text", "No description"),
                source_reflection_entry_ids=source_ids, 
                related_tool_name=details.get("tool_name") or details.get("function_name"),
                suggested_code_change=details.get("suggested_code_change"),
                new_tool_requirements=details.get("tool_description_prompt") if action_type == "CREATE_NEW_TOOL" else None,
                priority=priority,
                status="NEW",
                metadata=metadata
            )
            self.insights.append(new_insight)
            count += 1
        
        if count > 0:
            self._save_insights()
            print(f"LearningAgent: Ingested {count} suggestions from reflection cycle.")
        return count

    async def _analyze_root_cause(self, tool_name: str, code: str, error_context: str) -> str:
        """
        Uses the LLM to analyze the source code and error to determine the root cause.
        """
        prompt = f"""
You are an expert code debugger.
The tool `{tool_name}` failed with the following error:
{error_context}

Here is the source code for the tool:
```python
{code}
```

Analyze the code and the error to determine the specific root cause.
Explain EXACTLY why the error occurred based on the code logic.
Be concise and specific (e.g., "Line 45 assumes `x` is a list, but it is None because...").
Do not provide a full fix, just the diagnosis.
"""
        response = await invoke_gemini_model_async(
            prompt=f"Analyzing root cause for {tool_name}\n\n{prompt}",
            temperature=0.0,
            task_name="root_cause_analysis"
        )
        return response if response else "Could not generate root cause analysis."

    async def _deduce_tool_from_description(self, description: str) -> Optional[str]:
        """
        Uses the LLM to deduce the most likely tool name from the insight description.
        """
        import json
        
        # Hardcoded aliases for common hallucinations/guesses
        KNOWN_TOOL_ALIASES = {
            "tool_code_generator": "generate_new_tool_from_description",
            "tool_creator": "generate_new_tool_from_description",
            "create_tool": "generate_new_tool_from_description",
            "modify_tool": "stage_agent_tool_modification",
            "tool_modifier": "stage_agent_tool_modification",
            "search_web": "google_search",
            "web_search": "google_search",
            "google_search": "google_search"
        }
        
        # System Action Types that should NEVER be identified as tools
        SYSTEM_ACTION_TYPES = {
            "PROPOSE_TOOL_MODIFICATION", "ADD_LEARNED_FACT", "REVIEW_MANUALLY", 
            "APPLY_ARCHITECT_PROPOSAL", "EXECUTE_ACTION", "TASK_FAILED_UNKNOWN"
        }
        
        json_prompt = f"""
You are an intelligent system assistant.
An actionable insight has been generated, but the specific tool it refers to is missing.
Based on the description below, identify the exact name of the tool closest to the issue.

Insight Description:
"{description}"

Output a valid JSON object with a single key 'tool_name'.
Example: {{ "tool_name": "get_weather" }}
If no tool is clearly referenced, set 'tool_name' to null.

CRITICAL: Do NOT return internal system action names (like "PROPOSE_TOOL_MODIFICATION", "ADD_LEARNED_FACT") as the tool name. Only return registered agent tools.
"""
        response = await self.action_executor.code_service.llm_provider.invoke_ollama_model_async(json_prompt, temperature=0.0)
        
        deduced_name = None
        if response:
            try:
                # Try to find JSON in the response (it might be wrapped in ```json ... ```)
                cleaned_response = response.strip()
                if "```" in cleaned_response:
                     parts = cleaned_response.split("```")
                     if len(parts) > 1:
                        cleaned_response = parts[1]
                        if cleaned_response.startswith("json"):
                            cleaned_response = cleaned_response[4:]
                
                data = json.loads(cleaned_response.strip())
                deduced_name = data.get("tool_name")
            except Exception as e:
                print(f"Error parsing tool deduction JSON: {e}")
                pass
        
        if deduced_name:
            # 0. Check Blacklist
            if deduced_name in SYSTEM_ACTION_TYPES:
                print(f"LearningAgent: Deduced name '{deduced_name}' is a System Action Type, not a tool. Discarding.")
                return None

            # 1. Check Aliases
            if deduced_name in KNOWN_TOOL_ALIASES:
                print(f"LearningAgent: Mapped deduced tool '{deduced_name}' to alias '{KNOWN_TOOL_ALIASES[deduced_name]}'.")
                deduced_name = KNOWN_TOOL_ALIASES[deduced_name]
            
            # 2. Validate against Registry
            tool_info = get_tool(deduced_name)
            if tool_info:
                return deduced_name
            else:
                print(f"LearningAgent: Deduced tool '{deduced_name}' does not exist in registry. Discarding.")
                return None

        return None

    async def process_reflection_entry(self, entry: ReflectionLogEntry) -> Optional[ActionableInsight]:
        # Use the new unique entry_id from ReflectionLogEntry
        source_entry_ref_id = entry.entry_id # NEW WAY
        generated_insight: Optional[ActionableInsight] = None
        metadata_for_insight: Dict[str, Any] = {}

        # Store original_reflection_entry_ref_id in metadata for PROPOSE_TOOL_MODIFICATION
        # This will be used by ActionExecutor to find the original failing plan for re-testing
        metadata_for_insight["original_reflection_entry_ref_id"] = source_entry_ref_id
        metadata_for_insight["runtime_revision_at_failure"] = entry.runtime_revision_at_failure
        annotate_failure_metadata(metadata_for_insight, entry.timestamp)

        if entry.status in ["FAILURE", "PARTIAL_SUCCESS"] and is_failure_stale(
            metadata_for_insight, entry.timestamp
        ):
            print(f"LearningAgent: Skipping stale historical failure entry {entry.entry_id}; runtime changed after it was recorded.")
            return None


        # Check specifically for user-rejected insights for this tool
        if entry.status in ["FAILURE", "PARTIAL_SUCCESS"] and entry.error_type:
            
            # Heuristic: Check if we are trying to fix something the user said is fixed.
            # We need the tool name first, which is extracted below. 
            # So we will insert the check after tool name extraction.
            pass

        if entry.status in ["FAILURE", "PARTIAL_SUCCESS"] and entry.error_type:
            description = f"Tool execution failed or partially failed for goal '{entry.goal_description}'. Error: {entry.error_type} - {entry.error_message}."
            related_tool_name = None
            insight_type_to_use = InsightType.TOOL_BUG_SUSPECTED

            if entry.plan and entry.execution_results and len(entry.plan) == len(entry.execution_results):
                for i, result in enumerate(entry.execution_results):
                    is_error = False
                    if isinstance(result, dict) and result.get("_is_error_representation_"):
                        is_error = True
                    elif isinstance(result, Exception):
                        is_error = True

                    if is_error:
                        if entry.plan[i] and isinstance(entry.plan[i], dict):
                            failed_step_details = entry.plan[i]
                            related_tool_name = failed_step_details.get("tool_name")
                            
                            # --- NEW: Check for ModuleNotFoundError ---
                            error_msg = str(entry.error_message) if entry.error_message else ""
                            traceback_str = str(entry.traceback_snippet) if entry.traceback_snippet else ""
                            full_error_context = error_msg + " " + traceback_str
                            
                            import re
                            # Regex to capture "No module named 'xyz'"
                            module_match = re.search(r"No module named ['\"]([^'\"]+)['\"]", full_error_context)
                            
                            if module_match:
                                missing_module = module_match.group(1)
                                # Create a specific DEPENDENCY_MISSING insight/action
                                print(f"LearningAgent: Detected missing dependency '{missing_module}' for tool '{related_tool_name}'.")
                                
                                description = f"Tool '{related_tool_name}' failed due to missing Python dependency: '{missing_module}'. Suggesting installation via tool."
                                
                                metadata_for_insight["missing_dependency"] = missing_module
                                metadata_for_insight["suggested_tool_call"] = {
                                    "tool_name": "install_python_package",
                                    "args": {"package_name": missing_module}
                                }
                                
                                # We can treat this as a TOOL_BUG_SUSPECTED but with specific remediation data
                                # The ActionExecutor needs to know how to handle this.
                                insight_type_to_use = InsightType.TOOL_BUG_SUSPECTED 
                                metadata_for_insight["error_category"] = "DEPENDENCY_ERROR"
                            
                            elif related_tool_name in ["subtract_numbers", "echo_message"]:
                                metadata_for_insight["module_path"] = "ai_assistant.custom_tools.my_extra_tools"
                                metadata_for_insight["function_name"] = related_tool_name
                            elif failed_step_details.get("module_path") and failed_step_details.get("function_name_in_module"): # pragma: no cover
                                metadata_for_insight["module_path"] = failed_step_details.get("module_path")
                                metadata_for_insight["function_name"] = failed_step_details.get("function_name_in_module")

                            if not failed_step_details.get("args") and not failed_step_details.get("kwargs") and metadata_for_insight.get("error_category") != "DEPENDENCY_ERROR":
                                insight_type_to_use = InsightType.TOOL_USAGE_ERROR
                                description += f" The tool '{related_tool_name}' was called without arguments, suggesting a usage error."
                            if related_tool_name:
                                description += f" The failure occurred at the step involving tool '{related_tool_name}'."
                                # --- Deduplication/Rejection Check ---
                                # Check if we have recently failed to fix this tool or if usage was rejected.
                                for existing_insight in self.insights:
                                    if existing_insight.related_tool_name == related_tool_name:
                                        if existing_insight.status in ["REJECTED_BY_USER", "BLOCKED_BY_COUNCIL"]:
                                            # Found a previous REJECTION for this tool.
                                            # This means the user or system policy explicitly blocked it. Do not retry.
                                            print(f"LearningAgent: Skipping insight creation for '{related_tool_name}' because a previous attempt ({existing_insight.insight_id}) was BLOCKED/REJECTED ({existing_insight.status}).")
                                            return None
                                        
                                        # Note: We ALLOW retries for SELF_HEALING_FAILED or ACTION_FAILED per user request,
                                        # assuming the new insight might lead to a better fix.
                                        
                                        # Also deduplicate pending insights
                                        if existing_insight.status in ["NEW", "PENDING", "PENDING_MANUAL_REVIEW", "PROCESSING_SELF_HEALING", "SELF_HEALING_PROPOSED"]:
                                             print(f"LearningAgent: Skipping insight creation for '{related_tool_name}' because a similar insight ({existing_insight.insight_id}) is already pending/proposed.")
                                             return None

                            break

            if related_tool_name:
                # Fallback: If module_path is missing, try to look it up in the ToolRegistry
                if "module_path" not in metadata_for_insight:
                    tool_info = get_tool(related_tool_name)
                    if tool_info:
                        metadata_for_insight["module_path"] = tool_info.get("module_path")
                        metadata_for_insight["function_name"] = tool_info.get("function_name")
                        print(f"LearningAgent: Resolved module path for '{related_tool_name}' via ToolRegistry: {metadata_for_insight['module_path']}")

                # --- REGRESSION CHECK ---
                if self.memory_manager:
                    # Check for recent User Rejections matches
                    rejected_insights = [i for i in self.insights if i.status == "REJECTED_BY_USER" and i.related_tool_name == related_tool_name]
                    if rejected_insights:
                        print(f"LearningAgent: SKIPPING insight generation for '{related_tool_name}'. User recently rejected fixes for this tool.")
                        return None
                # ------------------------

                # Root Cause Analysis
                if "module_path" in metadata_for_insight and "function_name" in metadata_for_insight:
                    try:
                        code = self_modification.get_function_source_code(metadata_for_insight["module_path"], metadata_for_insight["function_name"])
                        if code:
                            error_ctx = f"Error: {entry.error_type} - {entry.error_message}"
                            print(f"LearningAgent: Analyzing root cause for failure in {related_tool_name}...")
                            analysis = await self._analyze_root_cause(related_tool_name, code, error_ctx)
                            description += f"\n\nROOT CAUSE ANALYSIS:\n{analysis}"
                            metadata_for_insight["root_cause_analysis"] = analysis
                    except Exception as e:
                        print(f"LearningAgent: Failed to perform root cause analysis: {e}")

                generated_insight = ActionableInsight(
                    type=insight_type_to_use,
                    description=description,
                    source_reflection_entry_ids=[source_entry_ref_id], # Use the new entry_id
                    related_tool_name=related_tool_name,
                    priority=3,
                    metadata=metadata_for_insight
                )
                print(f"LearningAgent: Generated insight: {generated_insight.insight_id} for tool {related_tool_name} due to failure.")
            else:
                generated_insight = ActionableInsight(
                    type=InsightType.TOOL_BUG_SUSPECTED,
                    description=f"A failure occurred for goal '{entry.goal_description}' (Error: {entry.error_type}) but could not be attributed to a specific tool in the plan. Manual review might be needed.",
                    source_reflection_entry_ids=[source_entry_ref_id], # Use the new entry_id
                    priority=4,
                    metadata=metadata_for_insight
                )
                print(f"LearningAgent: Generated general failure insight: {generated_insight.insight_id}.")

        elif entry.status == "SUCCESS" and entry.notes and "retry" in entry.notes.lower():
            description = f"Goal '{entry.goal_description}' succeeded after retries. This might indicate transient issues or sensitivity in the involved tools."
            related_tool_name = None
            if entry.plan and len(entry.plan) == 1 and isinstance(entry.plan[0], dict):
                related_tool_name = entry.plan[0].get("tool_name")
                if related_tool_name in ["subtract_numbers", "echo_message"]:
                    metadata_for_insight["module_path"] = "ai_assistant.custom_tools.my_extra_tools"
                    metadata_for_insight["function_name"] = related_tool_name

            generated_insight = ActionableInsight(
                type=InsightType.TOOL_ENHANCEMENT_SUGGESTED,
                description=description,
                source_reflection_entry_ids=[source_entry_ref_id], # Use the new entry_id
                related_tool_name=related_tool_name,
                priority=7,
                suggested_tool_description="Consider reviewing tool for robustness against transient errors or improving error handling if retries were involved.",
                metadata=metadata_for_insight
            )
            print(f"LearningAgent: Generated insight for success after retry: {generated_insight.insight_id}")

        if generated_insight:
            self.insights.append(generated_insight)
            self._save_insights()
            return generated_insight

        return None

    async def review_and_propose_next_action(self) -> Optional[Tuple[Dict[str, Any], bool]]:
        self._supersede_stale_failure_insights()
        actionable_new_insights = [insight for insight in self.insights if insight.status == "NEW"]
        if not actionable_new_insights:
            # print("LearningAgent: No new actionable insights to review.")
            return None
        actionable_new_insights.sort(key=lambda insight: (insight.priority, insight.creation_timestamp))
        selected_insight = actionable_new_insights[0]

        print(f"LearningAgent: Selected insight for action: {selected_insight.insight_id} (Priority: {selected_insight.priority}, Type: {selected_insight.type.name})")
        print(f"LearningAgent: Description: {selected_insight.description}")

        proposed_action = {
            "source_insight_id": selected_insight.insight_id,
            "action_type": "TBD", "details": {}
        }

        if selected_insight.type == InsightType.TOOL_BUG_SUSPECTED or selected_insight.type == InsightType.TOOL_ENHANCEMENT_SUGGESTED or selected_insight.type == InsightType.HYPOTHETICAL_SCENARIO:
            # Check for Caller/usage errors first
            desc_lower = selected_insight.description.lower()
            is_caller_error = "takes" in desc_lower and "arguments but" in desc_lower and "given" in desc_lower
            is_keyword_error = "unexpected keyword argument" in desc_lower

            if is_caller_error or is_keyword_error:
                 print(f"LearningAgent: Insight {selected_insight.insight_id} detected as CALLER ERROR (invalid usage), not tool bug.")
                 # Convert to planning heuristic or manual review
                 proposed_action["action_type"] = "ADD_PLANNING_HEURISTIC" # Or REVIEW_MANUALLY if not implemented
                 proposed_action["details"] = {
                     "heuristic": f"When using tool '{selected_insight.related_tool_name}', ensure you pass the correct arguments. Error context: {selected_insight.description}",
                     "trigger_context": f"tool_usage error ({selected_insight.related_tool_name})"
                 }
                 # Change type to persist this shift
                 selected_insight.type = InsightType.PLANNING_HEURISTIC_SUGGESTION
                 self._save_insights()

            elif selected_insight.related_tool_name:
                # JIT Fix for existing insights with missing metadata
                if "module_path" not in selected_insight.metadata:
                     tool_info = get_tool(selected_insight.related_tool_name)
                     if tool_info:
                         selected_insight.metadata["module_path"] = tool_info.get("module_path")
                         selected_insight.metadata["function_name"] = tool_info.get("function_name")
                         print(f"LearningAgent: JIT resolved module path for '{selected_insight.related_tool_name}' in existing insight: {selected_insight.metadata['module_path']}")
                         self._save_insights() # Persist the fix

                proposed_action["action_type"] = "PROPOSE_TOOL_MODIFICATION"
                proposed_action["details"] = {
                    "module_path": selected_insight.metadata.get("module_path"),
                    "function_name": selected_insight.metadata.get("function_name"),
                    "tool_name": selected_insight.related_tool_name,
                    "suggested_change_description": selected_insight.description,
                    "suggested_code_change": selected_insight.suggested_code_change,
                    "reason": f"Based on insight {selected_insight.insight_id}",
                    "original_reflection_entry_ref_id": selected_insight.source_reflection_entry_ids[0] if selected_insight.source_reflection_entry_ids else None
                }
            else: # pragma: no cover
                proposed_action["action_type"] = "REVIEW_MANUALLY"

        elif selected_insight.type == InsightType.KNOWLEDGE_GAP_IDENTIFIED:
            if selected_insight.knowledge_to_learn:
                proposed_action["action_type"] = "ADD_LEARNED_FACT"
                proposed_action["details"] = {
                    "fact_to_learn": selected_insight.knowledge_to_learn,
                    "source": f"Based on insight {selected_insight.insight_id}"
                }
            else:
                 proposed_action["action_type"] = "REVIEW_MANUALLY"

        elif selected_insight.type == InsightType.LEARNED_FACT: # NEW - Auto-add permanent facts
             proposed_action["action_type"] = "ADD_LEARNED_FACT"
             proposed_action["details"] = {
                 "fact_to_learn": selected_insight.description,
                 "source": f"Conversational Insight {selected_insight.insight_id}",
                 "permanence": "permanent" # User facts are permanent
             }

        elif selected_insight.type == InsightType.USER_FRUSTRATION or selected_insight.type == InsightType.USER_PREFERENCE_LEARNED:
             # Convert frustations/preferences into Planning Heuristics
             proposed_action["action_type"] = "ADD_PLANNING_HEURISTIC"
             proposed_action["details"] = {
                 "heuristic": f"User Preference/Feedback: {selected_insight.description}",
                 "trigger_context": "always_active", # specific context requires advanced parsing, default to general
                 "source": f"Derived from {selected_insight.type.name} (ID: {selected_insight.insight_id})"
             }
        
        elif selected_insight.type == InsightType.PLANNING_HEURISTIC_SUGGESTION:
             proposed_action["action_type"] = "ADD_PLANNING_HEURISTIC"
             details = selected_insight.planning_heuristic_details or {}
             proposed_action["details"] = {
                 "heuristic": details.get("suggestion") or selected_insight.description,
                 "trigger_context": "general_planning",
                 "source": f"Insight {selected_insight.insight_id}"
             }

        else:
             print(f"LearningAgent: Insight type {selected_insight.type.name} not handled autonomously. Defaulting to MANUAL REVIEW.")
             proposed_action["action_type"] = "REVIEW_MANUALLY"
        if proposed_action["action_type"] == "REVIEW_MANUALLY" or proposed_action["action_type"] == "TBD":
            selected_insight.status = "PENDING_MANUAL_REVIEW"
            selected_insight.metadata["review_reason"] = f"Action type was {proposed_action['action_type']}."
        else:
            selected_insight.status = "ACTION_ATTEMPTED"
            selected_insight.metadata["action_attempt_timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            try:
                # Pass permanence if ActionExecutor supports it (it just delegates to memory manager usually)
                # We need to verify ActionExecutor supports 'permanence' in ADD_LEARNED_FACT
                execution_success = await self.action_executor.execute_action(proposed_action)
                if execution_success: selected_insight.status = "ACTION_SUCCESSFUL"
                else: selected_insight.status = "ACTION_FAILED"
                selected_insight.metadata[f"action_{selected_insight.status.lower()}_timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            except Exception as e: # pragma: no cover
                selected_insight.status = "ACTION_EXCEPTION"
                selected_insight.metadata["action_exception_timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                selected_insight.metadata["exception_details"] = str(e)
                execution_success = False
        self._save_insights()
        return proposed_action, execution_success

    async def process_learned_facts_immediately(self) -> int:
        """
        Scans for 'LEARNED_FACT' insights and executes them immediately.
        This bypasses the slow 'review_and_propose' cycle for safe, permanent knowledge.
        Returns the number of facts processed.
        """
        fact_insights = [
            insight for insight in self.insights 
            if insight.type == InsightType.LEARNED_FACT and insight.status == "NEW"
        ]
        
        if not fact_insights:
            return 0
            
        print(f"LearningAgent: Fast-tracking {len(fact_insights)} learned facts...")
        processed_count = 0
        
        for insight in fact_insights:
            # Construct Action
            action = {
                "action_type": "ADD_LEARNED_FACT",
                "details": {
                    "fact_to_learn": insight.description,
                    "source": f"Conversational Insight {insight.insight_id}",
                    "permanence": "permanent"
                }
            }
            
            # Execute
            insight.status = "PROCESSING"
            insight.metadata["action_attempt_timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            
            try:
                success = await self.action_executor.execute_action(action)
                if success:
                    insight.status = "ACTION_SUCCESSFUL"
                    processed_count += 1
                else:
                    insight.status = "ACTION_FAILED"
            except Exception as e:
                insight.status = "ACTION_EXCEPTION"
                insight.metadata["error"] = str(e)
                
            insight.metadata["action_end_timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            
        if processed_count > 0:
            self._save_insights()
            
        return processed_count

    async def execute_self_healing_for_insight(self, insight: ActionableInsight, apply_immediately: bool = False) -> bool:
        """
        Executes self-healing action for a single insight.
        Args:
            sight: The insight to process.
            apply_immediately: If True, disables staging mode (verification-only) and applies the fix if verified.
        """
        print(f"LearningAgent: Processing insight {insight.insight_id} for self-healing (apply_immediately={apply_immediately}).")
        annotate_failure_metadata(insight.metadata, insight.creation_timestamp)
        if is_failure_stale(insight.metadata, insight.creation_timestamp):
            insight.status = SUPERSEDED_FAILURE_STATUS
            insight.metadata["superseded_reason"] = "Runtime source or configuration changed after this failure was recorded."
            insight.metadata["superseded_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._save_insights()
            print(f"LearningAgent: Skipping stale historical insight {insight.insight_id}; runtime changed after the failure.")
            return False

        # Construct the action
        action = {}

        # 1. Check for PRE-DEFINED TOOL CALL (e.g., dependency fix)
        if "suggested_tool_call" in insight.metadata:
            tool_call_info = insight.metadata["suggested_tool_call"]
            print(f"LearningAgent: Insight {insight.insight_id} suggests specific tool call: {tool_call_info.get('tool_name')}")
            action = {
                "action_type": "EXECUTE_SUGGESTED_TOOL",
                "source_insight_id": insight.insight_id,
                "details": {
                    "tool_name": tool_call_info.get("tool_name"),
                    "args": tool_call_info.get("args", {}),
                    "reason": insight.description
                }
            }
        
        # 2. Standard Self-Correction (Code Modification) logic
        else:
            # JIT Fix for existing insights with missing metadata (Self-Healing Context)
            if not insight.related_tool_name:
                 print(f"LearningAgent: Insight {insight.insight_id} missing tool name. Attempting deduction...")
                 deduced_name = await self._deduce_tool_from_description(insight.description)
                 if deduced_name:
                     print(f"LearningAgent: Deduced tool name '{deduced_name}' from description.")
                     insight.related_tool_name = deduced_name
                     self._save_insights()
                 else:
                     print(f"LearningAgent: Cannot execute self-healing for insight {insight.insight_id} - No related_tool_name and deduction failed.")
                     insight.status = "SELF_HEALING_SKIPPED_NO_TOOL"
                     insight.metadata["self_healing_skip_reason"] = "No related_tool_name and deduction failed"
                     self._save_insights()
                     return False

            tool_info = get_tool(insight.related_tool_name)
            if tool_info:
                registry_module_path = tool_info.get("module_path")
                registry_function_name = tool_info.get("function_name")
                
                # Robustness: Always prefer registry path if available
                if "module_path" in insight.metadata and insight.metadata["module_path"] != registry_module_path:
                    # check if metadata path matches registry path to avoid warning spam if identical
                    pass 
                
                insight.metadata["module_path"] = registry_module_path
                insight.metadata["function_name"] = registry_function_name
                self._save_insights()
            else:
                print(f"LearningAgent: Tool '{insight.related_tool_name}' not found in registry. Attempting deduction from description...")
                deduced_name = await self._deduce_tool_from_description(insight.description)
                if deduced_name:
                    print(f"LearningAgent: Deduced correct tool name '{deduced_name}' (was '{insight.related_tool_name}').")
                    insight.related_tool_name = deduced_name
                    tool_info_deduced = get_tool(deduced_name)
                    if tool_info_deduced:
                        insight.metadata["module_path"] = tool_info_deduced.get("module_path")
                        insight.metadata["function_name"] = tool_info_deduced.get("function_name")
                        self._save_insights()
                    else:
                         print(f"LearningAgent: Deduced name '{deduced_name}' also not found in registry. Skipping.")
                         insight.status = "SELF_HEALING_SKIPPED_METADATA"
                         self._save_insights()
                         return False
                else:
                    print(f"LearningAgent: Could not resolve module path for '{insight.related_tool_name}' in self-healing. Skipping.")
                    insight.status = "SELF_HEALING_SKIPPED_METADATA"
                    self._save_insights()
                    return False

            # Construct the PROPOSE_TOOL_MODIFICATION action
            action = {
                "source_insight_id": insight.insight_id,
                "action_type": "PROPOSE_TOOL_MODIFICATION",
                "details": {
                    "module_path": insight.metadata.get("module_path"),
                    "function_name": insight.metadata.get("function_name"),
                    "tool_name": insight.related_tool_name,
                    "suggested_change_description": insight.description,
                    "suggested_code_change": insight.suggested_code_change, # Likely None, will trigger generation
                    "reason": f"Self-healing trigger from insight {insight.insight_id}",
                    "original_reflection_entry_ref_id": insight.source_reflection_entry_ids[0] if insight.source_reflection_entry_ids else None,
                    "staging_mode": not apply_immediately # Critical flag: True = Evaluate & Revert; False = Evaluate & Keep
                }
            }

        # Update status *before* execution to avoid repeated processing if crash
        insight.status = "PROCESSING_SELF_HEALING"
        insight.metadata["self_healing_start"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_insights()

        success = False
        try:
            success = await self.action_executor.execute_action(action)
            if success:
                insight.status = "SELF_HEALING_PROPOSED" # Indicates a suggestion was created
            else:
                insight.status = "SELF_HEALING_FAILED"
        except Exception as e:
            print(f"LearningAgent: Error during self-healing for {insight.insight_id}: {e}")
            insight.status = "SELF_HEALING_EXCEPTION"
            insight.metadata["exception"] = str(e)

        insight.metadata["self_healing_end"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._save_insights()
        return success

    async def execute_architect_proposal(self, proposal: Dict[str, Any]) -> bool:
        """
        Executes an Evolutionary Architect proposal.
        """
        target_file = proposal.get('target_file')
        summary = proposal.get('proposal', {}).get('summary', 'No summary')
        plan = proposal.get('proposal', {}).get('plan', 'No plan')

        print(f"LearningAgent: Executing architect proposal for {target_file}")
        
        action = {
             "action_type": "APPLY_ARCHITECT_PROPOSAL",
             "details": {
                 "target_file": target_file,
                 "proposal_summary": summary,
                 "proposal_plan": plan
             }
        }
        
        try:
            return await self.action_executor.execute_action(action)
        except Exception as e:
             print(f"LearningAgent: Error executing architect proposal: {e}")
             return False

    async def process_self_healing_insights(self) -> int:
        """
        Scans for 'TOOL_BUG_SUSPECTED' insights and attempts to generate fixes
        using the ActionExecutor in 'staging_mode'.
        Returns the number of insights processed.
        """
        self._supersede_stale_failure_insights()
        # Filter for relevant insights
        bug_insights = [
            insight for insight in self.insights
            if insight.type == InsightType.TOOL_BUG_SUSPECTED
            and insight.status == "NEW"
            and insight.related_tool_name # Must have a tool target
        ]

        if not bug_insights:
            return 0

        print(f"LearningAgent: Found {len(bug_insights)} bug insights for self-healing.")

        processed_count = 0
        for insight in bug_insights:
            await self.execute_self_healing_for_insight(insight)
            processed_count += 1
        
        return processed_count

    async def extract_and_save_facts(self, text: str, source_desc: str = "Background Agent Report") -> int:
        """
        Analyzes the provided text to extract facts, determining their permanence.
        - Permanent: "Project uses Flask", "User's son is Thomas".
        - Transient: "Build checks failed", "Server is down".
        Saves these facts to the MemoryManager.
        """
        if not text or len(text) < 50:
            print("LearningAgent: Text too short for fact extraction. Skipping.")
            return 0
            
        print(f"LearningAgent: Extracting facts from '{source_desc}'...")
        
        prompt = f"""
You are the Memory Curator for an AI system.
Analyze the following text and extract facts.
For each fact, determine if it is:
- "permanent": Enduring truths (User bio, Tech stack, Design decisions).
- "transient": Temporary state (Current errors, Task status, Recent logs).

Text:
"{text}"

Output a JSON object with a key "facts". unique items only.
Each item must be object: {{ "text": "...", "type": "permanent" | "transient" }}

Example:
{{
  "facts": [
    {{ "text": "Project uses Flask.", "type": "permanent" }},
    {{ "text": "Test suite failed on line 50.", "type": "transient" }}
  ]
}}
        """

        try:
            # specialized model for extraction if available, otherwise default
            response = await invoke_gemini_model_async(
                prompt=f"Fact Extraction\n\n{prompt}",
                task_name="fact_extraction",
            )
            
            import json
            import re
            
            facts = []
            # Robust parsing
            if response:
                json_str = response
                if "```" in response:
                    match = re.search(r"```(?:json)?(.*?)```", response, re.DOTALL)
                    if match:
                        json_str = match.group(1).strip()
                
                try:
                    data = json.loads(json_str)
                    facts = data.get("facts", [])
                except json.JSONDecodeError:
                    print("LearningAgent: JSON decode failed for fact extraction.")
            
            saved_count = 0
            if self.memory_manager and facts:
                for fact_item in facts:
                    # Robustness check for old/bad LLM format
                    if isinstance(fact_item, str):
                        fact_text = fact_item
                        p_type = "permanent" # Default to careful
                    else:
                        fact_text = fact_item.get("text")
                        p_type = fact_item.get("type", "permanent")

                    if not fact_text: continue

                    # Async save if possible, or use sync wrapper if needed.
                    await self.memory_manager.add_fact_with_rag(
                        text=fact_text,
                        category="learned_fact",
                        source=source_desc,
                        permanence=p_type
                    )
                    saved_count += 1
                    print(f"LearningAgent: Learned fact ({p_type}): {fact_text}")
            
            return saved_count

        except Exception as e:
            print(f"LearningAgent: Fact extraction failed: {e}")
            return 0


if __name__ == '__main__': # pragma: no cover
    # import uuid # uuid is already imported at the top of the module
    # Removed local MockReflectionLogEntry, will use the actual one.
    # from ai_assistant.core.reflection import ReflectionLogEntry # Already imported at the top


    async def run_learning_tests():
        test_insights_file = "test_actionable_insights.json"
        if os.path.exists(test_insights_file): os.remove(test_insights_file)

        custom_tools_dir = os.path.join("ai_assistant", "custom_tools")
        os.makedirs(custom_tools_dir, exist_ok=True)
        dummy_tool_path = os.path.join(custom_tools_dir, "my_extra_tools.py")
        if not os.path.exists(dummy_tool_path):
            with open(dummy_tool_path, "w") as f:
                f.write("def subtract_numbers(a: float, b: float) -> float:\n    return a - b\n")
                f.write("def echo_message(message: str) -> str:\n    return message\n")

        # Instantiate TaskManager for the test, or pass None
        # test_notification_manager should be instantiated if specific notification functionality is tested here.
        # For now, passing None as the primary tests are for learning logic, not notification side-effects.
        test_task_manager = TaskManager(notification_manager=None) # TaskManager now needs it
        test_notification_manager_for_agent = None

        agent = LearningAgent(
            insights_filepath=test_insights_file,
            task_manager=test_task_manager,
            notification_manager=test_notification_manager_for_agent
        )

        # Test process_reflection_entry correctly uses entry.entry_id
        # Use the actual ReflectionLogEntry
        mock_entry_for_processing = ReflectionLogEntry(
            goal_description="Test entry_id propagation",
            status="FAILURE",
            error_type="TestError",
            plan=[], # Required non-optional field
            execution_results=[] # Required non-optional field
        )
        processed_insight = await agent.process_reflection_entry(mock_entry_for_processing)
        assert processed_insight is not None
        assert len(processed_insight.source_reflection_entry_ids) == 1
        assert processed_insight.source_reflection_entry_ids[0] == mock_entry_for_processing.entry_id
        print(f"Verified insight source ID: {processed_insight.source_reflection_entry_ids[0]} matches entry ID: {mock_entry_for_processing.entry_id}")

        # Reset insights for review_and_propose_next_action tests
        agent.insights = []
        ts_now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Insight for ADD_LEARNED_FACT
        manual_add_fact_insight = ActionableInsight(
            insight_id="manual_af_002", type=InsightType.KNOWLEDGE_GAP_IDENTIFIED,
            description="Manual insight for testing add fact.",
            source_reflection_entry_ids=[str(uuid.uuid4())], # Give it a source ID
            knowledge_to_learn="Python is a dynamically-typed language.",
            priority=1, status="NEW", creation_timestamp=ts_now_iso
        )
        agent.insights.append(manual_add_fact_insight)

        # Insight for PROPOSE_TOOL_MODIFICATION
        manual_tool_mod_insight = ActionableInsight(
            insight_id="manual_ttm_001", type=InsightType.TOOL_BUG_SUSPECTED,
            description="Manual insight for testing tool modification proposal.",
            source_reflection_entry_ids=[str(uuid.uuid4())], # Give it a source ID
            related_tool_name="subtract_numbers", priority=2, status="NEW",
            creation_timestamp=(datetime.datetime.fromisoformat(ts_now_iso) + datetime.timedelta(seconds=1)).isoformat(),
            suggested_code_change="def subtract_numbers(a: float, b: float) -> float:\n    # Modified by test\n    return float(a) - float(b) - 1.0",
            metadata={
                "module_path": "ai_assistant.custom_tools.my_extra_tools",
                "function_name": "subtract_numbers",
                "original_reflection_entry_ref_id": str(uuid.uuid4()) # Mock original ref ID
            }
        )
        agent.insights.append(manual_tool_mod_insight)
        agent._save_insights()

        print("\n--- Testing review_and_propose_next_action (with entry_id logic) ---")

        action_result_tuple_1 = await agent.review_and_propose_next_action()
        if action_result_tuple_1:
            proposed_action_1, exec_success_1 = action_result_tuple_1
            print(f"Proposed Action 1: {proposed_action_1}")
            print(f"Execution Success 1: {exec_success_1}")
            acted_insight_1 = next((inst for inst in agent.insights if inst.insight_id == proposed_action_1.get("source_insight_id")), None)
            if acted_insight_1:
                print(f"Insight {acted_insight_1.insight_id} status is now {acted_insight_1.status}")
                assert acted_insight_1.status in ["ACTION_SUCCESSFUL", "ACTION_FAILED", "ACTION_EXCEPTION"], f"Unexpected status: {acted_insight_1.status}"

        action_result_tuple_2 = await agent.review_and_propose_next_action()
        if action_result_tuple_2:
            proposed_action_2, exec_success_2 = action_result_tuple_2
            print(f"Proposed Action 2: {proposed_action_2}")
            print(f"Execution Success 2: {exec_success_2}")
            acted_insight_2 = next((inst for inst in agent.insights if inst.insight_id == proposed_action_2.get("source_insight_id")), None)
            if acted_insight_2:
                print(f"Insight {acted_insight_2.insight_id} status is now {acted_insight_2.status}")
                assert acted_insight_2.status in ["ACTION_SUCCESSFUL", "ACTION_FAILED", "ACTION_EXCEPTION"], f"Unexpected status: {acted_insight_2.status}"

        action_result_tuple_3 = await agent.review_and_propose_next_action()
        assert action_result_tuple_3 is None, f"Expected no action on 3rd attempt, but got {action_result_tuple_3}"
        print("No action proposed on 3rd attempt, as expected.")

        if os.path.exists(test_insights_file):
            print(f"Test file {test_insights_file} can be manually inspected or removed.")

    asyncio.run(run_learning_tests())
