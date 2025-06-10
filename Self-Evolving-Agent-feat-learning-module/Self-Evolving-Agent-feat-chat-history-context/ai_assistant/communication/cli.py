# ai_assistant/communication/cli.py
import importlib
import re
import asyncio
import os
import json # Already imported
import sys
import traceback
from prompt_toolkit.formatted_text import ANSI
from ai_assistant.goals import goal_management
from ai_assistant.tools import tool_system # Direct import for tool_system_instance 
from ai_assistant.planning.planning import PlannerAgent
from ..planning.execution import ExecutionAgent
from ..core.reflection import global_reflection_log, analyze_last_failure, get_learnings_from_reflections
from ai_assistant.core import self_modification
from ai_assistant.memory.awareness import get_tool_associations
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager, NotificationStatus, NotificationType, Notification # Added NotificationType and Notification
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task, is_debug_mode 
from typing import Tuple, List, Dict, Any, Optional
from ai_assistant.core.conversation_intelligence import detect_missed_tool_opportunity, formulate_tool_description_from_conversation, generate_conversational_response
from ai_assistant.memory.event_logger import log_event, get_recent_events
from ai_assistant.core.autonomous_reflection import run_self_reflection_cycle, select_suggestion_for_autonomous_action
from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.learning.autonomous_learning import learn_facts_from_interaction
from ai_assistant.config import AUTONOMOUS_LEARNING_ENABLED, CONVERSATION_HISTORY_TURNS
from ai_assistant.utils.display_utils import (
    CLIColors, color_text, format_header, format_message,
    format_input_prompt, format_thinking, format_tool_execution,
    format_status, draw_separator
)
from ai_assistant.core.refinement import RefinementAgent
from ai_assistant.code_services.service import CodeService
from ai_assistant.core.fs_utils import write_to_file
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.core import project_manager
from ai_assistant.core import suggestion_manager as suggestion_manager_module
from ai_assistant.core import status_reporting
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.patch_stdout import patch_stdout

# Imports for new CLI commands
from ai_assistant.custom_tools.awareness_tools import get_system_status_summary, get_item_details_by_id, list_formatted_suggestions
from ai_assistant.custom_tools.suggestion_management_tools import manage_suggestion_status


# State variable for pending tool confirmation
_pending_tool_confirmation_details: Optional[Dict[str, Any]] = None
_results_queue: Optional[asyncio.Queue] = None
_orchestrator: Optional[DynamicOrchestrator] = None
_task_manager_cli_instance: Optional[TaskManager] = None # Made global for _handle_code_generation
_notification_manager_cli_instance: Optional[NotificationManager] = None # Added global for NotificationManager

# Helper function to print notifications
def _print_notifications_list(notifications: List[Notification], title: str):
    print_formatted_text(format_header(title, color=CLIColors.HEADER)) # Assuming format_header can take color
    if not notifications:
        print_formatted_text(ANSI(color_text("  No notifications to display for this filter.", CLIColors.INFO_MESSAGE)))
        return
    for n in notifications:
        # Ensure timestamp is timezone-aware before formatting, or handle naive ones
        ts_aware = n.timestamp
        if ts_aware.tzinfo is None: # If naive, assume local and convert to UTC for display consistency, or just display as is
            # For simplicity, displaying as is, but be mindful of timezone handling in real apps
             ts_str = ts_aware.strftime('%Y-%m-%d %H:%M:%S')
        else:
             ts_str = ts_aware.strftime('%Y-%m-%d %H:%M:%S %Z') # More precise timestamp if tz-aware

        summary_text = n.summary_message
        max_len_summary = 120 # Max length for the summary part of the line

        # Construct the core message string first
        core_message = f"[{ts_str}] ({n.status.name}) {n.event_type.name}: {summary_text}"

        # Truncate the core_message if it's too long
        if len(core_message) > max_len_summary:
            core_message = core_message[:max_len_summary - 3] + "..."

        rel_item_info = ""
        if n.related_item_id:
            rel_item_info += f" (Related ID: {n.related_item_id}"
            if n.related_item_type: # related_item_type can be None
                rel_item_info += f", Type: {n.related_item_type}"
            rel_item_info += ")"

        # Print ID on one line, details on the next, indented.
        print_formatted_text(ANSI(color_text(f"- ID: {n.notification_id}", CLIColors.SYSTEM_MESSAGE)))
        print_formatted_text(ANSI(color_text(f"  {core_message}{rel_item_info}", CLIColors.INFO_MESSAGE)))


def _is_search_like_query(user_input: str) -> bool: # pragma: no cover
    user_input_lower = user_input.lower()
    search_keywords = [
        "what is", "who is", "what are", "who are", "explain", "tell me about",
        "search for", "find information on", "how does", "why does", "when did",
        "what's the capital of", "define "
    ]
    tool_creation_keywords = ["make a tool", "create a tool", "new tool", "generate a tool"]
    if any(keyword in user_input_lower for keyword in tool_creation_keywords): return False
    if any(user_input_lower.startswith(keyword) for keyword in search_keywords): return True
    if user_input.endswith("?") and len(user_input_lower) > 10:
        if not user_input_lower.startswith("/"): return True
    return False

