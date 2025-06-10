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
# Removed: invoke_ollama_model_async, get_model_for_task, LLM_CODE_FIX_PROMPT_TEMPLATE
from ai_assistant.planning.planning import PlannerAgent
from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.code_services.service import CodeService # Added

if TYPE_CHECKING:
    from ai_assistant.learning.learning import LearningAgent # For type hinting only

logger = logging.getLogger(__name__)

class ActionExecutor:
    """
    Responsible for taking proposed actions (derived from ActionableInsights)
    and attempting to execute them.
    """
    def __init__(self, learning_agent: "LearningAgent"): # Use string for forward reference
        """
        Initializes the ActionExecutor.
        """
        if is_debug_mode():
            print("[DEBUG] Initializing ActionExecutor...")
        print("ActionExecutor initialized.")
        # Import necessary modules for dependency injection
        from ai_assistant.llm_interface import ollama_client as default_llm_provider
        from ai_assistant.core import self_modification as default_self_modification_service

        self.code_service = CodeService(
            llm_provider=default_llm_provider,
            self_modification_service=default_self_modification_service
        )
        self.learning_agent = learning_agent
        if is_debug_mode():
            print(f"[DEBUG] CodeService instance in ActionExecutor: {self.code_service}")

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
                learning_agent=self.learning_agent # Pass the learning_agent instance
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
        original_description: str,
        source_insight_id: str,
        action_type: str,
        details_for_logging: Dict[str, Any]
    ) -> bool:
        tool_name = details_for_logging.get("tool_name", function_name)
        source_of_code = details_for_logging.get("source_of_code", "Unknown")
        original_reflection_id_for_test = details_for_logging.get("original_reflection_entry_id")
        log_notes_prefix = f"Action for insight {source_insight_id} ({source_of_code}): "

        modification_type_ast = "MODIFY_TOOL_CODE_LLM_AST" if source_of_code == "CodeService_LLM" else "MODIFY_TOOL_CODE_AST"
        modification_type_exception = "MODIFY_TOOL_CODE_LLM_AST_EXCEPTION" if source_of_code == "CodeService_LLM" else "MODIFY_TOOL_CODE_AST_EXCEPTION"

        # Determine project_root_path for self_modification calls
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")) # Relative to this file's location

        try:
            modification_result_msg = self_modification.edit_function_source_code(
                module_path=module_path,
                function_name=function_name,
                new_code_string=code_to_apply,
                project_root_path=project_root,
                change_description=original_description # Add this argument
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
                            revert_msg = self_modification.edit_function_source_code(
                                module_path, function_name,
                                original_code_from_backup,
                                project_root_path=project_root,
                                change_description=f"Reverting function '{function_name}' to backup due to failed post-modification test." # Add this argument
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
                    plan=[{"action_type": action_type, "details": details_for_logging}],
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
                return final_overall_success

            except Exception as e_main_apply: # pragma: no cover
                error_message = f"Exception during code application process for {tool_name} (from {source_of_code}): {e_main_apply}"
                print(f"ActionExecutor: {error_message}")
                global_reflection_log.log_execution(
                    goal_description=f"Self-modification ({source_of_code}) for insight {source_insight_id}",
                    plan=[{"action_type": action_type, "details": details_for_logging}],
                    execution_results=[error_message], overall_success=False,
                    notes=f"Exception for {tool_name} from {source_of_code}: {e_main_apply}",
                    is_self_modification_attempt=True, source_suggestion_id=source_insight_id,
                    modification_type=modification_type_exception,
                    modification_details={"module_path": module_path, "function_name": function_name, "reason": original_description, "source_of_code": source_of_code},
                    post_modification_test_passed=None,
                    first_error_type=type(e_main_apply).__name__, first_error_message=str(e_main_apply),
                    post_modification_test_details={"notes": "Test not run due to apply failure."}
                )
                return False

        except Exception as e_main_apply: # pragma: no cover
            error_message = f"Exception during code application process for {tool_name} (from {source_of_code}): {e_main_apply}"
            print(f"ActionExecutor: {error_message}")
            global_reflection_log.log_execution(
                goal_description=f"Self-modification ({source_of_code}) for insight {source_insight_id}",
                plan=[{"action_type": action_type, "details": details_for_logging}],
                execution_results=[error_message], overall_success=False,
                notes=f"Exception for {tool_name} from {source_of_code}: {e_main_apply}",
                is_self_modification_attempt=True, source_suggestion_id=source_insight_id,
                modification_type=modification_type_exception,
                modification_details={"module_path": module_path, "function_name": function_name, "reason": original_description, "source_of_code": source_of_code},
                post_modification_test_passed=None, 
                post_modification_test_details={"notes": "Test not run due to apply failure."}
            )
            return False

    async def execute_action(self, proposed_action: Dict[str, Any]) -> bool:
        action_type = proposed_action.get("action_type")
        details = proposed_action.get("details", {})
        source_insight_id = proposed_action.get("source_insight_id")
        log_notes_prefix = f"Action for insight {source_insight_id}: "

        print(f"ActionExecutor: Received action '{action_type}' for insight '{source_insight_id}'. Details: {details}")

        if action_type == "PROPOSE_TOOL_MODIFICATION":
            tool_name = details.get("tool_name")
            suggested_code = details.get("suggested_code_change") # This is the code string from the insight
            module_path = details.get("module_path")
            function_name = details.get("function_name")
            original_description = details.get("suggested_change_description", "No specific description provided.")
            original_reflection_id_for_test = details.get("original_reflection_entry_id")

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
                return False

            details_for_apply_log = details.copy() # For passing to the helper

            if suggested_code:
                details_for_apply_log["source_of_code"] = "Insight"
                return await self._apply_test_and_revert_code(
                    module_path, function_name, suggested_code, original_description,
                    str(source_insight_id) if source_insight_id else "NO_INSIGHT_ID", action_type, details_for_apply_log
                )
            else:
                print(f"ActionExecutor: No direct code for {tool_name}. Requesting CodeService for fix.")
                # Fetch original code to pass to CodeService if needed, or CodeService can fetch it.
                # For this refactor, CodeService's modify_code expects `existing_code` to be passed if available,
                # or it will fetch if `module_path` and `function_name` are given.
                # Here, we pass `existing_code=None` to signal CodeService to fetch.
                code_service_result = await self.code_service.modify_code(
                    context="SELF_FIX_TOOL",
                    modification_instruction=original_description,
                    existing_code=None, # Signal CodeService to fetch
                    module_path=module_path,
                    function_name=function_name
                )

                llm_generated_code = None
                if code_service_result.get("status") == "SUCCESS_CODE_GENERATED":
                    llm_generated_code = code_service_result.get("modified_code_string")
                    logger.info(f"CodeService generated code for {function_name}. Length: {len(llm_generated_code) if llm_generated_code else 0}")
                    global_reflection_log.log_execution(
                        goal_description=f"CodeService code generation for insight {source_insight_id}",
                        plan=[{"action_type": "CODE_SERVICE_MODIFY_CODE", "details": {"module": module_path, "func": function_name}}],
                        execution_results=[f"CodeService status: SUCCESS_CODE_GENERATED. Code length: {len(llm_generated_code) if llm_generated_code else 0}"],
                        overall_success=True, status_override="CODE_SERVICE_GEN_SUCCESS"
                    )
                else: # pragma: no cover
                    logger.error(f"CodeService failed to generate code. Status: {code_service_result.get('status')}, Error: {code_service_result.get('error')}")
                    global_reflection_log.log_execution(
                        goal_description=f"CodeService code generation for insight {source_insight_id}",
                        plan=[{"action_type": "CODE_SERVICE_MODIFY_CODE", "details": {"module": module_path, "func": function_name}}],
                        execution_results=[f"CodeService status: {code_service_result.get('status')}. Error: {code_service_result.get('error')}"],
                        overall_success=False, status_override="CODE_SERVICE_GEN_FAILED",
                        post_modification_test_passed=None, post_modification_test_details={"notes": "Test not run as LLM code generation failed."}
                    )
                    return False

                if llm_generated_code:
                    details_for_apply_log["source_of_code"] = "CodeService_LLM"
                    return await self._apply_test_and_revert_code(
                        module_path, function_name, llm_generated_code, original_description,
                        str(source_insight_id) if source_insight_id else "NO_INSIGHT_ID", action_type, details_for_apply_log
                    )
                else: # Should have been caught by previous check, but as a safeguard
                    return False # pragma: no cover

        elif action_type == "ADD_LEARNED_FACT":
            fact_to_learn = details.get("fact_to_learn")
            source_description = details.get("source", f"Insight {source_insight_id}")
            if not fact_to_learn:
                # ... (rest of ADD_LEARNED_FACT logic remains the same) ...
                log_message_details = "Missing 'fact_to_learn' in details. Cannot add fact."
                print(f"ActionExecutor: {log_message_details}")
                global_reflection_log.log_execution(
                    goal_description=f"Add learned fact attempt from insight {source_insight_id}",
                    plan=[{"action_type": action_type, "details": details}], execution_results=[f"Failure: {log_message_details}"],
                    overall_success=False, notes=log_notes_prefix + log_message_details,
                    status_override="ADD_FACT_FAILED_PRECONDITIONS"
                )
                return False
            try:
                current_facts = load_learned_facts()
                if any(fact.lower() == fact_to_learn.lower() for fact in current_facts):
                    log_message = f"Fact '{fact_to_learn}' already exists. No action taken."
                    print(f"ActionExecutor: {log_message}")
                    global_reflection_log.log_execution(
                        goal_description=f"Add learned fact from insight {source_insight_id}",
                        plan=[{"action_type": action_type, "details": details}], execution_results=[log_message],
                        overall_success=True, notes=log_notes_prefix + "Fact already present.",
                        is_self_modification_attempt=True, source_suggestion_id=source_insight_id,
                        modification_type="ADD_LEARNED_FACT_DUPLICATE_IGNORED"
                    )
                    return True
                current_facts.append(fact_to_learn)
                if save_learned_facts(current_facts):
                    log_message = f"Successfully added learned fact: '{fact_to_learn}'."
                    print(f"ActionExecutor: {log_message}")
                    global_reflection_log.log_execution(
                        goal_description=f"Add learned fact from insight {source_insight_id}",
                        plan=[{"action_type": action_type, "details": details}], execution_results=[log_message],
                        overall_success=True, notes=log_notes_prefix + f"Added '{fact_to_learn}'.",
                        is_self_modification_attempt=True, source_suggestion_id=source_insight_id,
                        modification_type="ADD_LEARNED_FACT_SUCCESS",
                        modification_details={"fact_added": fact_to_learn}
                    )
                    return True
                else: # pragma: no cover
                    log_message = "Failed to save updated learned facts."
                    print(f"ActionExecutor: {log_message}")
                    global_reflection_log.log_execution(
                        goal_description=f"Add learned fact from insight {source_insight_id}",
                        plan=[{"action_type": action_type, "details": details}], execution_results=[log_message],
                        overall_success=False, notes=log_notes_prefix + f"Failed to save fact '{fact_to_learn}'.",
                        is_self_modification_attempt=True, source_suggestion_id=source_insight_id,
                        modification_type="ADD_LEARNED_FACT_SAVE_FAILED"
                    )
                    return False
            except Exception as e: # pragma: no cover
                error_message = f"Exception during adding learned fact '{fact_to_learn}': {e}"
                print(f"ActionExecutor: {error_message}")
                global_reflection_log.log_execution(
                    goal_description=f"Add learned fact attempt from insight {source_insight_id}",
                    plan=[{"action_type": action_type, "details": details}], execution_results=[error_message],
                    overall_success=False, notes=log_notes_prefix + f"Exception for '{fact_to_learn}'.",
                    is_self_modification_attempt=True, source_suggestion_id=source_insight_id,
                    modification_type="ADD_LEARNED_FACT_EXCEPTION", # Corrected parameter names
                    first_error_type=type(e).__name__, first_error_message=str(e)
                )
                return False

        else: # pragma: no cover
            print(f"ActionExecutor: Unknown or unsupported action_type: {action_type}")
            return False

        # This line should ideally not be reached if all action types return explicitly.
        # However, it can be reached if a PROPOSE_TOOL_MODIFICATION path that should return doesn't.
        print(f"ActionExecutor: Unhandled path or placeholder for action '{action_type}'. Returning False.")
        return False

if __name__ == '__main__': # pragma: no cover
    from dataclasses import dataclass, field
    from ai_assistant.config import get_data_dir # Added for test setup

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

    # Ensure the central data directory exists, as other modules (like persistent_memory)
    # are expected to use it.
    data_dir = get_data_dir()
    print(f"[ActionExecutor Test Setup] Ensured data directory exists at: {data_dir}")

    # LEARNED_FACTS_FILEPATH is imported from persistent_memory.
    # It is now expected to point to a file within the data_dir.
    # The following check and save_learned_facts will use that (hopefully updated) path.
    if not os.path.exists(LEARNED_FACTS_FILEPATH):
        print(f"[ActionExecutor Test Setup] Attempting to create dummy learned facts at: {LEARNED_FACTS_FILEPATH}")
        save_learned_facts(["Initial dummy fact from action_executor test setup (should be in data dir)."])
    else:
        print(f"[ActionExecutor Test Setup] Learned facts file already exists at: {LEARNED_FACTS_FILEPATH}")

    @dataclass
    class MockReflectionLogEntryForTest:
        entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        goal_description: str = "Mock Goal"
        plan: Optional[List[Dict[str, Any]]] = None
        execution_results: Optional[List[Any]] = None
        status: str = "UNKNOWN"
        notes: Optional[str] = ""
        timestamp: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
        error_type: Optional[str] = None
        error_message: Optional[str] = None
        # ... (other fields as needed by ReflectionLogEntry) ...

    async def main_test():
        # For testing ActionExecutor, we need a LearningAgent instance or a mock.
        # Let's use the actual LearningAgent for this test, assuming it can be instantiated simply.
        # If LearningAgent has complex dependencies for init, a mock would be better.
        test_learning_agent = LearningAgent(insights_filepath="test_action_executor_insights.json")
        executor = ActionExecutor(learning_agent=test_learning_agent)

        original_timestamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
        original_goal = "Test original goal that failed"
        original_plan = [{"tool_name": "subtract_numbers_tool_alias", "args": (10, 5), "kwargs": {}}]

        mock_entry_id = str(uuid.uuid4())
        # Create a proper ReflectionLogEntry for testing
        mock_original_entry = ReflectionLogEntry(
            entry_id=mock_entry_id,
            goal_description=original_goal,
            status="FAILURE",
            plan=original_plan,
            execution_results=[TypeError("Simulated original error")],
            error_type="TypeError",
            timestamp=original_timestamp
        )
        # Replace the log entries with our test entry
        global_reflection_log.log_entries = [mock_original_entry]  # Reset and add

        mock_tool_mod_action_with_code = {
            "source_insight_id": "insight_tool_mod_001",
            "action_type": "PROPOSE_TOOL_MODIFICATION",
            "details": {
                "module_path": "ai_assistant.custom_tools.my_extra_tools",
                "function_name": "subtract_numbers",
                "tool_name": "subtract_numbers_tool_alias",
                "suggested_change_description": "Enhance subtract_numbers to log output.",
                "suggested_code_change": "def subtract_numbers(a: float, b: float) -> float:\n    result = float(a) - float(b)\n    print(f'Subtracting: {a} - {b} = {result}')\n    return result",
                "original_reflection_entry_id": mock_entry_id
            }
        }
        print("\n--- Testing PROPOSE_TOOL_MODIFICATION (with code & post-mod test) ---")
        success_tool_mod = await executor.execute_action(mock_tool_mod_action_with_code)
        print(f"Tool modification with code and post-mod test action success: {success_tool_mod}")
        if not success_tool_mod:
            print("INFO: If this was due to test failure, reversion should have been attempted (see logs).")

        print("\n--- Testing PROPOSE_TOOL_MODIFICATION (LLM Gen - CodeService will be called) ---")
        mock_tool_mod_llm_attempt = {
            "source_insight_id": "insight_tool_mod_llm_002",
            "action_type": "PROPOSE_TOOL_MODIFICATION",
            "details": {
                "module_path": "ai_assistant.custom_tools.my_extra_tools",
                "function_name": "echo_message",
                "tool_name": "echo_message_tool_alias",
                "suggested_change_description": "echo_message needs to shout more and return original.",
                "original_reflection_entry_id": str(uuid.uuid4()) # Dummy ID for this test
            }
        }

        # Temporarily mock CodeService.modify_code to simulate LLM behavior for this test
        original_code_service_modify = executor.code_service.modify_code
        async def mock_modify_code_no_suggestion(*args, **kwargs):
            print("Mocked CodeService.modify_code: Simulating LLM no suggestion.")
            return {"status": "ERROR_LLM_NO_SUGGESTION", "modified_code_string": None, "logs": ["LLM failed"], "error": "LLM no suggestion"}

        executor.code_service.modify_code = mock_modify_code_no_suggestion
        try:
            success_tool_mod_llm = await executor.execute_action(mock_tool_mod_llm_attempt)
            print(f"Tool modification (LLM attempt - no code from CodeService) action success: {success_tool_mod_llm} (expected False)")
        finally:
            executor.code_service.modify_code = original_code_service_modify # Restore

    asyncio.run(main_test())
