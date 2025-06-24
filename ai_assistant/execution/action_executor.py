# ai_assistant/execution/action_executor.py
from typing import Dict, Any, Optional, Tuple, List, TYPE_CHECKING
import datetime 
import re
import asyncio
import os
import uuid
import logging

from ai_assistant.config import is_debug_mode
from ai_assistant.core import self_modification
from ..core.reflection import global_reflection_log, ReflectionLogEntry  # Add ReflectionLogEntry to import
from ai_assistant.memory.persistent_memory import load_learned_facts, save_learned_facts, LEARNED_FACTS_FILEPATH
from ai_assistant.core.suggestion_manager import mark_suggestion_implemented # Added import
import json # Added for parsing LLM response in _is_fact_valuable
from ai_assistant.planning.planning import PlannerAgent
from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.code_services.service import CodeService # Added
from ..core.task_manager import TaskManager, ActiveTaskType, ActiveTaskStatus # Added for TaskManager
from ..core.notification_manager import NotificationManager, NotificationType # Added
from datetime import timezone # Ensure timezone is available

if TYPE_CHECKING:
    from ai_assistant.learning.learning import LearningAgent # For type hinting only

logger = logging.getLogger(__name__)

LLM_FACT_VALUE_ASSESSMENT_PROMPT_TEMPLATE = """
You are an AI assistant's knowledge curator. Your task is to assess if a given fact is valuable to learn and store for future reference by a large language model AI assistant.
A valuable fact is typically:
1. Non-trivial: Not common sense or easily inferable.
2. Useful: Likely to aid in future tasks, reasoning, or conversations.
3. Factual: Represents a piece of information rather than an opinion or instruction (unless it's a user preference).
4. Concise: Stated clearly and briefly.
5. Novel: Not something the AI would already implicitly know or that is too generic.

Fact to assess: "{fact_to_assess}"

Based on these criteria, is this fact valuable for the AI assistant to learn and remember?
Respond with a single JSON object containing two keys:
- "is_valuable": boolean (true if valuable, false otherwise)
- "reason": string (a brief explanation for your decision, especially if not valuable)

Example for a valuable fact:
Fact: "The user's preferred programming language is Python."
Response: {{"is_valuable": true, "reason": "Stores a specific user preference that can tailor future interactions."}}

Example for a non-valuable fact (trivial):
Fact: "The sky is blue."
Response: {{"is_valuable": false, "reason": "This is common knowledge and too trivial to store."}}

Example for a non-valuable fact (instruction, not a fact):
Fact: "Think step by step."
Response: {{"is_valuable": false, "reason": "This is a general instruction or meta-advice, not a piece of knowledge to store as a fact."}}

JSON Response:
"""

LLM_FACT_CATEGORY_PROMPT_TEMPLATE = """
You are an AI assistant's knowledge categorizer. Your task is to assign a relevant category to the given factual statement.
Choose from the following predefined categories or suggest 'general' if none seem to fit well:
- "user_preference": Specific preferences of the user (e.g., favorite color, preferred tools, communication style).
- "user_personal_info": Personal details about the user (e.g., name, location if volunteered and relevant for interaction).
- "domain_knowledge": Facts related to a specific domain the user is working in (e.g., "In Python, lists are mutable.").
- "technical_term": Definitions or explanations of technical terms.
- "system_config": Information about the AI assistant's own configuration or status if it's a fact to remember.
- "project_context": Facts specific to a user's ongoing project that the AI is assisting with.
- "general_knowledge": Common knowledge facts that are useful but don't fit other categories.
- "correction": A fact that corrects a previous misunderstanding by the AI.

Fact to categorize: "{fact_text}"

Based on the fact, which category is most appropriate?
Respond with a single JSON object containing one key:
- "category": string (one of the predefined categories, or "general")

Example:
Fact: "The user prefers Python for scripting."
Response: {{"category": "user_preference"}}

Fact: "The capital of France is Paris."
Response: {{"category": "general_knowledge"}}

Fact: "The current project 'WebAppX' uses FastAPI."
Response: {{"category": "project_context"}}

JSON Response:
"""