async def _handle_code_generation_and_registration(
    tool_description_for_generation: str,
    task_manager: Optional[TaskManager],
    notification_manager: Optional[NotificationManager] # Added notification_manager
):
    """
    Handles the process of generating code via CodeService, saving it, and registering it as a tool.
    """
    if not tool_description_for_generation: # pragma: no cover
        print_formatted_text(ANSI(color_text("Error: Tool description for generation is empty.", CLIColors.ERROR_MESSAGE)))
        return

    print_formatted_text(ANSI(color_text(f"\nReceived tool description for CodeService generation: \"{tool_description_for_generation}\"", CLIColors.SYSTEM_MESSAGE)))

    from ai_assistant.llm_interface import ollama_client as default_llm_provider
    from ai_assistant.core import self_modification as default_self_modification_service

    code_service = CodeService(
        llm_provider=default_llm_provider,
        self_modification_service=default_self_modification_service,
        task_manager=task_manager, # Use passed task_manager
        notification_manager=notification_manager # Pass notification_manager
    )

    print(color_text(f"Requesting CodeService to generate new tool (context='NEW_TOOL')...", CLIColors.DEBUG_MESSAGE))

    generation_result = await code_service.generate_code(
        context="NEW_TOOL",
        prompt_or_description=tool_description_for_generation,
        target_path=None
    )

    if is_debug_mode():
        print_formatted_text(ANSI(color_text(f"[DEBUG] CodeService generation result: {generation_result}", CLIColors.DEBUG_MESSAGE)))

    if generation_result.get("status") != "SUCCESS_CODE_GENERATED":
        error_msg = generation_result.get("error", "CodeService failed to generate code or parse metadata.")
        logs = generation_result.get("logs", [])
        print_formatted_text(ANSI(color_text(f"CodeService Error: {error_msg}", CLIColors.ERROR_MESSAGE)))
        if logs: # pragma: no cover
            for log_entry in logs: print(color_text(f"  Log: {log_entry}", CLIColors.DEBUG_MESSAGE))

        global_reflection_log.log_execution(
            goal_description=f"CodeService new tool generation attempt for: {tool_description_for_generation}",
            plan=[{'action_type': 'CODESERVICE_GENERATE_NEW_TOOL', 'description': tool_description_for_generation}],
            execution_results=[f"CodeService failed. Status: {generation_result.get('status')}, Error: {error_msg}"],
            overall_success=False, status_override=f"CODESERVICE_GEN_FAILED_{generation_result.get('status','UNKNOWN_ERR')}"
        )
        return

    cleaned_code = generation_result.get("code_string")
    parsed_metadata = generation_result.get("metadata")

    if is_debug_mode():
        print_formatted_text(ANSI(color_text(f"[DEBUG] Cleaned code: {cleaned_code[:200] if cleaned_code else 'None'}...", CLIColors.DEBUG_MESSAGE)))
        print_formatted_text(ANSI(color_text(f"[DEBUG] Parsed metadata: {parsed_metadata}", CLIColors.DEBUG_MESSAGE)))

    if not cleaned_code or not parsed_metadata: # pragma: no cover
        print_formatted_text(ANSI(color_text("CodeService returned success status but missing code or metadata.", CLIColors.ERROR_MESSAGE)))
        global_reflection_log.log_execution(
            goal_description=f"CodeService new tool generation for: {tool_description_for_generation}",
            plan=[{'action_type': 'CODESERVICE_GENERATE_NEW_TOOL'}],
            execution_results=["CodeService reported success but returned incomplete data (missing code/metadata)."],
            overall_success=False, status_override="CODESERVICE_INCOMPLETE_DATA"
        )
        return

    global_reflection_log.log_execution(
        goal_description=f"CodeService new tool generation for: {tool_description_for_generation}",
        plan=[{'action_type': 'CODESERVICE_GENERATE_NEW_TOOL', 'description': tool_description_for_generation,
               'output_preview': cleaned_code[:150] + "..."}],
        execution_results=["CodeService successfully generated code and metadata. Review and registration to follow."],
        overall_success=True, status_override="CODESERVICE_GEN_SUCCESS"
    )

    print_formatted_text(ANSI(color_text("\n--- CodeService Generated Python Code ---", CLIColors.SYSTEM_MESSAGE)))
    print_formatted_text(ANSI(cleaned_code))
    print_formatted_text(ANSI(color_text("--- End of CodeService Generated Code ---", CLIColors.SYSTEM_MESSAGE)))

    if len(cleaned_code.splitlines()) > 3:
        print_formatted_text(ANSI(color_text("\nConducting initial automated code review...", CLIColors.SYSTEM_MESSAGE)))
        current_code = cleaned_code
        review_results: Optional[Dict[str, Any]] = None

        try:
            review_results = await tool_system_instance.execute_tool(
                "request_code_review_tool",
                args=(current_code, tool_description_for_generation),
                kwargs={'attempt_number': 1}
            )
        except tool_system.ToolNotFoundError: # pragma: no cover
            print_formatted_text(ANSI(color_text("Error: 'request_code_review_tool' not found. Proceeding without review.", CLIColors.ERROR_MESSAGE)))
            review_results = {"status": "review_tool_missing", "comments": "Review tool not found."}
        except Exception as e: # pragma: no cover
            print_formatted_text(ANSI(color_text(f"Error during initial code review: {e}. Proceeding without further refinement.", CLIColors.ERROR_MESSAGE)))
            review_results = {"status": "review_error", "comments": f"Initial review failed: {e}"}

        if review_results:
            initial_review_status_str = review_results.get('status', 'N/A').upper()
            status_color = CLIColors.SYSTEM_MESSAGE
            if initial_review_status_str == 'APPROVED': status_color = CLIColors.AI_RESPONSE
            elif initial_review_status_str in ['REJECTED', 'ERROR', 'REVIEW_TOOL_MISSING', 'REVIEW_ERROR']: status_color = CLIColors.ERROR_MESSAGE # pragma: no cover
            print_formatted_text(ANSI(color_text(f"Initial Review Status: {initial_review_status_str}", status_color)))
            if review_results.get('comments'): print_formatted_text(ANSI(color_text(f"Initial Review Comments: {review_results.get('comments', 'No comments.')}", CLIColors.SYSTEM_MESSAGE)))
            if review_results.get('suggestions'): print_formatted_text(ANSI(color_text(f"Initial Review Suggestions:\n{review_results['suggestions']}", CLIColors.SYSTEM_MESSAGE)))


        if review_results and review_results.get('status') == "requires_changes": # pragma: no cover
            print_formatted_text(ANSI(color_text("\nCode requires changes. Attempting automated refinement...", CLIColors.SYSTEM_MESSAGE)))
            refinement_agent = RefinementAgent()
            max_refinement_attempts = 2
            for attempt in range(max_refinement_attempts):
                refinement_attempt_count = attempt + 1
                print_formatted_text(ANSI(color_text(f"Refinement Attempt {refinement_attempt_count}/{max_refinement_attempts}...", CLIColors.SYSTEM_MESSAGE)))
                if not review_results:
                    break
                refined_code_str = await refinement_agent.refine_code(current_code, tool_description_for_generation, review_results)
                if not refined_code_str or not refined_code_str.strip(): break
                current_code = refined_code_str
                try:
                    review_results = await tool_system_instance.execute_tool("request_code_review_tool", args=(current_code, tool_description_for_generation), kwargs={'attempt_number': refinement_attempt_count + 1})
                except Exception as e:
                    review_results = {"status": "review_error", "comments": f"Failed to review refined code: {e}"}
                    break
                if review_results and (
                    review_results.get('status') == "approved" or 
                    review_results.get('status') == "rejected" or 
                    review_results.get('status') == "review_error"
                ): break
            cleaned_code = current_code

        if review_results and review_results.get('status') not in ["approved", None]: # pragma: no cover
            print_formatted_text(ANSI(color_text("\nLLM-generated code did not pass automated review or review process encountered issues.", CLIColors.ERROR_MESSAGE)))
            try:
                user_choice_after_all_reviews = await asyncio.to_thread(input, color_text("Options: [s]ave anyway, [d]iscard code. Default is discard (d): ", CLIColors.USER_INPUT))
                if user_choice_after_all_reviews.strip().lower() == 's':
                    print_formatted_text(ANSI(color_text("Proceeding to save code despite final review findings...", CLIColors.SYSTEM_MESSAGE)))
                else:
                    print_formatted_text(ANSI(color_text("Generated code discarded based on final review and user choice.", CLIColors.SYSTEM_MESSAGE)))
                    return
            except EOFError: # pragma: no cover
                print_formatted_text(ANSI(color_text("\nInput cancelled. Discarding code.", CLIColors.SYSTEM_MESSAGE)))
                return
        elif review_results and review_results.get('status') == 'approved':
             print_formatted_text(ANSI(color_text("Code review approved!", CLIColors.AI_RESPONSE)))
    else:
        print_formatted_text(ANSI(color_text("\nGenerated code is too short, automated code review was skipped.", CLIColors.SYSTEM_MESSAGE)))

    print_formatted_text(ANSI(color_text("\n" + "="*60, CLIColors.SYSTEM_MESSAGE)))
    print_formatted_text(ANSI(color_text("WARNING: This is LLM-generated code. Review carefully before saving or using.", CLIColors.ERROR_MESSAGE)))
    print_formatted_text(ANSI(color_text("="*60 + "\n", CLIColors.SYSTEM_MESSAGE)))

    use_suggested_details = False
    derived_filename = ""
    function_name_from_meta = parsed_metadata.get("suggested_function_name")
    tool_name_from_meta = parsed_metadata.get("suggested_tool_name")
    description_from_meta = parsed_metadata.get("suggested_description")

    if function_name_from_meta and tool_name_from_meta and description_from_meta:
        derived_filename_base = re.sub(r'[^\w_]', '', function_name_from_meta)
        if not derived_filename_base: derived_filename_base = "generated_tool" # pragma: no cover
        derived_filename = os.path.basename(derived_filename_base + ".py")

        print_formatted_text(ANSI(color_text("\nCodeService Suggested Details:", CLIColors.SYSTEM_MESSAGE)))
        print_formatted_text(ANSI(color_text(f"  Filename:      {derived_filename}", CLIColors.SYSTEM_MESSAGE)))
        print_formatted_text(ANSI(color_text(f"  Function name: {function_name_from_meta}", CLIColors.SYSTEM_MESSAGE)))
        print_formatted_text(ANSI(color_text(f"  Tool name:     {tool_name_from_meta}", CLIColors.SYSTEM_MESSAGE)))
        print_formatted_text(ANSI(color_text(f"  Description:   {description_from_meta}", CLIColors.SYSTEM_MESSAGE)))

        try: # pragma: no cover
            confirm_suggested = input(color_text("Use these details to save and register? (y/n): ", CLIColors.USER_INPUT)).strip().lower()
            if confirm_suggested == 'y': use_suggested_details = True
            elif confirm_suggested == 'n': print_formatted_text(ANSI(color_text("Okay, you can provide details manually or cancel.", CLIColors.SYSTEM_MESSAGE)))
            else: print_formatted_text(ANSI(color_text("Invalid choice. Proceeding with manual input.", CLIColors.ERROR_MESSAGE)))
        except EOFError: # pragma: no cover
            print_formatted_text(ANSI(color_text("\nInput cancelled. Proceeding with manual input or cancellation option.", CLIColors.SYSTEM_MESSAGE)))
            return
    else:
        print_formatted_text(ANSI(color_text("CodeService did not provide complete suggestions for all fields (function name, tool name, description).", CLIColors.SYSTEM_MESSAGE)))

    filepath_to_save: Optional[str] = None
    module_path_for_registration: Optional[str] = None
    function_to_register_final: Optional[str] = None
    tool_name_for_registration_final: Optional[str] = None
    tool_description_for_registration_final: Optional[str] = None
    should_save_and_register = False

    if use_suggested_details:
        filepath_to_save = os.path.join("ai_assistant", "custom_tools", derived_filename)
        function_to_register_final = function_name_from_meta
        tool_name_for_registration_final = tool_name_from_meta
        tool_description_for_registration_final = description_from_meta
        filename_base_no_py = derived_filename[:-3] if derived_filename.endswith(".py") else derived_filename
        module_path_for_registration = f"ai_assistant.custom_tools.{filename_base_no_py}"
        should_save_and_register = True
    else: # pragma: no cover
        try:
            save_choice = input(color_text("Save generated code to `ai_assistant/custom_tools/`? (y/n): ", CLIColors.USER_INPUT)).strip().lower()
            if save_choice == 'y':
                filename_input = input(color_text("Filename (e.g., my_tool.py): ", CLIColors.USER_INPUT)).strip()
                if not filename_input: return
                sanitized_basename = os.path.basename(filename_input)
                if not sanitized_basename.endswith(".py"): sanitized_basename += ".py"
                filepath_to_save = os.path.join("ai_assistant", "custom_tools", sanitized_basename)

                register_choice = input(color_text(f"Register function from '{filepath_to_save}'? (y/n): ", CLIColors.USER_INPUT)).strip().lower()
                if register_choice == 'y':
                    function_to_register_final = input(color_text("Function name: ", CLIColors.USER_INPUT)).strip()
                    tool_name_for_registration_final = input(color_text("Tool name for registration: ", CLIColors.USER_INPUT)).strip()
                    tool_description_for_registration_final = input(color_text("Tool description: ", CLIColors.USER_INPUT)).strip()
                    if all([function_to_register_final, tool_name_for_registration_final, tool_description_for_registration_final]):
                        module_path_for_registration = f"ai_assistant.custom_tools.{sanitized_basename[:-3]}"
                        should_save_and_register = True
            else:
                print_formatted_text(ANSI(color_text("Code not saved.", CLIColors.SYSTEM_MESSAGE)))
                return
        except EOFError: return

    if filepath_to_save:
        print_formatted_text(ANSI(color_text(f"Attempting to save code to {filepath_to_save} using fs_utils...", CLIColors.DEBUG_MESSAGE)))
        if write_to_file(filepath_to_save, cleaned_code):
            print_formatted_text(ANSI(color_text(f"Code successfully saved to {filepath_to_save}.", CLIColors.SYSTEM_MESSAGE)))
            global_reflection_log.log_execution(
                goal_description=f"File saving for generated tool: {tool_description_for_generation}",
                plan=[{'action_type': 'TOOL_CODE_SAVE_CLI', 'filepath': filepath_to_save}],
                execution_results=[f"Generated code successfully saved by CLI to {filepath_to_save}."],
                overall_success=True, status_override="TOOL_CODE_SAVE_SUCCESS_CLI"
            )

            if should_save_and_register and module_path_for_registration and \
               function_to_register_final and tool_name_for_registration_final and tool_description_for_registration_final:
                reg_success, reg_message = _perform_tool_registration(
                    module_path_for_registration, function_to_register_final,
                    tool_name_for_registration_final, tool_description_for_registration_final
                )
                print_formatted_text(ANSI(color_text(reg_message, CLIColors.SYSTEM_MESSAGE if reg_success else CLIColors.ERROR_MESSAGE)))
                global_reflection_log.log_execution(
                    goal_description=f"Tool registration attempt: {tool_name_for_registration_final}",
                    plan=[{'action_type': 'TOOL_REGISTER_CLI', 'details': {'name': tool_name_for_registration_final, 'module': module_path_for_registration}}],
                    execution_results=[reg_message],
                    overall_success=reg_success, status_override="TOOL_REGISTRATION_SUCCESS_CLI" if reg_success else "TOOL_REGISTRATION_FAILED_CLI"
                )
                if reg_success:
                    print_formatted_text(ANSI(color_text(f"Tool '{tool_name_for_registration_final}' registered. Attempting to generate unit test scaffold...", CLIColors.SYSTEM_MESSAGE)))

                    if not module_path_for_registration or not cleaned_code: # pragma: no cover
                        print_formatted_text(ANSI(color_text("Error: Missing module path or code content for scaffold generation.", CLIColors.ERROR_MESSAGE)))
                    else:
                        base_module_name = module_path_for_registration.split('.')[-1]
                        test_filename = f"test_{base_module_name}.py"
                        test_target_dir = os.path.join("tests", "custom_tools") 
                        os.makedirs(test_target_dir, exist_ok=True) 
                        test_target_path = os.path.join(test_target_dir, test_filename)

                        scaffold_gen_result = await code_service.generate_code(
                            context="GENERATE_UNIT_TEST_SCAFFOLD",
                            prompt_or_description=cleaned_code,
                            additional_context={"module_name_hint": module_path_for_registration},
                            target_path=test_target_path
                        )

                        if not scaffold_gen_result:
                            print_formatted_text(ANSI(color_text("Error: Failed to generate unit test scaffold - no result returned", CLIColors.ERROR_MESSAGE)))
                            return

                        status = scaffold_gen_result.get("status")
                        saved_path = scaffold_gen_result.get("saved_to_path")
                        error_msg = scaffold_gen_result.get("error", "Unknown error occurred")

                        if status == "SUCCESS_CODE_GENERATED" and saved_path:
                            print_formatted_text(ANSI(color_text(f"Unit test scaffold successfully generated and saved to: {saved_path}", CLIColors.AI_RESPONSE)))
                            global_reflection_log.log_execution(
                                goal_description=f"Unit test scaffold generation for tool {tool_name_for_registration_final}",
                                plan=[{'action_type': 'SCAFFOLD_GENERATION_CLI', 'tool_module': module_path_for_registration}],
                                execution_results=[f"Scaffold saved to {saved_path}"],
                                overall_success=True, status_override="SCAFFOLD_GEN_SAVE_SUCCESS"
                            )
                        else:
                            print_formatted_text(ANSI(color_text(f"Failed to generate or save unit test scaffold: {error_msg}", CLIColors.ERROR_MESSAGE)))
                            global_reflection_log.log_execution(
                                goal_description=f"Unit test scaffold generation for tool {tool_name_for_registration_final}",
                                plan=[{'action_type': 'SCAFFOLD_GENERATION_CLI', 'tool_module': module_path_for_registration}],
                                execution_results=[f"Scaffold generation failed. Status: {status}, Error: {error_msg}"],
                                overall_success=False, status_override=f"SCAFFOLD_GEN_FAILED_{status or 'UNKNOWN_ERR'}"
                            )
            elif filepath_to_save and not should_save_and_register: 
                 global_reflection_log.log_execution(
                    goal_description=f"File saving for generated tool (no registration): {tool_description_for_generation}",
                    plan=[{'action_type': 'TOOL_CODE_SAVE_ONLY_CLI', 'filepath': filepath_to_save}],
                    execution_results=[f"Code saved to {filepath_to_save}, registration skipped by user."],
                    overall_success=True, status_override="TOOL_CODE_SAVE_ONLY_SUCCESS_CLI"
                )
        else: 
            print_formatted_text(ANSI(color_text(f"Failed to save code to {filepath_to_save} using fs_utils.", CLIColors.ERROR_MESSAGE)))
            global_reflection_log.log_execution(
                goal_description=f"File saving for generated tool: {tool_description_for_generation}",
                plan=[{'action_type': 'TOOL_CODE_SAVE_CLI', 'filepath': filepath_to_save}],
                execution_results=[f"Failed to save code by CLI to {filepath_to_save}."],
                overall_success=False, status_override="TOOL_CODE_SAVE_FAILED_CLI"
            )
            return 

