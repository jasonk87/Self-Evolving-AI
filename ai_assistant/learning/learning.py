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
from enum import Enum, auto
from dataclasses import dataclass, field, asdict
from ..core.task_manager import TaskManager
from ..core.notification_manager import NotificationManager # Made unconditional

from ai_assistant.core.reflection import ReflectionLogEntry
from ai_assistant.memory.persistent_memory import save_actionable_insights, load_actionable_insights, ACTIONABLE_INSIGHTS_FILEPATH
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.tools.tool_system import get_tool

class InsightType(Enum):
    TOOL_BUG_SUSPECTED = auto()
    TOOL_USAGE_ERROR = auto()
    TOOL_ENHANCEMENT_SUGGESTED = auto()
    NEW_TOOL_SUGGESTED = auto()
    KNOWLEDGE_GAP_IDENTIFIED = auto()
    LEARNED_FACT_CORRECTION = auto()
    PLANNING_HEURISTIC_SUGGESTION = auto()
    SELF_CORRECTION_SUCCESS = auto()
    SELF_CORRECTION_FAILURE = auto()

@dataclass
class ActionableInsight:
    type: InsightType
    description: str
    source_reflection_entry_ids: List[str]
    insight_id: Optional[str] = None
    related_tool_name: Optional[str] = None
    suggested_code_change: Optional[str] = None
    suggested_tool_description: Optional[str] = None
    new_tool_requirements: Optional[str] = None
    knowledge_to_learn: Optional[str] = None
    incorrect_fact_to_correct: Optional[str] = None
    corrected_fact: Optional[str] = None
    planning_heuristic_details: Optional[Dict[str, Any]] = None
    priority: int = 5
    status: str = "NEW"
    creation_timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.insight_id:
            # Generate a new UUID-based insight_id if not provided or empty
            self.insight_id = f"{self.type.name}_{uuid.uuid4().hex[:8]}"