class ActionExecutor:
    """
    Responsible for taking proposed actions (derived from ActionableInsights)
    and attempting to execute them.
    """
    def __init__(self, learning_agent: "LearningAgent",
                 task_manager: Optional[TaskManager] = None,
                 notification_manager: Optional[NotificationManager] = None): # New parameter
        """
        Initializes the ActionExecutor.
        """
        if is_debug_mode():
            print("[DEBUG] Initializing ActionExecutor...")
        print("ActionExecutor initialized.")
        self.learning_agent = learning_agent
        self.task_manager = task_manager
        self.notification_manager = notification_manager # Store it

        from ai_assistant.llm_interface import ollama_client as default_llm_provider
        from ai_assistant.core import self_modification as default_self_modification_service

        self.code_service = CodeService(
            llm_provider=default_llm_provider,
            self_modification_service=default_self_modification_service,
            task_manager=self.task_manager,
            notification_manager=self.notification_manager # Add this line
        )
        if is_debug_mode():
            print(f"[DEBUG] CodeService instance in ActionExecutor: {self.code_service}")
            print(f"[DEBUG] TaskManager instance in ActionExecutor: {self.task_manager}")
            print(f"[DEBUG] NotificationManager instance in ActionExecutor: {self.notification_manager}")

    def _update_task_if_manager(self, task_id: Optional[str], status: ActiveTaskStatus, reason: Optional[str] = None, step_desc: Optional[str] = None):
        if task_id and self.task_manager:
            self.task_manager.update_task_status(task_id, status, reason=reason, step_desc=step_desc)

    def _add_notification_if_manager(
        self,
        event_type: NotificationType,
        summary_message: str,
        related_item_id: Optional[str] = None,
        related_item_type: Optional[str] = None,
        details_payload: Optional[Dict[str, Any]] = None
    ):
        if self.notification_manager:
            self.notification_manager.add_notification(
                event_type, summary_message, related_item_id, related_item_type, details_payload
            )

    def _find_original_reflection_entry(self, entry_id: str) -> Optional[ReflectionLogEntry]:
        """
        Finds the original ReflectionLogEntry based on its unique entry_id.
        """
        for entry in reversed(global_reflection_log.log_entries):
            if entry.entry_id == entry_id:
                return entry
        print(f"ActionExecutor: Warning - Could not find original reflection entry with ID '{entry_id}'.")
        return None

    async def _run_post_modification_test(self, source_insight_id: Optional[str],
                                          original_reflection_entry_id: Optional[str],
                                          modified_tool_name: str) -> Tuple[Optional[bool], str]:
        if not original_reflection_entry_id:
            return None, "Post-modification test skipped: No original_reflection_entry_id provided."

        original_entry = self._find_original_reflection_entry(original_reflection_entry_id)

        if not original_entry:
            return None, f"Post-modification test skipped: Could not find original reflection entry for ID '{original_reflection_entry_id}'."

        if not original_entry.plan:
            return None, f"Post-modification test skipped: Original reflection entry '{original_reflection_entry_id}' had no plan to re-execute."

        print(f"ActionExecutor: Running post-modification test for tool '{modified_tool_name}'. Re-executing plan from original entry ID '{original_reflection_entry_id}'.")

        from ai_assistant.planning.execution import ExecutionAgent # Moved import here
        executor = ExecutionAgent()
        test_goal_description = f"POST_MOD_TEST for insight {source_insight_id} (orig_goal: {original_entry.goal_description[:50]})"

        try:
            # Create a temporary planner instance for executing the test
            temp_planner = PlannerAgent()
            test_results = await executor.execute_plan(
                goal_description=test_goal_description,
                initial_plan=original_entry.plan,
                tool_system=tool_system_instance,
                planner_agent=temp_planner,
                learning_agent=self.learning_agent, # Pass the learning_agent instance
                task_manager=self.task_manager # Pass it here
            )
            test_passed = not any(isinstance(res, Exception) for res in test_results)
            notes = f"Re-ran original plan. Test passed: {test_passed}. Results: {str(test_results)[:200]}..."
            
            if not test_passed:
                current_run_errors = [type(res).__name__ for res in test_results if isinstance(res, Exception)]
                if current_run_errors:
                    notes += f" Errors occurred: {current_run_errors}."
                else: # pragma: no cover
                    notes += " Test failed but could not identify specific error types in results."
            print(f"ActionExecutor: Post-modification test result for {modified_tool_name}: {'PASSED' if test_passed else 'FAILED' if test_passed is False else 'SKIPPED'}. Notes: {notes}")
            return test_passed, notes
        except Exception as e: # pragma: no cover
            error_msg = f"Exception during post-modification test execution for {modified_tool_name}: {type(e).__name__} - {e}"
            print(f"ActionExecutor: {error_msg}")
            return False, error_msg

    async def _apply_test_and_revert_code(
        self,
        module_path: str,
        function_name: str,
        code_to_apply: str,
        original_description: str, # This is the change_description for edit_function_source_code
        source_insight_id: str,   # This will be the related_item_id for the task
        action_task_id: Optional[str], # This is the parent_task_id for edit_function_source_code
    ) -> bool: # Return just success/failure
        tool_name = function_name
        source_of_code = "CodeService_LLM" if "CodeService generated code" in original_description else "Insight"

        original_reflection_id_for_test: Optional[str] = None

        log_notes_prefix = f"Action for insight {source_insight_id} ({source_of_code}): "
        modification_type_ast = "MODIFY_TOOL_CODE_LLM_AST" if source_of_code == "CodeService_LLM" else "MODIFY_TOOL_CODE_AST"
        modification_type_exception = "MODIFY_TOOL_CODE_LLM_AST_EXCEPTION" if source_of_code == "CodeService_LLM" else "MODIFY_TOOL_CODE_AST_EXCEPTION"

        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

        try:
            modification_result_msg = await self_modification.edit_function_source_code(
                module_path=module_path,
                function_name=function_name,
                new_code_string=code_to_apply,
                project_root_path=project_root,
                change_description=original_description,
                task_manager=self.task_manager,
                parent_task_id=action_task_id
            )
            edit_success = "success" in modification_result_msg.lower()
            print(f"ActionExecutor: Code modification result for {tool_name} (from {source_of_code}): {modification_result_msg}")

            test_passed_status: Optional[bool] = None
            test_run_notes: str = "Test not run."
            reversion_successful: Optional[bool] = None
            reversion_notes: str = ""

            if edit_success:
                if not original_reflection_id_for_test:
                    print(f"ActionExecutor: Warning - 'original_reflection_entry_id' not found in details for insight {source_insight_id}. Post-modification testing will be skipped.")
                    test_run_notes = "Post-modification test skipped: original_reflection_entry_id not provided."
                else:
                    test_passed_status, test_run_notes = await self._run_post_modification_test(
                        source_insight_id=source_insight_id,
                        original_reflection_entry_id=original_reflection_id_for_test,
                        modified_tool_name=tool_name
                    )

                if test_passed_status is False:
                    print(f"ActionExecutor: Post-modification test failed for {tool_name}. Attempting to revert.")
                    original_code_from_backup = self_modification.get_backup_function_source_code(module_path, function_name)
                    if original_code_from_backup:
                        try:
                            revert_msg = await self_modification.edit_function_source_code(
                                module_path, function_name,
                                original_code_from_backup,
                                project_root_path=project_root,
                                change_description=f"Reverting function '{function_name}' to backup due to failed post-modification test.",
                                task_manager=self.task_manager,
                                parent_task_id=action_task_id
                            )
                            reversion_successful = "success" in revert_msg.lower()
                            reversion_notes = f"Reverted: {reversion_successful}. Msg: {revert_msg}"
                        except Exception as e_revert: # pragma: no cover
                            reversion_successful = False
                            reversion_notes = f"Exception during revert: {e_revert}"
                        print(f"ActionExecutor: {reversion_notes}")
                    else: # pragma: no cover
                        reversion_successful = False
                        reversion_notes = f"Could not get backup code for {function_name}. Reversion not attempted."
                        print(f"ActionExecutor: {reversion_notes}")
                    test_run_notes += f" | Reversion attempted: {reversion_successful is not None}. Notes: {reversion_notes}"
            else:
                test_run_notes = "Test not run as code edit failed."

            final_overall_success = edit_success and (test_passed_status is True)
            try:
                global_reflection_log.log_execution(
                    goal_description=f"Self-modification ({source_of_code}) for insight {source_insight_id}",
                    plan=[{"action_type": "PROPOSE_TOOL_MODIFICATION", "details": {"tool_name": tool_name, "module_path": module_path}}],
                    execution_results=[modification_result_msg], overall_success=final_overall_success,
                    notes=log_notes_prefix + f"Applied code for {tool_name}. Edit success: {edit_success}. Test notes: {test_run_notes}",
                    is_self_modification_attempt=True, source_suggestion_id=source_insight_id,
                    modification_type=modification_type_ast,
                    modification_details={
                        "module_path": module_path, "function_name": function_name,
                        "applied_code_change_preview": code_to_apply[:200] + "..." if code_to_apply else "N/A",
                        "reason": original_description, "source_of_code": source_of_code,
                        "reversion_attempted": reversion_successful is not None,
                        "reversion_successful": reversion_successful
                    },
                    post_modification_test_passed=test_passed_status,
                    post_modification_test_details={"notes": test_run_notes}
                )
                if final_overall_success and source_insight_id and source_insight_id != "NO_INSIGHT_ID":
                    mark_suggestion_implemented(source_insight_id, f"Tool modification '{tool_name}' applied and tested successfully.", notification_manager=self.notification_manager)
                    self._add_notification_if_manager(
                        NotificationType.SELF_MODIFICATION_APPLIED,
                        f"Tool '{tool_name}' modified and passed tests successfully.",
                        related_item_id=tool_name,
                        related_item_type="agent_tool",
                        details_payload={"module_path": module_path, "function_name": function_name, "change_description": original_description}
                    )
                elif not final_overall_success and edit_success and test_passed_status is False:
                    self._add_notification_if_manager(
                        NotificationType.SELF_MODIFICATION_FAILED_TESTS,
                        f"Tool '{tool_name}' modification failed post-modification tests. Reversion success: {reversion_successful}.",
                        related_item_id=tool_name,
                        related_item_type="agent_tool",
                        details_payload={"module_path": module_path, "function_name": function_name, "reversion_notes": reversion_notes}
                    )
                return final_overall_success

            except Exception as e_log: # pragma: no cover
                print(f"ActionExecutor: CRITICAL - Failed to log successful/failed modification: {e_log}")
                return final_overall_success if 'final_overall_success' in locals() else False


        except Exception as e_main_apply: # pragma: no cover
            error_message = f"Major exception during code application process for {tool_name} (from {source_of_code}): {e_main_apply}"
            print(f"ActionExecutor: {error_message}")
            global_reflection_log.log_execution(
                goal_description=f"Self-modification ({source_of_code}) for insight {source_insight_id}",
                plan=[{"action_type": "PROPOSE_TOOL_MODIFICATION", "details": {"tool_name": tool_name, "module_path": module_path}}],
                execution_results=[error_message], overall_success=False,
                notes=f"Major exception for {tool_name} from {source_of_code}: {e_main_apply}",
                is_self_modification_attempt=True, source_suggestion_id=source_insight_id
            )
            return False

    async def _is_fact_valuable(self, fact_to_assess: str) -> Tuple[bool, str]:
        """
        Assesses if a given fact is valuable to learn using an LLM.
        """
        prompt = LLM_FACT_VALUE_ASSESSMENT_PROMPT_TEMPLATE.format(fact_to_assess=fact_to_assess)

        if not self.code_service or not self.code_service.llm_provider:
            logger.error("LLM Provider for CodeService not available in ActionExecutor for fact assessment.")
            return False, "LLM provider not available for assessment."

        try:
            model_name = self.code_service.llm_provider.model
            llm_response_str = await self.code_service.llm_provider.invoke_ollama_model_async(
                prompt,
                model_name=model_name,
                temperature=0.2
            )

            if not llm_response_str or not llm_response_str.strip():
                logger.warning("Fact assessment LLM returned empty response.")
                return False, "LLM returned empty response during assessment."

            cleaned_response_str = llm_response_str.strip()
            if cleaned_response_str.startswith("```json"):
                cleaned_response_str = cleaned_response_str[len("```json"):].strip()
                if cleaned_response_str.endswith("```"):
                    cleaned_response_str = cleaned_response_str[:-len("```")].strip()

            assessment_data = json.loads(cleaned_response_str)

            is_valuable = assessment_data.get("is_valuable", False)
            reason = assessment_data.get("reason", "No reason provided by LLM.")

            if not isinstance(is_valuable, bool):
                logger.warning(f"Fact assessment 'is_valuable' is not a boolean: {is_valuable}. Defaulting to False.")
                is_valuable = False
                reason += " (Assessment format error: is_valuable was not boolean)"

            return is_valuable, reason

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse fact assessment JSON: {e}. Response: {llm_response_str[:200]}")
            return False, f"JSON parsing error during assessment: {e}"
        except Exception as e:
            logger.error(f"Unexpected error during fact value assessment: {e}", exc_info=True)
            return False, f"Unexpected error during assessment: {e}"

    async def _get_fact_category_with_llm(self, fact_text: str) -> str:
        """
        Determines the category for a given fact using an LLM.
        """
        prompt = LLM_FACT_CATEGORY_PROMPT_TEMPLATE.format(fact_text=fact_text)
        default_category = "general"

        if not self.code_service or not self.code_service.llm_provider: # pragma: no cover
            logger.error("LLM Provider for CodeService not available for fact categorization.")
            return default_category

        try:
            model_name = self.code_service.llm_provider.model
            llm_response_str = await self.code_service.llm_provider.invoke_ollama_model_async(
                prompt,
                model_name=model_name,
                temperature=0.2
            )

            if not llm_response_str or not llm_response_str.strip(): # pragma: no cover
                logger.warning("Fact categorization LLM returned empty response. Defaulting to 'general'.")
                return default_category

            cleaned_response_str = llm_response_str.strip()
            if cleaned_response_str.startswith("```json"): # pragma: no cover
                cleaned_response_str = cleaned_response_str[len("```json"):].strip()
                if cleaned_response_str.endswith("```"):
                    cleaned_response_str = cleaned_response_str[:-len("```")].strip()

            category_data = json.loads(cleaned_response_str)
            category = category_data.get("category", default_category).lower().strip()

            return category if category else default_category

        except json.JSONDecodeError as e: # pragma: no cover
            logger.error(f"Failed to parse fact category JSON: {e}. Response: {llm_response_str[:200]}. Defaulting to 'general'.")
            return default_category
        except Exception as e: # pragma: no cover
            logger.error(f"Unexpected error during fact category assessment: {e}. Defaulting to 'general'.", exc_info=True)
            return default_category

    async def execute_action(self, proposed_action: Dict[str, Any]) -> bool:
        action_type = proposed_action.get("action_type")
        details = proposed_action.get("details", {})
        source_insight_id = proposed_action.get("source_insight_id", f"action_{uuid.uuid4().hex[:8]}")
        log_notes_prefix = f"Action for insight {source_insight_id}: "

        action_task_id: Optional[str] = None
        edit_success: bool = False

        if self.task_manager:
            task_type = ActiveTaskType.PROCESSING_REFLECTION # Default for actions from insights
            related_item = source_insight_id
            task_description = f"Executing action: {action_type}, Source: {source_insight_id}"

            if action_type == "PROPOSE_TOOL_MODIFICATION":
                task_type = ActiveTaskType.AGENT_TOOL_MODIFICATION
                related_item = details.get("tool_name", details.get("function_name", source_insight_id))
            elif action_type == "ADD_LEARNED_FACT":
                task_type = ActiveTaskType.LEARNING_NEW_FACT
                related_item = details.get("fact_to_learn", "Unknown fact")[:70]

            action_task = self.task_manager.add_task(
                description=task_description, # Corrected order
                task_type=task_type,          # Corrected order
                related_item_id=related_item,
                details=details
            )
            action_task_id = action_task.task_id

        print(f"ActionExecutor: Received action '{action_type}' for insight '{source_insight_id}'. Task ID: {action_task_id}. Details: {details}")

        if action_type == "PROPOSE_TOOL_MODIFICATION":
            tool_name = details.get("tool_name")
            suggested_code = details.get("suggested_code_change")
            module_path = details.get("module_path")
            function_name = details.get("function_name")
            original_description = details.get("suggested_change_description", "No specific description provided.")

            if not module_path or not function_name:
                log_message_details = f"Missing module_path or function_name for tool '{tool_name}'. Cannot attempt modification."
                print(f"ActionExecutor: {log_message_details}")
                global_reflection_log.log_execution(
                    goal_description=f"Self-modification attempt for insight {source_insight_id}",
                    plan=[{"action_type": action_type, "details": details}], execution_results=[f"Failure: {log_message_details}"],
                    overall_success=False, notes=log_notes_prefix + log_message_details,
                    status_override="SELF_MODIFICATION_FAILED_PRECONDITIONS",
                    post_modification_test_passed=None, post_modification_test_details={"notes": "Test not run due to precondition failure."}
                )
                self._update_task_if_manager(action_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=log_message_details, step_desc="Precondition check failed")
                return False

            suggested_code_or_llm_generated_code: Optional[str] = suggested_code

            if not suggested_code_or_llm_generated_code:
                print(f"ActionExecutor: No direct code for {tool_name}. Requesting CodeService for fix. Task ID: {action_task_id}")
                self._update_task_if_manager(action_task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="CodeService: Generating code fix")
                code_service_result = await self.code_service.modify_code(
                    context="SELF_FIX_TOOL",
                    modification_instruction=original_description,
                    existing_code=None,
                    module_path=module_path,
                    function_name=function_name
                )
                if code_service_result.get("status") == "SUCCESS_CODE_GENERATED":
                    suggested_code_or_llm_generated_code = code_service_result.get("modified_code_string")
                    logger.info(f"CodeService generated code for {function_name}. Length: {len(suggested_code_or_llm_generated_code) if suggested_code_or_llm_generated_code else 0}. Task ID: {action_task_id}")
                else:
                    err_msg = f"CodeService failed to generate code. Status: {code_service_result.get('status')}, Error: {code_service_result.get('error')}"
                    logger.error(f"{err_msg}. Task ID: {action_task_id}")
                    global_reflection_log.log_execution(
                        goal_description=f"CodeService code generation for insight {source_insight_id}",
                        plan=[{"action_type": "CODE_SERVICE_MODIFY_CODE", "details": {"module": module_path, "func": function_name}}],
                        execution_results=[err_msg], overall_success=False, status_override="CODE_SERVICE_GEN_FAILED"
                    )
                    self._update_task_if_manager(action_task_id, ActiveTaskStatus.FAILED_CODE_GENERATION, reason=err_msg, step_desc="CodeService failed")
                    return False

            if suggested_code_or_llm_generated_code:
                edit_success = await self._apply_test_and_revert_code(
                    module_path, function_name, suggested_code_or_llm_generated_code,
                    original_description,
                    str(source_insight_id) if source_insight_id else "NO_INSIGHT_ID",
                    action_task_id=action_task_id
                )
                if edit_success:
                    self._update_task_if_manager(action_task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, step_desc="Tool modification process completed successfully.")
                else:
                    if self.task_manager and action_task_id:
                        task = self.task_manager.get_task(action_task_id) # Get current task to check status
                        if task and task.status not in [ActiveTaskStatus.POST_MOD_TEST_FAILED, ActiveTaskStatus.FAILED_DURING_APPLY, ActiveTaskStatus.CRITIC_REVIEW_REJECTED]: # Avoid overriding specific failure
                             self._update_task_if_manager(action_task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason="Tool modification process failed at some stage.", step_desc="Tool modification process failed.")
                return edit_success
            else:
                self._update_task_if_manager(action_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason="No code available to apply.", step_desc="No code to apply")
                return False


        elif action_type == "ADD_LEARNED_FACT":
            fact_to_learn = details.get("fact_to_learn")
            if not fact_to_learn:
                log_msg = "Missing 'fact_to_learn' in details. Cannot add fact."
                self._update_task_if_manager(action_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=log_msg, step_desc="Missing fact text")
                return False

            try:
                current_facts = load_learned_facts()
                normalized_fact_to_learn = fact_to_learn.strip()

                if any(entry.get("text", "").strip().lower() == normalized_fact_to_learn.lower() for entry in current_facts):
                    log_message = f"Fact '{normalized_fact_to_learn}' already exists. No action taken."
                    if source_insight_id: mark_suggestion_implemented(source_insight_id, f"Fact already known: {normalized_fact_to_learn}", notification_manager=self.notification_manager)
                    self._update_task_if_manager(action_task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Fact is a duplicate.", step_desc="Duplicate check complete.")
                    self._add_notification_if_manager(
                        NotificationType.GENERAL_INFO,
                        f"Fact already known, not re-learned: {normalized_fact_to_learn[:70]}...",
                        related_item_id=source_insight_id,
                        related_item_type="suggestion_leading_to_fact_assessment"
                    )
                    return True

                self._update_task_if_manager(action_task_id, ActiveTaskStatus.PLANNING, step_desc="Assessing fact value")
                is_valuable, assessment_reason = await self._is_fact_valuable(normalized_fact_to_learn)
                if not is_valuable:
                    log_message = f"Fact '{normalized_fact_to_learn}' assessed as NOT VALUABLE. Reason: {assessment_reason}."
                    if source_insight_id: mark_suggestion_implemented(source_insight_id, f"Fact assessed as not valuable: {log_message}", notification_manager=self.notification_manager)
                    self._update_task_if_manager(action_task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason=f"Fact not valuable: {assessment_reason}", step_desc="Fact assessment complete.")
                    self._add_notification_if_manager(
                        NotificationType.GENERAL_INFO,
                        f"Fact assessed as not valuable, not learned: {normalized_fact_to_learn[:70]}...",
                        related_item_id=source_insight_id,
                        related_item_type="suggestion_leading_to_fact_assessment"
                    )
                    return True

                self._update_task_if_manager(action_task_id, ActiveTaskStatus.PLANNING, step_desc="Categorizing fact")
                determined_category = await self._get_fact_category_with_llm(normalized_fact_to_learn)

                new_fact_entry = {
                    "fact_id": f"fact_{uuid.uuid4().hex[:8]}", "text": normalized_fact_to_learn,
                    "category": determined_category,
                    "source": details.get("source", f"Insight {source_insight_id}" if source_insight_id else "Unknown"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                current_facts.append(new_fact_entry)

                if save_learned_facts(current_facts):
                    log_message = f"Successfully added valuable fact: '{normalized_fact_to_learn}' (Category: {determined_category})."
                    if source_insight_id: mark_suggestion_implemented(source_insight_id, f"Valuable fact added: {normalized_fact_to_learn} (Category: {determined_category})", notification_manager=self.notification_manager)
                    self._update_task_if_manager(action_task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, step_desc="Fact learned and saved.")
                    self._add_notification_if_manager(
                        NotificationType.FACT_LEARNED,
                        f"New fact learned: {new_fact_entry['text'][:70]}... (Category: {new_fact_entry['category']})",
                        related_item_id=new_fact_entry['fact_id'],
                        related_item_type="fact",
                        details_payload={"category": new_fact_entry['category'], "source": new_fact_entry['source'], "text": new_fact_entry['text']}
                    )
                    return True
                else: # pragma: no cover
                    self._update_task_if_manager(action_task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason="Failed to save fact.", step_desc="Fact save operation failed.")
                    return False
            except Exception as e: # pragma: no cover
                self._update_task_if_manager(action_task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=str(e), step_desc="Exception during fact processing")
                return False
        else: # pragma: no cover
            log_msg = f"ActionExecutor: Unknown or unsupported action_type: {action_type}"
            print(log_msg)
            self._update_task_if_manager(action_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=log_msg, step_desc="Unsupported action type")
            return False

        if 'edit_success' in locals() and isinstance(edit_success, bool):
            final_status_reason = "Tool modification process completed." if edit_success else "Tool modification process failed."
            final_task_status = ActiveTaskStatus.COMPLETED_SUCCESSFULLY if edit_success else ActiveTaskStatus.FAILED_UNKNOWN
            self._update_task_if_manager(action_task_id, final_task_status, reason=final_status_reason, step_desc=final_status_reason)
            return edit_success

        self._update_task_if_manager(action_task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason="Unhandled execution path.", step_desc="Unhandled path")
        return False


if __name__ == '__main__': # pragma: no cover
    from dataclasses import dataclass, field
    from ai_assistant.config import get_data_dir
    # from ..core.task_manager import TaskManager # Already imported at top

    CUSTOM_TOOLS_DIR = os.path.join("ai_assistant", "custom_tools")
    os.makedirs(CUSTOM_TOOLS_DIR, exist_ok=True)
    dummy_tool_file_path = os.path.join(CUSTOM_TOOLS_DIR, "my_extra_tools.py")

    if not os.path.exists(dummy_tool_file_path):
        with open(dummy_tool_file_path, "w") as f:
            f.write("# Dummy tool file for testing ActionExecutor\n")
            f.write("def subtract_numbers(a: float, b: float) -> float:\n")
            f.write("    # Original version, might have a bug or needs enhancement\n")
            f.write("    return float(a) - float(b)\n\n")
            f.write("def echo_message(message: str) -> str:\n")
            f.write("    return message # Original echo\n")
            f.write("\n# Placeholder for a function that might cause an error during post-modification test if not careful\n")
            f.write("def original_failing_function(data: dict) -> str:\n")
            f.write("    return data['key_that_might_be_missing_after_edit']\n")

    data_dir = get_data_dir()
    print(f"[ActionExecutor Test Setup] Ensured data directory exists at: {data_dir}")

    if not os.path.exists(LEARNED_FACTS_FILEPATH):
        print(f"[ActionExecutor Test Setup] Attempting to create dummy learned facts at: {LEARNED_FACTS_FILEPATH}")
        save_learned_facts([{"text": "Initial dummy fact from action_executor test setup (should be in data dir).", "fact_id": "fact_test_init", "category": "test", "source": "init", "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}]) # Updated to new fact structure
    else:
        print(f"[ActionExecutor Test Setup] Learned facts file already exists at: {LEARNED_FACTS_FILEPATH}")

    @dataclass
    class MockReflectionLogEntryForTest: # Simplified for testing focus
        entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        goal_description: str = "Mock Goal"
        plan: Optional[List[Dict[str, Any]]] = None
        timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


    async def main_test():
        mock_notification_manager = NotificationManager()
        mock_task_manager = TaskManager(notification_manager=mock_notification_manager)

        test_learning_agent = LearningAgent(
            insights_filepath="test_action_executor_insights.json",
            task_manager=mock_task_manager,
            notification_manager=mock_notification_manager
        )
        executor = ActionExecutor(
            learning_agent=test_learning_agent,
            task_manager=mock_task_manager,
            notification_manager=mock_notification_manager
        )

        original_timestamp = datetime.now(timezone.utc) - datetime.timedelta(minutes=5)
        original_goal = "Test original goal that failed"
        original_plan = [{"tool_name": "subtract_numbers_tool_alias", "args": (10, 5), "kwargs": {}}]

        mock_entry_id_for_test = str(uuid.uuid4())
        # Using actual ReflectionLogEntry for consistency
        mock_original_entry = ReflectionLogEntry(
            entry_id=mock_entry_id_for_test,
            goal_description=original_goal,
            status="FAILURE",
            plan=original_plan,
            execution_results=[TypeError("Simulated original error")],
            error_type="TypeError",
            timestamp=original_timestamp
        )
        global_reflection_log.log_entries = [mock_original_entry]


        mock_tool_mod_action_with_code = {
            "source_insight_id": "insight_tool_mod_001",
            "action_type": "PROPOSE_TOOL_MODIFICATION",
            "details": {
                "module_path": "ai_assistant.custom_tools.my_extra_tools",
                "function_name": "subtract_numbers",
                "tool_name": "subtract_numbers_tool_alias",
                "suggested_change_description": "Enhance subtract_numbers to log output and handle types.",
                "suggested_code_change": "def subtract_numbers(a: float, b: float) -> float:\n    # Enhanced version\n    a_float = float(a)\n    b_float = float(b)\n    result = a_float - b_float\n    print(f'Subtracting: {a} - {b} = {result}')\n    return result",
                "original_reflection_entry_id": mock_entry_id_for_test
            }
        }
        print("\n--- Testing PROPOSE_TOOL_MODIFICATION (with code & post-mod test) ---")
        success_tool_mod = await executor.execute_action(mock_tool_mod_action_with_code)
        print(f"Tool modification with code and post-mod test action success: {success_tool_mod}")


        print("\n--- Testing ADD_LEARNED_FACT (New Fact) ---")
        new_fact_action = {
            "source_insight_id": "insight_add_fact_001",
            "action_type": "ADD_LEARNED_FACT",
            "details": { "fact_to_learn": "The sky is often blue during the day.", "source": "Observation during test" }
        }
        success_add_fact = await executor.execute_action(new_fact_action)
        print(f"Add new fact action success: {success_add_fact}")

        print("\n--- Testing ADD_LEARNED_FACT (Duplicate Fact) ---")
        duplicate_fact_action = {
            "source_insight_id": "insight_add_fact_002",
            "action_type": "ADD_LEARNED_FACT",
            "details": { "fact_to_learn": "The sky is often blue during the day.", "source": "Repeated observation" }
        }
        success_add_duplicate_fact = await executor.execute_action(duplicate_fact_action)
        print(f"Add duplicate fact action success: {success_add_duplicate_fact} (expected True, as it's handled)")

        if mock_task_manager:
            print("\n--- Task Manager Summary ---")
            # Method to print summary might need to be added to TaskManager for this.
            # For now, just list active tasks as an example.
            active_tasks_for_summary = mock_task_manager.list_active_tasks()
            print(f"Found {len(active_tasks_for_summary)} active tasks.")
            for t in active_tasks_for_summary:
                 print(f"  - ID: {t.task_id}, Desc: {t.description[:50]}..., Status: {t.status.name}")


    asyncio.run(main_test())