def _perform_tool_registration(module_path: str, function_name: str, tool_name: str, description: str) -> Tuple[bool, str]:
    try:
        if module_path in sys.modules:
            importlib.reload(sys.modules[module_path])
        imported_module = importlib.import_module(module_path)
        function_object = getattr(imported_module, function_name)
        tool_system_instance.register_tool(
            tool_name=tool_name,
            description=description,
            module_path=module_path,
            function_name_in_module=function_name,
            tool_type="dynamic", 
            func_callable=function_object
        )
        log_event(event_type="TOOL_REGISTERED_MANUAL",description=f"Tool '{tool_name}' was manually registered via CLI.",source="cli._perform_tool_registration",metadata={"tool_name": tool_name,"module_path": module_path,"function_name": function_name, "description": description})
        return True, f"Tool '{tool_name}' from '{module_path}.{function_name}' registered successfully."
    except ModuleNotFoundError: return False, f"Error: Module '{module_path}' not found."
    except AttributeError: return False, f"Error: Function '{function_name}' not found in module '{module_path}'."
    except tool_system.ToolAlreadyRegisteredError: return False, f"Error: Tool name '{tool_name}' is already registered." 
    except Exception as e: return False, f"An unexpected error occurred during tool registration: {e}"


async def _process_command_wrapper(prompt: str, orchestrator: DynamicOrchestrator, queue: asyncio.Queue):
    """Wraps orchestrator processing, handles learning, and puts results on a queue."""
    try:
        success, response = await orchestrator.process_prompt(prompt)
        status_message_str = format_status("Task completed", True) if success else format_status("Task failed", False)

        await queue.put({
            "type": "status_update",
            "message": status_message_str,
            "prompt_context": prompt
        })

        if AUTONOMOUS_LEARNING_ENABLED and response and success:
            learned_facts = await learn_facts_from_interaction(prompt, response, AUTONOMOUS_LEARNING_ENABLED)
            if learned_facts:
                await queue.put({
                    "type": "learning_result",
                    "facts": learned_facts,
                    "original_prompt": prompt
                })

        await queue.put({"type": "command_result", "prompt": prompt, "success": success, "response": response})

    except Exception as e:
        error_msg_text = f"Error processing '{prompt}': {type(e).__name__}"
        log_event(
            event_type="CLI_WRAPPER_ERROR",
            description=error_msg_text,
            source="cli._process_command_wrapper",
            metadata={"prompt": prompt, "error": str(e), "traceback": traceback.format_exc()}
        )
        await queue.put({
            "type": "status_update",
            "message": format_message("ERROR", error_msg_text, CLIColors.ERROR_MESSAGE),
            "prompt_context": prompt
        })
        await queue.put({
            "type": "command_result",
            "prompt": prompt,
            "success": False,
            "response": error_msg_text
        })