class LearningAgent:
    def __init__(self, insights_filepath: Optional[str] = None,
                 task_manager: Optional[TaskManager] = None,
                 notification_manager: Optional[NotificationManager] = None): # Type hint updated
        self.insights: List[ActionableInsight] = []
        self.insights_filepath = insights_filepath if insights_filepath is not None else ACTIONABLE_INSIGHTS_FILEPATH
        self.task_manager = task_manager
        self.notification_manager = notification_manager # Store it
        self.action_executor = ActionExecutor(
            learning_agent=self,
            task_manager=self.task_manager,
            notification_manager=self.notification_manager # Pass it
        )
        self._load_insights()

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
            print(f"LearningAgent: Successfully saved insights.")
        else: # pragma: no cover
            print(f"LearningAgent: Failed to save insights.")

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
            if action_type == "MODIFY_TOOL_CODE":
                 insight_type = InsightType.TOOL_BUG_SUSPECTED # Treat code mods as high priority bugs/fixes
            elif action_type == "CREATE_NEW_TOOL":
                insight_type = InsightType.NEW_TOOL_SUGGESTED

            # Map fields
            details = suggestion.get("action_details", {})
            
            new_insight = ActionableInsight(
                type=insight_type,
                description=suggestion.get("suggestion_text", "No description"),
                source_reflection_entry_ids=[], 
                related_tool_name=details.get("tool_name") or details.get("function_name"),
                suggested_code_change=details.get("suggested_code_change"),
                new_tool_requirements=details.get("tool_description_prompt") if action_type == "CREATE_NEW_TOOL" else None,
                priority=3, # Default priority
                status="NEW",
                metadata={
                    "source_suggestion_id": s_id,
                    "action_type_from_reflection": action_type,
                    "module_path": details.get("module_path"),
                    "function_name": details.get("function_name"),
                    "reviewer_confidence": suggestion.get("reviewer_confidence")
                }
            )
            self.insights.append(new_insight)
            count += 1
        
        if count > 0:
            self._save_insights()
            print(f"LearningAgent: Ingested {count} suggestions from reflection cycle.")
        return count

    def process_reflection_entry(self, entry: ReflectionLogEntry) -> Optional[ActionableInsight]:
        # Use the new unique entry_id from ReflectionLogEntry
        source_entry_ref_id = entry.entry_id # NEW WAY
        generated_insight: Optional[ActionableInsight] = None
        metadata_for_insight: Dict[str, Any] = {}

        # Store original_reflection_entry_ref_id in metadata for PROPOSE_TOOL_MODIFICATION
        # This will be used by ActionExecutor to find the original failing plan for re-testing
        metadata_for_insight["original_reflection_entry_ref_id"] = source_entry_ref_id


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

                            if related_tool_name in ["subtract_numbers", "echo_message"]:
                                metadata_for_insight["module_path"] = "ai_assistant.custom_tools.my_extra_tools"
                                metadata_for_insight["function_name"] = related_tool_name
                            elif failed_step_details.get("module_path") and failed_step_details.get("function_name_in_module"): # pragma: no cover
                                metadata_for_insight["module_path"] = failed_step_details.get("module_path")
                                metadata_for_insight["function_name"] = failed_step_details.get("function_name_in_module")

                            if not failed_step_details.get("args") and not failed_step_details.get("kwargs"):
                                insight_type_to_use = InsightType.TOOL_USAGE_ERROR
                                description += f" The tool '{related_tool_name}' was called without arguments, suggesting a usage error."
                            else:
                                description += f" The failure occurred at the step involving tool '{related_tool_name}'."
                            break

            if related_tool_name:
                # Fallback: If module_path is missing, try to look it up in the ToolRegistry
                if "module_path" not in metadata_for_insight:
                    tool_info = get_tool(related_tool_name)
                    if tool_info:
                        metadata_for_insight["module_path"] = tool_info.get("module_path")
                        metadata_for_insight["function_name"] = tool_info.get("function_name")
                        print(f"LearningAgent: Resolved module path for '{related_tool_name}' via ToolRegistry: {metadata_for_insight['module_path']}")

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
        actionable_new_insights = [insight for insight in self.insights if insight.status == "NEW"]
        if not actionable_new_insights:
            print("LearningAgent: No new actionable insights to review.")
            return None
        actionable_new_insights.sort(key=lambda insight: (insight.priority, insight.creation_timestamp))
        selected_insight = actionable_new_insights[0]

        print(f"LearningAgent: Selected insight for action: {selected_insight.insight_id} (Priority: {selected_insight.priority}, Type: {selected_insight.type.name})")
        print(f"LearningAgent: Description: {selected_insight.description}")

        proposed_action = {
            "source_insight_id": selected_insight.insight_id,
            "action_type": "TBD", "details": {}
        }

        if selected_insight.type == InsightType.TOOL_BUG_SUSPECTED or selected_insight.type == InsightType.TOOL_ENHANCEMENT_SUGGESTED:
            if selected_insight.related_tool_name:
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
            else: # pragma: no cover
                proposed_action["action_type"] = "REVIEW_MANUALLY"
        else: # pragma: no cover
            proposed_action["action_type"] = "REVIEW_MANUALLY"

        execution_success = False
        if proposed_action["action_type"] == "REVIEW_MANUALLY" or proposed_action["action_type"] == "TBD":
            selected_insight.status = "PENDING_MANUAL_REVIEW"
            selected_insight.metadata["review_reason"] = f"Action type was {proposed_action['action_type']}."
        else:
            selected_insight.status = "ACTION_ATTEMPTED"
            selected_insight.metadata["action_attempt_timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            try:
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

    async def execute_self_healing_for_insight(self, insight: ActionableInsight) -> bool:
        """
        Executes self-healing action for a single insight.
        """
        print(f"LearningAgent: Processing insight {insight.insight_id} for self-healing (staging mode).")

        # Construct the action
        # JIT Fix for existing insights with missing metadata (Self-Healing Context)
        if not insight.related_tool_name:
             print(f"LearningAgent: Cannot execute self-healing for insight {insight.insight_id} - No related_tool_name.")
             insight.status = "SELF_HEALING_SKIPPED_NO_TOOL"
             insight.metadata["self_healing_skip_reason"] = "No related_tool_name"
             self._save_insights()
             return False

        if "module_path" not in insight.metadata:
             tool_info = get_tool(insight.related_tool_name)
             if tool_info:
                 insight.metadata["module_path"] = tool_info.get("module_path")
                 insight.metadata["function_name"] = tool_info.get("function_name")
                 print(f"LearningAgent: JIT resolved module path for '{insight.related_tool_name}' in self-healing: {insight.metadata['module_path']}")
                 self._save_insights()
             else:
                 print(f"LearningAgent: Could not resolve module path for '{insight.related_tool_name}' in self-healing. Skipping.")
                 insight.status = "SELF_HEALING_SKIPPED_METADATA"
                 self._save_insights()
                 return False

        # Construct the action
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
                "staging_mode": True # Critical flag for self-healing
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
        processed_insight = agent.process_reflection_entry(mock_entry_for_processing)
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