async def _handle_cli_results(queue: asyncio.Queue):
    """Checks the queue and prints any results."""
    while not queue.empty():
        try:
            result_item = queue.get_nowait()
            item_type = result_item.get("type")

            if item_type == "status_update":
                print_formatted_text(result_item.get('message'))
            elif item_type == "command_result":
                original_prompt = result_item.get("prompt", "Unknown prompt")
                success = result_item.get("success")
                response_msg = result_item.get("response")

                print_formatted_text(format_header(f"Result for: {original_prompt}"))
                if success:
                    print_formatted_text(format_message("AI", response_msg, CLIColors.AI_RESPONSE))
                else:
                    print_formatted_text(format_message("ERROR", response_msg, CLIColors.ERROR_MESSAGE))
            elif item_type == "learning_result":
                facts = result_item.get("facts", [])
                learned_from_prompt = result_item.get("original_prompt", "a recent interaction")
                if facts:
                    print_formatted_text(format_message("LEARNED", f"From '{learned_from_prompt}', I've noted: {', '.join(facts)}", CLIColors.SUCCESS))
            queue.task_done()
        except asyncio.QueueEmpty:
            break
        except Exception as e: # pragma: no cover
            print_formatted_text(ANSI(color_text(f"\nError displaying result: {e}", CLIColors.ERROR_MESSAGE)))

async def periodic_results_processor(queue: asyncio.Queue, running_event: asyncio.Event):
    """Periodically checks the queue and displays results if the CLI is running."""
    while running_event.is_set():
        if not queue.empty():
            await _handle_cli_results(queue)
        await asyncio.sleep(0.1)

_task_manager_cli_instance: Optional[TaskManager] = None
# _notification_manager_cli_instance is already declared at the top of the file

async def start_cli():
    global _pending_tool_confirmation_details, _orchestrator, _results_queue, _task_manager_cli_instance, _notification_manager_cli_instance

    # Instantiate NotificationManager first
    _notification_manager_cli_instance = NotificationManager()

    # Instantiate TaskManager first as other components might need it.
    _task_manager_cli_instance = TaskManager(notification_manager=_notification_manager_cli_instance)


    print_formatted_text(ANSI("\n"))
    print_formatted_text(draw_separator())
    print_formatted_text(format_header("AI Assistant CLI"))
    print_formatted_text(format_message("WELCOME", "Interactive AI Assistant Ready", CLIColors.SUCCESS))
    print_formatted_text(format_message("INFO", "Type /help to see available commands", CLIColors.SYSTEM_MESSAGE))
    print_formatted_text(draw_separator())
    print_formatted_text(ANSI("\n"))
    
    insights_file_path_actual = os.path.join(os.path.expanduser("~"), ".ai_assistant", "actionable_insights.json")
    os.makedirs(os.path.dirname(insights_file_path_actual), exist_ok=True)

    # Pass _task_manager_cli_instance to components that need it.
    # LearningAgent needs it for its ActionExecutor.
    learning_agent = LearningAgent(
        insights_filepath=insights_file_path_actual,
        task_manager=_task_manager_cli_instance,
        notification_manager=_notification_manager_cli_instance
    )

    # ActionExecutor for DynamicOrchestrator also needs TaskManager and NotificationManager.
    action_executor_for_orchestrator = ActionExecutor(
        learning_agent=learning_agent,
        task_manager=_task_manager_cli_instance,
        notification_manager=_notification_manager_cli_instance
    )

    execution_agent = ExecutionAgent()
    planner_agent = PlannerAgent()

    _orchestrator = DynamicOrchestrator(
        planner=planner_agent,
        executor=execution_agent,
        learning_agent=learning_agent,
        action_executor=action_executor_for_orchestrator,
        task_manager=_task_manager_cli_instance,
        notification_manager=_notification_manager_cli_instance
    )
    _results_queue = asyncio.Queue()

    session = PromptSession(format_input_prompt())
    user_command_tasks: List[asyncio.Task] = []
    cli_running_event = asyncio.Event()
    cli_running_event.set()

    results_processor_task = asyncio.create_task(
        periodic_results_processor(_results_queue, cli_running_event)
    )

    with patch_stdout():
        try:
            while True:
                # Display unread notifications BEFORE the prompt
                if _notification_manager_cli_instance:
                    unread_notifications = _notification_manager_cli_instance.get_notifications(
                        status_filter=NotificationStatus.UNREAD, limit=5 # Display up to 5
                    )
                    if unread_notifications:
                        print_formatted_text(draw_separator(char='-', color=CLIColors.SYSTEM_MESSAGE))
                        # Use format_message for header for consistency if it supports color, else format_header
                        # Assuming format_header is preferred for headers.
                        print_formatted_text(format_header("Unread Notifications", color=CLIColors.WARNING_MESSAGE))
                        displayed_ids = []
                        for n in unread_notifications:
                            ts = n.timestamp.strftime('%Y-%m-%d %H:%M')
                            # Max length for summary_message part of the string
                            max_summary_len = 100
                            # Construct the main part of the message
                            message_core = f"[{ts}] {n.event_type.name}: {n.summary_message}"
                            # Truncate if necessary
                            if len(n.summary_message) > max_summary_len:
                                message_core = f"[{ts}] {n.event_type.name}: {n.summary_message[:max_summary_len-3]}..."

                            # Final formatted line including ID
                            # Ensure the total line length is also reasonable, though harder to control precisely here
                            # For now, relying on summary_message truncation primarily.
                            full_notification_line = f"- {message_core} (ID: {n.notification_id})"
                            print_formatted_text(ANSI(color_text(full_notification_line, CLIColors.INFO_MESSAGE)))
                            displayed_ids.append(n.notification_id)

                        if displayed_ids: # Mark only if some were actually displayed
                            _notification_manager_cli_instance.mark_as_read(displayed_ids)
                        print_formatted_text(draw_separator(char='-', color=CLIColors.SYSTEM_MESSAGE))
                        print_formatted_text(ANSI("")) # Blank line after notifications

                try:
                    user_command_tasks = [t for t in user_command_tasks if not t.done()]
                    user_input = await session.prompt_async()
                    
                    if user_input.strip():
                        log_event(event_type="USER_INPUT_RECEIVED", description=user_input, source="cli.start_cli", metadata={"length": len(user_input)})
                        print_formatted_text(draw_separator())
                    else:
                        await asyncio.sleep(0.01)
                        continue

                except EOFError:
                    print_formatted_text(format_message("SYSTEM", "\nGracefully shutting down (EOF)...", CLIColors.SYSTEM_MESSAGE))
                    break
                except KeyboardInterrupt:
                    print_formatted_text(format_message("SYSTEM", "\nGracefully shutting down (Ctrl+C)...", CLIColors.SYSTEM_MESSAGE))
                    break

                if not user_input.strip():
                    continue

                if user_input.startswith("/"):
                    _pending_tool_confirmation_details = None 
                    parts = user_input.split()
                    command = parts[0].lower()
                    args_cmd = parts[1:] 

                    if command == "/exit" or command == "/quit":
                        print_formatted_text(ANSI(color_text("Exiting assistant...", CLIColors.SYSTEM_MESSAGE)))
                        break
                    elif command == "/help":
                        print_formatted_text(format_header("Available Commands"))
                        
                        print_formatted_text(format_message("CMD", "/tools <action> [tool_name]", CLIColors.COMMAND))
                        print_formatted_text(ANSI(color_text("      Manage tools (list, add, remove, update)", CLIColors.SYSTEM_MESSAGE)))

                        print_formatted_text(format_message("CMD", "/projects <action> [project_name]", CLIColors.COMMAND))
                        print_formatted_text(ANSI(color_text("      Manage AI projects", CLIColors.SYSTEM_MESSAGE)))

                        print_formatted_text(format_message("CMD", "/suggestions <action>", CLIColors.COMMAND))
                        print_formatted_text(ANSI(color_text("      Manage AI suggestions and improvements", CLIColors.SYSTEM_MESSAGE)))
                        print_formatted_text(ANSI(color_text("      • list [status_filter]: List suggestions (default: pending, or 'all')", CLIColors.SYSTEM_MESSAGE)))
                        print_formatted_text(ANSI(color_text("      • approve <id> [reason]: Approve a suggestion (uses manage_suggestion_status tool).", CLIColors.SYSTEM_MESSAGE)))
                        print_formatted_text(ANSI(color_text("      • deny <id> [reason]: Deny a suggestion (uses manage_suggestion_status tool).", CLIColors.SYSTEM_MESSAGE)))
                        print_formatted_text(ANSI(color_text("      • status           : Show suggestions summary status.", CLIColors.SYSTEM_MESSAGE)))

                        print_formatted_text(format_message("CMD", "/notifications <action> [filter|id|all] [limit]", CLIColors.COMMAND))
                        print_formatted_text(ANSI(color_text("      Manage and view system notifications.", CLIColors.SYSTEM_MESSAGE)))
                        print_formatted_text(ANSI(color_text("      • list [unread|read|archived|all] [limit=10]: List notifications.", CLIColors.SYSTEM_MESSAGE)))
                        print_formatted_text(ANSI(color_text("      • mark_read <id|all|comma,separated,ids>: Mark notification(s) as read.", CLIColors.SYSTEM_MESSAGE)))
                        print_formatted_text(ANSI(color_text("      • archive <id|all|comma,separated,ids>: Archive notification(s).", CLIColors.SYSTEM_MESSAGE)))

                        print_formatted_text(format_message("CMD", "/status [component | all | item <item_type> <item_id>]", CLIColors.COMMAND))
                        print_formatted_text(ANSI(color_text("      Show system status or details for a specific item.", CLIColors.SYSTEM_MESSAGE)))
                        print_formatted_text(ANSI(color_text("      • item <item_type> <item_id> : Get details for item (type: task, suggestion, project).", CLIColors.SYSTEM_MESSAGE)))

                        print_formatted_text(format_message("CMD", "/tasks [list [active_limit] [archived_limit]]", CLIColors.COMMAND))
                        print_formatted_text(ANSI(color_text("      Show current and recent system tasks summary.", CLIColors.SYSTEM_MESSAGE)))

                        print_formatted_text(format_message("CMD", "/generate_tool_code_with_llm \"<description>\"", CLIColors.COMMAND))
                        print_formatted_text(ANSI(color_text("      Generate and register a new tool from description", CLIColors.SYSTEM_MESSAGE)))
                        
                        print_formatted_text(format_message("CMD", "/review_insights", CLIColors.COMMAND))
                        print_formatted_text(ANSI(color_text("      Review insights and propose actions", CLIColors.SYSTEM_MESSAGE)))

                        print_formatted_text(format_message("CMD", "/exit or /quit", CLIColors.COMMAND))
                        print_formatted_text(ANSI(color_text("      Exit the assistant", CLIColors.SYSTEM_MESSAGE)))
                        
                        print_formatted_text(draw_separator())

                    elif command == "/notifications":
                        if not _notification_manager_cli_instance:
                            print_formatted_text(ANSI(color_text("NotificationManager not available.", CLIColors.ERROR_MESSAGE)))
                            continue

                        action_notif = args_cmd[0].lower() if args_cmd else "list"

                        if action_notif == "list":
                            filter_str = args_cmd[1].lower() if len(args_cmd) > 1 else "unread"
                            limit_str = args_cmd[2] if len(args_cmd) > 2 else "10"
                            try:
                                limit = int(limit_str)
                                if limit == 0: limit = 1000 # Use a large number for "all" if 0 is passed by user for limit
                            except ValueError:
                                print_formatted_text(ANSI(color_text(f"Invalid limit: {limit_str}. Defaulting to 10.", CLIColors.WARNING_MESSAGE)))
                                limit = 10

                            notif_status_filter: Optional[NotificationStatus] = None
                            title_filter_str = filter_str
                            if filter_str == "unread":
                                notif_status_filter = NotificationStatus.UNREAD
                            elif filter_str == "read":
                                notif_status_filter = NotificationStatus.READ
                            elif filter_str == "archived":
                                notif_status_filter = NotificationStatus.ARCHIVED
                            elif filter_str == "all":
                                notif_status_filter = None # No filter for status
                                title_filter_str = "All"
                            else:
                                print_formatted_text(ANSI(color_text(f"Invalid filter '{filter_str}'. Use 'unread', 'read', 'archived', or 'all'. Defaulting to 'unread'.", CLIColors.ERROR_MESSAGE)))
                                notif_status_filter = NotificationStatus.UNREAD # Default to unread on invalid filter
                                title_filter_str = "unread"


                            notifications_list = _notification_manager_cli_instance.get_notifications(
                                status_filter=notif_status_filter, limit=limit
                            )
                            _print_notifications_list(notifications_list, f"Notifications ({title_filter_str.capitalize()})")

                        elif action_notif == "mark_read":
                            if len(args_cmd) < 2:
                                print_formatted_text(ANSI(color_text("Usage: /notifications mark_read <notification_id_or_all|comma,separated,ids>", CLIColors.ERROR_MESSAGE)))
                                continue
                            ids_str = args_cmd[1]
                            ids_to_mark = []
                            if ids_str.lower() == "all":
                                # Using a large limit to signify "all" for get_notifications
                                unread_notifs = _notification_manager_cli_instance.get_notifications(status_filter=NotificationStatus.UNREAD, limit=10000)
                                ids_to_mark = [n.notification_id for n in unread_notifs]
                            else:
                                ids_to_mark = [s.strip() for s in ids_str.split(',')]

                            if not ids_to_mark:
                                print_formatted_text(ANSI(color_text("No notifications specified or found to mark as read.", CLIColors.WARNING_MESSAGE)))
                            elif _notification_manager_cli_instance.mark_as_read(ids_to_mark):
                                print_formatted_text(ANSI(color_text(f"Marked {len(ids_to_mark)} notification(s) as read.", CLIColors.SUCCESS)))
                            else:
                                print_formatted_text(ANSI(color_text("No notifications were updated (they might have been already read or IDs were invalid).", CLIColors.WARNING_MESSAGE)))

                        elif action_notif == "archive":
                            if len(args_cmd) < 2:
                                print_formatted_text(ANSI(color_text("Usage: /notifications archive <notification_id_or_all|comma_separated_ids>", CLIColors.ERROR_MESSAGE)))
                                continue
                            ids_str = args_cmd[1]
                            ids_to_archive = []
                            if ids_str.lower() == "all":
                                # Archive all UNREAD or READ notifications
                                non_archived_unread = _notification_manager_cli_instance.get_notifications(status_filter=NotificationStatus.UNREAD, limit=10000)
                                non_archived_read = _notification_manager_cli_instance.get_notifications(status_filter=NotificationStatus.READ, limit=10000)
                                ids_to_archive = [n.notification_id for n in non_archived_unread]
                                ids_to_archive.extend([n.notification_id for n in non_archived_read if n.notification_id not in ids_to_archive])
                            else:
                                ids_to_archive = [s.strip() for s in ids_str.split(',')]

                            if not ids_to_archive:
                                print_formatted_text(ANSI(color_text("No notifications specified or found to archive.", CLIColors.WARNING_MESSAGE)))
                            elif _notification_manager_cli_instance.mark_as_archived(ids_to_archive):
                                print_formatted_text(ANSI(color_text(f"Archived {len(ids_to_archive)} notification(s).", CLIColors.SUCCESS)))
                            else:
                                print_formatted_text(ANSI(color_text("No notifications were updated (they might have been already archived or IDs were invalid).", CLIColors.WARNING_MESSAGE)))
                        else:
                            print_formatted_text(ANSI(color_text(f"Unknown action for /notifications: {action_notif}. Use list, mark_read, or archive.", CLIColors.ERROR_MESSAGE)))

                    elif command == "/tools":
                        if not args_cmd:
                            print_formatted_text(format_message("ERROR", "Usage: /tools <list|add|remove|info|update> [tool_name]", CLIColors.ERROR_MESSAGE))
                            continue
                        action = args_cmd[0].lower()
                        tool_name = args_cmd[1] if len(args_cmd) > 1 else None
                        if action == "add":
                            if len(args_cmd) < 2:
                                print_formatted_text(format_message("ERROR", "Usage: /tools add <tool_description>", CLIColors.ERROR_MESSAGE))
                                continue
                            description = " ".join(args_cmd[1:])
                            await _handle_code_generation_and_registration(
                                description,
                                _task_manager_cli_instance,
                                _notification_manager_cli_instance # Pass NM
                            )
                        # ... (other /tools actions remain the same)
                        elif action == "list":
                            tools = tool_system_instance.list_tools()
                            print_formatted_text(format_header("Available Tools"))
                            for name, desc in tools.items():
                                print_formatted_text(ANSI(f"{color_text(name, CLIColors.COMMAND)}: {color_text(desc, CLIColors.SYSTEM_MESSAGE)}"))
                        elif action == "remove":
                            if not tool_name:
                                print_formatted_text(format_message("ERROR", "Usage: /tools remove <tool_name>", CLIColors.ERROR_MESSAGE))
                                continue
                            if tool_system_instance.remove_tool(tool_name):
                                print_formatted_text(format_message("SUCCESS", f"Tool '{tool_name}' removed successfully", CLIColors.SUCCESS))
                            else:
                                print_formatted_text(format_message("ERROR", f"Could not remove tool '{tool_name}'. It may not exist or be a system tool.", CLIColors.ERROR_MESSAGE))
                        elif action == "info":
                            if not tool_name:
                                print_formatted_text(format_message("ERROR", "Usage: /tools info <tool_name>", CLIColors.ERROR_MESSAGE))
                                continue
                            tool_info = tool_system_instance.get_tool(tool_name)
                            if tool_info:
                                print_formatted_text(format_header(f"Tool Information: {tool_name}"))
                                print_formatted_text(ANSI(f"Description: {color_text(tool_info['description'], CLIColors.SYSTEM_MESSAGE)}"))
                                print_formatted_text(ANSI(f"Module: {color_text(tool_info['module_path'], CLIColors.SYSTEM_MESSAGE)}"))
                                print_formatted_text(ANSI(f"Function: {color_text(tool_info['function_name'], CLIColors.SYSTEM_MESSAGE)}"))
                                print_formatted_text(ANSI(f"Type: {color_text(tool_info['type'], CLIColors.SYSTEM_MESSAGE)}"))
                            else:
                                print_formatted_text(format_message("ERROR", f"Tool '{tool_name}' not found", CLIColors.ERROR_MESSAGE))
                        elif action == "update":
                            if not tool_name:
                                print_formatted_text(format_message("ERROR", "Usage: /tools update <tool_name> <new_description>", CLIColors.ERROR_MESSAGE))
                                continue
                            description = " ".join(args_cmd[2:]) if len(args_cmd) > 2 else None
                            if not description:
                                print_formatted_text(format_message("ERROR", "Please provide a new description for the tool", CLIColors.ERROR_MESSAGE))
                                continue
                            try:
                                result = await tool_system_instance.execute_tool("system_update_tool_metadata", args=(tool_name, description))
                                if result: print_formatted_text(format_message("SUCCESS", f"Tool '{tool_name}' updated successfully", CLIColors.SUCCESS))
                                else: print_formatted_text(format_message("ERROR", f"Failed to update tool '{tool_name}'", CLIColors.ERROR_MESSAGE))
                            except Exception as e: print_formatted_text(format_message("ERROR", f"Error updating tool '{tool_name}': {e}", CLIColors.ERROR_MESSAGE))
                        else: print_formatted_text(format_message("ERROR", f"Unknown tools action: {action}", CLIColors.ERROR_MESSAGE))


                    elif command == "/projects": # Unchanged for this subtask
                        if not args_cmd:
                            print_formatted_text(format_message("ERROR", "Usage: /projects <list|new|remove|info|status> [project_name]", CLIColors.ERROR_MESSAGE))
                            continue
                        action = args_cmd[0].lower()
                        project_name_or_id = args_cmd[1] if len(args_cmd) > 1 else None
                        if action == "list":
                            projects = project_manager.list_projects()
                            if projects:
                                print_formatted_text(format_header("Projects List"))
                                for proj in projects:
                                    print_formatted_text(ANSI(f"- Name: {color_text(proj['name'], CLIColors.COMMAND)} (ID: {proj['project_id']})"))
                                    print_formatted_text(ANSI(f"  Status: {color_text(proj['status'], CLIColors.SYSTEM_MESSAGE)}, Created: {proj['created_at']}"))
                                    if proj.get('description'): print_formatted_text(ANSI(f"  Description: {proj['description']}"))
                            else: print_formatted_text(format_message("INFO", "No projects found.", CLIColors.SYSTEM_MESSAGE))
                        elif action == "new":
                            if not project_name_or_id:
                                print_formatted_text(format_message("ERROR", "Usage: /projects new <project_name>", CLIColors.ERROR_MESSAGE))
                                continue
                            description = " ".join(args_cmd[2:]) if len(args_cmd) > 2 else None
                            project_manager.create_project(project_name_or_id, description)
                        elif action == "remove":
                            if not project_name_or_id:
                                print_formatted_text(format_message("ERROR", "Usage: /projects remove <project_name_or_id>", CLIColors.ERROR_MESSAGE))
                                continue
                            project_manager.remove_project(project_name_or_id)
                        elif action == "info":
                            if not project_name_or_id:
                                print_formatted_text(format_message("ERROR", "Usage: /projects info <project_name_or_id>", CLIColors.ERROR_MESSAGE))
                                continue
                            info = project_manager.get_project_info(project_name_or_id)
                            if info:
                                print_formatted_text(format_header(f"Project Info: {info['name']} (ID: {info['project_id']})"))
                                for key, value in info.items(): print_formatted_text(ANSI(f"- {key.capitalize()}: {color_text(str(value), CLIColors.SYSTEM_MESSAGE)}"))
                            else: pass # Error printed by get_project_info
                        elif action == "status":
                            if not project_name_or_id:
                                print_formatted_text(format_header("Overall Projects Status"))
                                status_info = project_manager.get_all_projects_summary_status()
                                print_formatted_text(ANSI(color_text(status_info, CLIColors.SYSTEM_MESSAGE)))
                            else:
                                status = project_manager.get_project_status(project_name_or_id)
                                if status:
                                    project_info = project_manager.get_project_info(project_name_or_id)
                                    print_formatted_text(format_header(f"Project Status: {project_info['name'] if project_info else project_name_or_id}"))
                                    print_formatted_text(ANSI(f"Status: {color_text(status, CLIColors.SYSTEM_MESSAGE)}"))
                                else: print_formatted_text(format_message("ERROR", f"Project '{project_name_or_id}' not found.", CLIColors.ERROR_MESSAGE))
                        elif action == "set_status":
                            if len(args_cmd) < 3:
                                print_formatted_text(format_message("ERROR", "Usage: /projects set_status <project_name_or_id> <new_status>", CLIColors.ERROR_MESSAGE))
                                continue
                            project_identifier = args_cmd[1]
                            new_status_val = args_cmd[2]
                            valid_statuses = ["planning", "active", "completed", "on_hold", "archived"]
                            if new_status_val.lower() not in valid_statuses:
                                print_formatted_text(format_message("ERROR", f"Invalid status '{new_status_val}'. Valid statuses are: {', '.join(valid_statuses)}", CLIColors.ERROR_MESSAGE))
                                continue
                            project_manager.update_project_status(project_identifier, new_status_val.lower())
                        else: print_formatted_text(format_message("ERROR", f"Unknown projects action: {action}", CLIColors.ERROR_MESSAGE))

                    elif command == "/suggestions":
                        if not args_cmd:
                            print_formatted_text(format_message("ERROR", "Usage: /suggestions <list|approve|deny|status> [id] [reason]", CLIColors.ERROR_MESSAGE))
                            continue
                        action = args_cmd[0].lower()

                        if action == "list":
                            status_query = args_cmd[1] if len(args_cmd) > 1 else "pending"
                            formatted_suggs = list_formatted_suggestions(status_filter=status_query) # Uses new tool
                            if formatted_suggs:
                                print_formatted_text(format_header(f"Suggestions (Status: {status_query.capitalize()})"))
                                print_formatted_text(ANSI(json.dumps(formatted_suggs, indent=2)))
                            else:
                                print_formatted_text(format_message("INFO", f"No suggestions found with status '{status_query}'.", CLIColors.SYSTEM_MESSAGE))

                        elif action == "approve" or action == "deny":
                            suggestion_id_arg = args_cmd[1] if len(args_cmd) > 1 else None
                            reason_arg = " ".join(args_cmd[2:]) if len(args_cmd) > 2 else None
                            if not suggestion_id_arg:
                                print_formatted_text(format_message("ERROR", f"Usage: /suggestions {action} <suggestion_id> [reason]", CLIColors.ERROR_MESSAGE))
                                continue
                            # The manage_suggestion_status tool will get notification_manager injected by ToolSystem
                            result_dict = manage_suggestion_status(suggestion_id_arg, action, reason_arg)
                            if result_dict.get("status") == "success":
                                print_formatted_text(format_message("SUCCESS", result_dict.get("message",""), CLIColors.SUCCESS))
                            else:
                                print_formatted_text(format_message("ERROR", result_dict.get("message",""), CLIColors.ERROR_MESSAGE))

                        elif action == "status":
                            print_formatted_text(format_header("Overall Suggestions Status"))
                            status_info = suggestion_manager_module.get_suggestions_summary_status()
                            print_formatted_text(ANSI(color_text(status_info, CLIColors.SYSTEM_MESSAGE)))
                        else:
                            print_formatted_text(format_message("ERROR", f"Unknown suggestions action: {action}. Try list, approve, deny, status.", CLIColors.ERROR_MESSAGE))

                    elif command == "/tasks":
                        active_limit_val = 5
                        archived_limit_val = 3
                        if len(args_cmd) == 0 or (len(args_cmd) > 0 and args_cmd[0].lower() == "list"):
                            list_args = args_cmd[1:] if args_cmd and args_cmd[0].lower() == "list" else []
                            if len(list_args) > 0:
                                try: active_limit_val = int(list_args[0])
                                except ValueError:
                                    print_formatted_text(format_message("ERROR", "Invalid active_limit, must be an integer.", CLIColors.ERROR_MESSAGE))
                                    continue
                            if len(list_args) > 1:
                                try: archived_limit_val = int(list_args[1])
                                except ValueError:
                                    print_formatted_text(format_message("ERROR", "Invalid archived_limit, must be an integer.", CLIColors.ERROR_MESSAGE))
                                    continue
                        else:
                            print_formatted_text(format_message("ERROR", "Usage: /tasks [list [active_limit] [archived_limit]]", CLIColors.ERROR_MESSAGE))
                            continue

                        if _task_manager_cli_instance:
                            summary_output = get_system_status_summary(_task_manager_cli_instance, active_limit_val, archived_limit_val)
                            print_formatted_text(ANSI(summary_output))
                        else: # pragma: no cover
                            print_formatted_text(format_message("ERROR","TaskManager not available.",CLIColors.ERROR_MESSAGE))

                    elif command == "/status":
                        if not args_cmd:
                            print_formatted_text(format_message("ERROR", "Usage: /status <component|all|item item_type item_id>", CLIColors.ERROR_MESSAGE))
                            continue

                        component_or_action = args_cmd[0].lower()
                        active_tasks_count = len(user_command_tasks)

                        if component_or_action == "item":
                            if len(args_cmd) < 3:
                                print_formatted_text(format_message("ERROR", "Usage: /status item <item_type> <item_id>", CLIColors.ERROR_MESSAGE))
                                continue
                            item_type_arg = args_cmd[1].lower()
                            item_id_arg = args_cmd[2]
                            details_dict = get_item_details_by_id(item_id_arg, item_type_arg, task_manager=_task_manager_cli_instance)
                            if details_dict:
                                print_formatted_text(format_header(f"Details for {item_type_arg.capitalize()} ID: {item_id_arg}"))
                                print_formatted_text(ANSI(json.dumps(details_dict, indent=2)))

                        elif component_or_action in ["tools", "all"]:
                            print_formatted_text(format_header("Tools Status"))
                            print_formatted_text(ANSI(color_text(status_reporting.get_tools_status(), CLIColors.SYSTEM_MESSAGE)))

                        elif component_or_action in ["projects", "all"]:
                            print_formatted_text(format_header("Projects Status"))
                            print_formatted_text(ANSI(color_text(status_reporting.get_projects_status(), CLIColors.SYSTEM_MESSAGE)))

                        elif component_or_action in ["suggestions", "all"]:
                            print_formatted_text(format_header("Suggestions Status"))
                            print_formatted_text(ANSI(color_text(suggestion_manager_module.get_suggestions_summary_status(), CLIColors.SYSTEM_MESSAGE)))

                        elif component_or_action in ["system", "all"]:
                            print_formatted_text(format_header("Legacy System Status (use /tasks for detailed task summary)"))
                            print_formatted_text(ANSI(color_text(status_reporting.get_system_status(active_tasks_count), CLIColors.SYSTEM_MESSAGE)))
                        
                        if component_or_action == "all":
                            if "tools" not in args_cmd:
                                print_formatted_text(format_header("Tools Status"))
                                print_formatted_text(ANSI(color_text(status_reporting.get_tools_status(), CLIColors.SYSTEM_MESSAGE)))
                            if "projects" not in args_cmd:
                                print_formatted_text(format_header("Projects Status"))
                                print_formatted_text(ANSI(color_text(status_reporting.get_projects_status(), CLIColors.SYSTEM_MESSAGE)))
                            if "suggestions" not in args_cmd:
                                print_formatted_text(format_header("Suggestions Status"))
                                print_formatted_text(ANSI(color_text(suggestion_manager_module.get_suggestions_summary_status(), CLIColors.SYSTEM_MESSAGE)))
                            if "system" not in args_cmd:
                                print_formatted_text(format_header("Legacy System Status (use /tasks for detailed task summary)"))
                                print_formatted_text(ANSI(color_text(status_reporting.get_system_status(active_tasks_count), CLIColors.SYSTEM_MESSAGE)))

                        elif component_or_action not in ["tools", "projects", "suggestions", "system", "all", "item"]:
                            print_formatted_text(format_message("ERROR", f"Unknown status component: {component_or_action}", CLIColors.ERROR_MESSAGE))
                else: 
                    if is_debug_mode():
                        print_formatted_text(format_message("DEBUG", f"User input: {user_input}", CLIColors.DEBUG_MESSAGE))
                        
                    processed_as_confirmation = False
                    ai_response_for_learning = None

                    if _pending_tool_confirmation_details:
                        if is_debug_mode():
                            print_formatted_text(format_message("DEBUG", f"Pending confirmation: {_pending_tool_confirmation_details}", CLIColors.DEBUG_MESSAGE))
                            
                        pending_tool_name = _pending_tool_confirmation_details.get("tool_name")
                        if not pending_tool_name: 
                            if is_debug_mode():
                                print_formatted_text(ANSI(color_text(f"[DEBUG CLI] Pending confirmation details are incomplete (missing tool_name). Clearing.", CLIColors.ERROR_MESSAGE)))
                            _pending_tool_confirmation_details = None
                        elif user_input.lower() in ["yes", "y", "ok", "sure", "yeah", "yep"]:
                            if is_debug_mode():
                                print(color_text(f"[DEBUG CLI] User confirmed pending tool: {pending_tool_name}", CLIColors.DEBUG_MESSAGE))
                            
                            tool_to_run = pending_tool_name
                            inferred_args_list = _pending_tool_confirmation_details.get("inferred_args", [])
                            inferred_args_tuple = tuple(inferred_args_list)
                            inferred_kwargs_dict = _pending_tool_confirmation_details.get("inferred_kwargs", {})
                            
                            try:
                                tool_result = await tool_system_instance.execute_tool(
                                    tool_to_run, 
                                    args=inferred_args_tuple, 
                                    kwargs=inferred_kwargs_dict,
                                    task_manager=_task_manager_cli_instance, # Pass TM here
                                    notification_manager=_notification_manager_cli_instance # Pass NM here
                                )
                                ai_response_for_learning = f"OK, I've run the '{tool_to_run}' tool. Result: {str(tool_result)[:500]}"
                                print_formatted_text(format_tool_execution(tool_to_run))
                                print_formatted_text(format_message("AI", ai_response_for_learning, CLIColors.AI_RESPONSE))
                                log_event(event_type="AI_TOOL_EXECUTION_RESPONSE", description=ai_response_for_learning, source="cli.handle_confirmation", metadata={"tool_name": tool_to_run, "user_input": user_input})
                            except Exception as e_exec: # pragma: no cover
                                ai_response_for_learning = f"Sorry, I encountered an error trying to run the '{tool_to_run}' tool: {e_exec}"
                                print_formatted_text(format_message("ERROR", ai_response_for_learning, CLIColors.ERROR_MESSAGE))
                                log_event(event_type="AI_TOOL_EXECUTION_FAILURE", description=ai_response_for_learning, source="cli.handle_confirmation", metadata={"tool_name": tool_to_run, "error": str(e_exec)})
                            processed_as_confirmation = True
                        elif user_input.lower() in ["no", "n", "nope", "cancel"]:
                            if is_debug_mode():
                                print_formatted_text(ANSI(color_text(f"[DEBUG CLI] User declined pending tool: {pending_tool_name}", CLIColors.DEBUG_MESSAGE)))
                            ai_response_for_learning = "Okay, I won't run that tool."
                            print_formatted_text(ANSI(color_text(f"AI: {ai_response_for_learning}", CLIColors.AI_RESPONSE)))
                            log_event(event_type="AI_TOOL_EXECUTION_DECLINED", description=ai_response_for_learning, source="cli.handle_confirmation", metadata={"tool_name": pending_tool_name})
                            processed_as_confirmation = True
                        
                        if processed_as_confirmation: 
                            _pending_tool_confirmation_details = None 
                            if is_debug_mode(): 
                                print_formatted_text(ANSI(color_text(f"[DEBUG CLI] Cleared _pending_tool_confirmation_details after yes/no.", CLIColors.DEBUG_MESSAGE)))
                            
                            if AUTONOMOUS_LEARNING_ENABLED and ai_response_for_learning:
                                learned_facts = await learn_facts_from_interaction(user_input, ai_response_for_learning, AUTONOMOUS_LEARNING_ENABLED)
                                if learned_facts:
                                    await _results_queue.put({
                                        "type": "learning_result",
                                        "facts": learned_facts,
                                        "original_prompt": f"Confirmation for '{pending_tool_name}' (User: {user_input})"
                                    })
                                    for fact in learned_facts:
                                        log_event(
                                            event_type="AUTONOMOUS_FACT_LEARNED",
                                            description=fact,
                                            source="autonomous_learning.learn_facts_from_interaction",
                                            metadata={"interaction_context": user_input[:50], "trigger": "tool_confirmation_flow"}
                                        )
                            continue
                    
                    print_formatted_text(format_message("AI", f"Working on: '{user_input}'...", CLIColors.THINKING))
                    print_formatted_text(format_thinking())
                    if _orchestrator and _results_queue:
                        task = asyncio.create_task(
                            _process_command_wrapper(user_input, _orchestrator, _results_queue)
                        )
                        user_command_tasks.append(task)
                    else: # pragma: no cover
                        print_formatted_text(ANSI(color_text("Error: Orchestrator or results queue not initialized. Cannot process in background.", CLIColors.ERROR_MESSAGE)))
        finally:
            cli_running_event.clear()
            if results_processor_task:
                try:
                    await asyncio.wait_for(results_processor_task, timeout=1.0)
                except asyncio.TimeoutError:
                    if not results_processor_task.done(): # pragma: no cover
                        results_processor_task.cancel()
                except asyncio.CancelledError: # pragma: no cover
                    pass
                try:
                    await results_processor_task
                except asyncio.CancelledError: # pragma: no cover
                    pass

            if user_command_tasks:
                print_formatted_text(ANSI("\n"))
                print_formatted_text(draw_separator())
                print_formatted_text(format_message("SYSTEM", "Cleaning up pending user commands...", CLIColors.SYSTEM_MESSAGE))
                for task in user_command_tasks: # pragma: no cover
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*user_command_tasks, return_exceptions=True)
                print_formatted_text(format_status("User commands cleanup attempt complete", True))
            
            await _handle_cli_results(_results_queue)
            
            if global_reflection_log:
                global_reflection_log.save_log()
                print_formatted_text(format_status("Reflection log saved", True))

            print_formatted_text(draw_separator())
            print(format_message("GOODBYE", "AI Assistant shutting down. Have a great day!", CLIColors.SUCCESS))
            print_formatted_text(draw_separator())
            print_formatted_text(ANSI("\n"))


if __name__ == '__main__': # pragma: no cover
    try:
        asyncio.run(start_cli())
    except KeyboardInterrupt:
        print_formatted_text(ANSI(color_text("\nCLI terminated by user (KeyboardInterrupt in __main__).", CLIColors.SYSTEM_MESSAGE)))
    except Exception as e:
        print_formatted_text(ANSI(color_text(f"\nCLI terminated due to unexpected error: {e}", CLIColors.ERROR_MESSAGE)))
        traceback.print_exc()
