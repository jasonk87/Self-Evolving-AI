# ai_assistant/communication/cli.py
import importlib
import re
import asyncio
import os
import json # Already imported
import sys
import traceback
import logging
import warnings

class BufferedStream:
    def __init__(self):
        self.buffer = []
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr

    def write(self, data):
        self.buffer.append(data)

    def flush(self):
        pass

    def isatty(self):
        return False

# Add project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from prompt_toolkit.formatted_text import ANSI
from ai_assistant.tools import tool_system # Direct import for tool_system_instance
from ai_assistant.planning.planning import PlannerAgent
from ai_assistant.planning.execution import ExecutionAgent
from ai_assistant.core.reflection import global_reflection_log
from ai_assistant.core.task_manager import TaskManager, ActiveTaskType, ActiveTaskStatus # Added ActiveTaskType
from ai_assistant.core.notification_manager import NotificationManager, NotificationStatus, Notification # Added NotificationType and Notification
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.config import is_debug_mode
from typing import Tuple, List, Dict, Any, Optional
from ai_assistant.memory.event_logger import log_event
from ai_assistant.core.autonomous_reflection import run_self_reflection_cycle, select_suggestion_for_autonomous_action
from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.learning.autonomous_learning import learn_facts_from_interaction
from ai_assistant.config import AUTONOMOUS_LEARNING_ENABLED
from ai_assistant.utils.display_utils import (
    CLIColors, color_text, format_header, format_message,
    format_status, draw_separator
)
from ai_assistant.core.refinement import RefinementAgent
from ai_assistant.code_services.service import CodeService
from ai_assistant.core.fs_utils import write_to_file
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.core import project_manager
from ai_assistant.core import suggestion_manager as suggestion_manager_module
from ai_assistant.core import status_reporting
from ai_assistant.utils.conversational_helpers import rephrase_error_message_conversationally # Added
from ai_assistant.llm_interface.ollama_client import OllamaProvider # Added
from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner # Added
from prompt_toolkit import print_formatted_text
from prompt_toolkit.application import Application
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit.layout.containers import HSplit, VSplit, Window
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.widgets import Frame, TextArea
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.controls import FormattedTextControl
import subprocess
from datetime import datetime, timezone

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
    print_formatted_text(format_header(title))
    if not notifications:
        print_formatted_text(ANSI(color_text("  No notifications to display for this filter.", CLIColors.SYSTEM_MESSAGE)))
        return
    for n in notifications:
        ts_aware = n.timestamp
        if ts_aware.tzinfo is None:
             ts_str = ts_aware.strftime('%Y-%m-%d %H:%M:%S')
        else:
             ts_str = ts_aware.strftime('%Y-%m-%d %H:%M:%S %Z')

        summary_text = n.summary_message
        max_len_summary = 120

        core_message = f"[{ts_str}] ({n.status.name}) {n.event_type.name}: {summary_text}"

        if len(core_message) > max_len_summary:
            core_message = core_message[:max_len_summary - 3] + "..."

        rel_item_info = ""
        if n.related_item_id:
            rel_item_info += f" (Related ID: {n.related_item_id}"
            if n.related_item_type:
                rel_item_info += f", Type: {n.related_item_type}"
            rel_item_info += ")"

        print_formatted_text(ANSI(color_text(f"- ID: {n.notification_id}", CLIColors.SYSTEM_MESSAGE)))
        print_formatted_text(ANSI(color_text(f"  {core_message}{rel_item_info}", CLIColors.SYSTEM_MESSAGE)))


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
    notification_manager: Optional[NotificationManager]
):
    if not tool_description_for_generation: # pragma: no cover
        print_formatted_text(ANSI(color_text("Error: Tool description for generation is empty.", CLIColors.ERROR_MESSAGE)))
        return

    print_formatted_text(ANSI(color_text(f"\nReceived tool description for CodeService generation: \"{tool_description_for_generation}\"", CLIColors.SYSTEM_MESSAGE)))

    from ai_assistant.llm_interface import ollama_client as default_llm_provider
    from ai_assistant.core import self_modification as default_self_modification_service

    code_service = CodeService(
        llm_provider=default_llm_provider,
        self_modification_service=default_self_modification_service,
        task_manager=task_manager,
        notification_manager=notification_manager
    )

    print(color_text("Requesting CodeService to generate new tool (context='NEW_TOOL')...", CLIColors.DEBUG_MESSAGE))

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
        from ai_assistant.core.models.state import ExecutionState
        state = ExecutionState(original_user_prompt=prompt, context_limits={"max_tokens": 100000})
        state = await orchestrator.process_prompt(state=state)

        success = state.current_status == "completed"
        response = ""
        if state.tool_results and len(state.tool_results) > 0:
            last_result = state.tool_results[-1]
            if last_result.get("action_name") == "orchestrator_final_answer":
                response = last_result.get("result", "")

        if not success and not response:
            response = "Task encountered errors:\n" + "\n".join(state.errors)

        status_message_str = format_status("Task completed", True) if success else format_status("Task failed", False)
        status_message_display = status_message_str.value if hasattr(status_message_str, "value") else str(status_message_str)

        await queue.put({
            "type": "status_update",
            "message": status_message_display,
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
        technical_error_for_log = f"Error processing '{prompt}': {type(e).__name__}: {str(e)}"
        technical_error_msg_for_llm = f"{type(e).__name__}: {str(e)}"
        user_friendly_error_msg = technical_error_msg_for_llm # Fallback

        # Attempt to rephrase the error
        if orchestrator and orchestrator.action_executor and \
           orchestrator.action_executor.code_service and \
           orchestrator.action_executor.code_service.llm_provider:
            try:
                rephrased_error = await rephrase_error_message_conversationally(
                    technical_error_message=technical_error_msg_for_llm,
                    original_user_query=prompt, # 'prompt' is the original user input to the command
                    llm_provider=orchestrator.action_executor.code_service.llm_provider
                )
                if rephrased_error:
                    user_friendly_error_msg = rephrased_error
            except Exception as e_rephrase: # pragma: no cover
                # Log that rephrasing failed, user will see technical error
                # Use print for CLI debug messages if no logger is set up for CLI specifically
                print(color_text(f"[CLI DEBUG] Error rephrasing direct wrapper exception: {e_rephrase}", CLIColors.DEBUG_MESSAGE))
        else: # pragma: no cover
            print(color_text("[CLI DEBUG] LLM provider not available for direct wrapper exception rephrasing.", CLIColors.DEBUG_MESSAGE))

        # Log the original, full technical error
        log_event(
            event_type="CLI_WRAPPER_ERROR",
            description=technical_error_for_log, # Log the more detailed technical error
            source="cli._process_command_wrapper",
            metadata={"prompt": prompt, "error": str(e), "traceback": traceback.format_exc()}
        )

        # Queue the (potentially rephrased) error message for display
        status_update_msg_display = format_message("ERROR", f"Error processing '{prompt}': {user_friendly_error_msg}", CLIColors.ERROR_MESSAGE)
        status_update_text = status_update_msg_display.value if hasattr(status_update_msg_display, "value") else str(status_update_msg_display)
        await queue.put({
            "type": "status_update",
            "message": status_update_text,
            "prompt_context": prompt
        })
        await queue.put({
            "type": "command_result",
            "prompt": prompt,
            "success": False,
            "response": user_friendly_error_msg # This is the message that will be displayed by _handle_cli_results
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


ASCII_BANNER = r"""  ___  ____ _    ____    ____ _  _ ____ _    _  _ _ _  _ ____    ____ _ 
  __]  |___ |    |___    |___ |  | |  | |    |  | | |\ | | __    |__| | 
  ___] |___ |___ |       |___  \/  |__| |___  \/  | | \| |__]    |  | | """

def get_git_branch() -> Optional[str]:
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except Exception:
        return None

def is_shell_command(text: str) -> bool:
    if not text.strip():
        return False
    first_word = text.strip().split()[0].lower()
    shell_commands = {"git", "dir", "ls", "cd", "python", "pip", "pytest", "npm", "node", "clear", "cls", "echo"}
    return first_word in shell_commands

def strip_ansi(text: str) -> str:
    # Match all ANSI escape sequences
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def is_verbose_mode() -> bool:
    return "--verbose" in sys.argv or "--debug-tui" in sys.argv

def should_filter_line(line: str) -> bool:
    line_stripped = line.strip()
    if not line_stripped:
        return False
    filter_prefixes = (
        "--> Strategy:",
        "Thinking:",
        "Action:",
        "Reasoning:",
        "--- Cycle ",
        ">>> [Gemini Async]",
        "<<< [Gemini Async]",
        "[DEBUG",
        "ToolSystem:",
        "ReflectionLog:",
        "GoalManagement:",
        "LearningAgent:",
        "Circuit breaker",
        "CircuitBreaker",
        "Triggered Proactive Self-Healing",
        "Fact curation process",
    )
    if any(line_stripped.startswith(p) for p in filter_prefixes):
        return True
        
    # Match standard logger outputs starting with timestamp (YYYY-MM-DD)
    if re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}', line_stripped):
        return True
        
    return False

def clean_and_filter_text(text: str) -> str:
    lines = text.splitlines(keepends=True)
    kept_lines = []
    for line in lines:
        if not should_filter_line(line):
            kept_lines.append(line)
    return "".join(kept_lines)

class TUIStreamRedirector:
    def __init__(self, text_area, app):
        self.text_area = text_area
        self.app = app
        self.original_stdout = sys.stdout

    def write(self, data):
        # Log all raw output to file
        log_file_path = os.path.join("logs", "weebo_tui.log")
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(data)
        except Exception:
            pass

        # Clean VT100/ANSI codes from stdout/stderr to make it readable in plain TextArea
        clean_data = strip_ansi(data)
        
        # If not in verbose mode, filter out verbose logs
        if not is_verbose_mode():
            clean_data = clean_and_filter_text(clean_data)
            if not clean_data:
                return
                
        loop = self.app.loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._safe_write, clean_data)
        else:
            self._safe_write(clean_data)

    def _safe_write(self, data):
        self.text_area.text += data
        self.text_area.buffer.cursor_position = len(self.text_area.text)

    def flush(self):
        pass

    def isatty(self):
        return False


class DummyStatus:
    def __init__(self, name):
        self.name = name


class DummyType:
    def __init__(self, name):
        self.name = name


class ActiveAgentTask:
    def __init__(self, agent_id, purpose, created_at=None):
        self.task_id = agent_id
        self.agent_id = agent_id
        self.task_type = DummyType("EPHEMERAL_AGENT")
        self.status = DummyStatus("RUNNING")
        self.description = f"Agent: {purpose}"
        self.created_at = created_at or datetime.now(timezone.utc)
        self.last_updated_at = None
        self.current_step_description = "Executing autonomous loops in workspace"
        self.current_sub_step_name = None
        self.error_count = 0
        self.details = {"agent_id": agent_id, "purpose": purpose}


class WeeboTUI:
    def __init__(self, task_manager, notification_manager, orchestrator):
        self.task_manager = task_manager
        self.notification_manager = notification_manager
        self.orchestrator = orchestrator
        self.results_queue = asyncio.Queue()

        self.current_view = "dashboard" # compatibility
        self.focused_section = "active_tasks" # compatibility
        self.blocked_tools = []

        # Create Layout TextAreas with focusable & dynamic heights
        self.panes = {}
        for name in [
            "active_tasks", "queued_tasks", "background_agents",
            "suggestions", "quarantine", "notifications",
            "projects", "tools", "system_status"
        ]:
            self.panes[name] = TextArea(
                focusable=True,
                read_only=True,
                wrap_lines=True
            )

        self.selected_indices = {name: 0 for name in self.panes}
        self.pane_items = {name: [] for name in self.panes}

        # TextAreas for chat and input area
        self.console_area = TextArea(focusable=True, read_only=True, text="Weebo TUI Console Ready\n", wrap_lines=True)
        self.input_area = TextArea(multiline=False, focusable=True)
        # Compatibility fields
        self.zoom_area = TextArea(focusable=True, read_only=True, text="", wrap_lines=True)

        # Build dashboard container
        self.banner_control = FormattedTextControl(ANSI(color_text(ASCII_BANNER, CLIColors.AI_RESPONSE)))
        self.banner_window = Window(content=self.banner_control, height=3)

        # Left pane (Console & Input)
        self.left_pane = HSplit([
            self.banner_window,
            Frame(self.console_area, title="Console & Chat Log"),
            Frame(self.input_area, title="Weebo Chat Input (Type prompt or /command, Tab to switch panels)", height=3)
        ])

        # Right pane (Sidebar)
        self.right_pane = HSplit([
            Frame(self.panes["active_tasks"], title="Tasks (Active/Queued)"),
            Frame(self.panes["system_status"], title="System Telemetry", height=8),
            Frame(self.panes["suggestions"], title="AI Suggestions (A:Approve, D:Deny)"),
            Frame(self.panes["notifications"], title="Notifications (R:Read, C:Clear)"),
        ], width=40)

        # Dynamic body container using VSplit
        self.body_container = VSplit([
            self.left_pane,
            self.right_pane
        ])

        # Status bars
        self.dashboard_status_bar = Window(content=FormattedTextControl(self.get_dashboard_status_text), height=1, style="reverse")

        # Full container
        self.body = HSplit([
            self.body_container,
            self.dashboard_status_bar
        ])

        # Focusable elements for Tab cycling
        self.focusable_elements = [
            self.input_area,
            self.panes["active_tasks"],
            self.panes["suggestions"],
            self.panes["notifications"],
            self.console_area
        ]

        # Initialise layout with focused_element to avoid default_focus bugs
        self.layout = Layout(self.body, focused_element=self.input_area)

        # Keybindings
        self.kb = KeyBindings()
        self.setup_keybindings()

        # Create output device before streams are redirected
        self.real_output = create_output()
        self.app = Application(layout=self.layout, key_bindings=self.kb, full_screen=True, output=self.real_output)

        # Redirect standard streams
        self.stdout_redirector = TUIStreamRedirector(self.console_area, self.app)
        self.stderr_redirector = TUIStreamRedirector(self.console_area, self.app)

        # Enable periodic update
        self.update_task = None

    async def run_interactive_command(self, func):
        """Suspends the TUI, runs an interactive terminal function, and then restores the TUI."""
        try:
            sys.stdout = self.stdout_redirector.original_stdout
            sys.stderr = sys.__stderr__
            self.restore_logging()
            await self.app.run_in_terminal(func)
        finally:
            sys.stdout = self.stdout_redirector
            sys.stderr = self.stderr_redirector
            self.redirect_logging()
            self.app.invalidate()

    def _make_height_callable(self, name):
        from prompt_toolkit.layout.dimension import Dimension
        return lambda: Dimension.exact(1)

    def get_main_container(self):
        return self.body_container

    def get_status_bar_container(self):
        return self.dashboard_status_bar

    def setup_keybindings(self):
        from prompt_toolkit.filters import Condition

        @self.kb.add('c-c')
        def _exit(event):
            event.app.exit()

        # Enter submits input in input area, or triggers details in sidebar
        @self.kb.add('enter')
        def _enter(event):
            if self.layout.has_focus(self.input_area):
                text = self.input_area.text.strip()
                if text:
                    self.input_area.text = ""
                    asyncio.create_task(self.handle_input(text))
            else:
                # If sidebar list is focused, show details of selected item
                for name in ["active_tasks", "suggestions", "notifications"]:
                    if self.layout.has_focus(self.panes[name]):
                        self.show_item_details(name)
                        break

        # Left arrow resets focus to the main input area
        @self.kb.add('left')
        def _left(event):
            self.layout.focus(self.input_area)
            self.app.invalidate()

        # Tab cycles focus
        @self.kb.add('tab')
        def _tab(event):
            curr_idx = -1
            for i, elem in enumerate(self.focusable_elements):
                if self.layout.has_focus(elem):
                    curr_idx = i
                    break
            next_idx = (curr_idx + 1) % len(self.focusable_elements)
            self.layout.focus(self.focusable_elements[next_idx])
            self.update_dashboard_displays()
            self.app.invalidate()

        @self.kb.add('s-tab')
        def _s_tab(event):
            curr_idx = -1
            for i, elem in enumerate(self.focusable_elements):
                if self.layout.has_focus(elem):
                    curr_idx = i
                    break
            prev_idx = (curr_idx - 1) % len(self.focusable_elements)
            self.layout.focus(self.focusable_elements[prev_idx])
            self.update_dashboard_displays()
            self.app.invalidate()

        # Sidebar list focused condition
        def is_sidebar_focused():
            return any(self.layout.has_focus(self.panes[name]) for name in ["active_tasks", "suggestions", "notifications"])

        # Up/Down scrolling for lists
        @self.kb.add('up', filter=Condition(is_sidebar_focused))
        def _sidebar_up(event):
            for name in ["active_tasks", "suggestions", "notifications"]:
                if self.layout.has_focus(self.panes[name]):
                    mapped_name = "tasks" if name == "active_tasks" else name
                    items = self.pane_items.get(mapped_name, [])
                    if items and self.selected_indices[name] > 0:
                        self.selected_indices[name] -= 1
                        self.update_dashboard_displays()
                    break

        @self.kb.add('down', filter=Condition(is_sidebar_focused))
        def _sidebar_down(event):
            for name in ["active_tasks", "suggestions", "notifications"]:
                if self.layout.has_focus(self.panes[name]):
                    mapped_name = "tasks" if name == "active_tasks" else name
                    items = self.pane_items.get(mapped_name, [])
                    if items and self.selected_indices[name] < len(items) - 1:
                        self.selected_indices[name] += 1
                        self.update_dashboard_displays()
                    break

        # Up/Down scrolling for console
        @self.kb.add('up', filter=Condition(lambda: self.layout.has_focus(self.console_area)))
        def _console_up(event):
            self.console_area.buffer.cursor_up()

        @self.kb.add('down', filter=Condition(lambda: self.layout.has_focus(self.console_area)))
        def _console_down(event):
            self.console_area.buffer.cursor_down()

        # Right opens details of selected item
        @self.kb.add('right', filter=Condition(is_sidebar_focused))
        def _right(event):
            for name in ["active_tasks", "suggestions", "notifications"]:
                if self.layout.has_focus(self.panes[name]):
                    self.show_item_details(name)
                    break

        # A/D to approve/deny suggestions
        @self.kb.add('a', filter=Condition(lambda: self.layout.has_focus(self.panes["suggestions"])))
        @self.kb.add('A', filter=Condition(lambda: self.layout.has_focus(self.panes["suggestions"])))
        def _approve_sug(event):
            items = self.pane_items["suggestions"]
            sel_idx = self.selected_indices["suggestions"]
            if items and sel_idx < len(items):
                item = items[sel_idx]
                sug_id = item.get('suggestion_id')
                if sug_id:
                    asyncio.create_task(self.approve_suggestion(sug_id))

        @self.kb.add('d', filter=Condition(lambda: self.layout.has_focus(self.panes["suggestions"])))
        @self.kb.add('D', filter=Condition(lambda: self.layout.has_focus(self.panes["suggestions"])))
        def _deny_sug(event):
            items = self.pane_items["suggestions"]
            sel_idx = self.selected_indices["suggestions"]
            if items and sel_idx < len(items):
                item = items[sel_idx]
                sug_id = item.get('suggestion_id')
                if sug_id:
                    asyncio.create_task(self.deny_suggestion(sug_id))

        # R retries failed tasks or marks notifications read
        @self.kb.add('r', filter=Condition(is_sidebar_focused))
        @self.kb.add('R', filter=Condition(is_sidebar_focused))
        def _retry_or_read(event):
            for name in ["active_tasks", "notifications"]:
                if self.layout.has_focus(self.panes[name]):
                    mapped_name = "tasks" if name == "active_tasks" else name
                    items = self.pane_items[mapped_name]
                    sel_idx = self.selected_indices[name]
                    if items and sel_idx < len(items):
                        item = items[sel_idx]
                        if name == "active_tasks":
                            asyncio.create_task(self.retry_task(item.task_id))
                        elif name == "notifications":
                            self.notification_manager.mark_as_read([item.notification_id])
                            self.console_area.text += f"\nSystem: Marked notification {item.notification_id} as read.\n"
                            self.selected_indices[name] = 0
                            self.update_dashboard_displays()
                    break

        # C cancels tasks or clears all notifications
        @self.kb.add('c', filter=Condition(is_sidebar_focused))
        @self.kb.add('C', filter=Condition(is_sidebar_focused))
        def _cancel_or_clear(event):
            for name in ["active_tasks", "notifications"]:
                if self.layout.has_focus(self.panes[name]):
                    mapped_name = "tasks" if name == "active_tasks" else name
                    items = self.pane_items[mapped_name]
                    sel_idx = self.selected_indices[name]
                    if name == "active_tasks":
                        if items and sel_idx < len(items):
                            item = items[sel_idx]
                            self.task_manager.update_task_status(item.task_id, ActiveTaskStatus.USER_CANCELLED, reason="Cancelled by user from TUI dashboard.")
                            self.console_area.text += f"\nSystem: Task {item.task_id} cancelled by user.\n"
                            self.selected_indices[name] = 0
                            self.update_dashboard_displays()
                    elif name == "notifications":
                        unread = self.notification_manager.get_notifications(status_filter=NotificationStatus.UNREAD, limit=10000)
                        if unread:
                            ids = [n.notification_id for n in unread]
                            self.notification_manager.mark_as_read(ids)
                            self.console_area.text += f"\nSystem: Cleared all {len(ids)} notifications.\n"
                            self.selected_indices[name] = 0
                            self.update_dashboard_displays()
                    break

    def focus_next_section(self, current_name):
        # Compatibility
        pass

    def focus_previous_section(self, current_name):
        # Compatibility
        pass

    def get_focused_pane_name_str(self):
        for name, pane in self.panes.items():
            if self.layout.has_focus(pane):
                return name
        return "input"

    def toggle_quarantine(self, tool_name):
        if not self.orchestrator:
            return
        blocked = self.orchestrator.get_blocked_tools()
        if tool_name in blocked:
            self.orchestrator.unblock_tool(tool_name)
            self.console_area.text += f"\nSystem: Manually unblocked tool '{tool_name}' from quarantine.\n"
        else:
            self.orchestrator.blocked_tools[tool_name] = {
                "blocked_at": datetime.now(timezone.utc).isoformat(),
                "reason": "Manually quarantined by user from TUI dashboard."
            }
            self.orchestrator._save_quarantine_state()
            from ai_assistant.core.events import EventEmitter
            EventEmitter.emit("quarantine_update", {"blocked_tools": self.orchestrator.blocked_tools})
            self.console_area.text += f"\nSystem: Manually quarantined tool '{tool_name}'.\n"
        self.console_area.buffer.cursor_position = len(self.console_area.text)
        self.update_dashboard_displays()

    async def retry_task(self, task_id):
        self.console_area.text += f"\nSystem: Retrying task '{task_id}'...\n"
        self.task_manager.update_task_status(task_id, ActiveTaskStatus.PLANNING, reason="Retried by user from TUI dashboard.")
        self.console_area.buffer.cursor_position = len(self.console_area.text)
        self.update_dashboard_displays()

    def zoom_selected_item(self, section_name):
        # Compatibility
        self.show_item_details(section_name)

    def show_item_details(self, section_name):
        # Compatibility mapping
        mapped_name = section_name
        if section_name == "active_tasks":
            mapped_name = "tasks"

        items = self.pane_items.get(mapped_name, [])
        sel_idx = self.selected_indices.get(section_name, 0)
        if not items or sel_idx >= len(items):
            return
        item = items[sel_idx]
        details = ""
        if mapped_name == "tasks":
            details = (
                f"\n=== Task Details ===\n"
                f"ID: {item.task_id}\n"
                f"Type: {item.task_type.name}\n"
                f"Status: {item.status.name}\n"
                f"Description: {item.description}\n"
                f"Created At: {item.created_at.isoformat()}\n"
                f"Updated At: {item.last_updated_at.isoformat() if item.last_updated_at else 'N/A'}\n"
                f"Step: {item.current_step_description or 'N/A'}\n"
                f"Substep: {item.current_sub_step_name or 'N/A'}\n"
                f"Error Count: {item.error_count}\n"
            )
            if item.details:
                details += f"\nMetadata Details:\n{json.dumps(item.details, indent=2)}\n"
        elif mapped_name == "suggestions":
            details = (
                f"\n=== Suggestion Details ===\n"
                f"ID: {item.get('suggestion_id', 'N/A')}\n"
                f"Type: {item.get('type', 'N/A')}\n"
                f"Status: {item.get('status', 'N/A')}\n"
                f"Created: {item.get('created_at', 'N/A')}\n"
                f"Description: {item.get('description', 'N/A')}\n"
            )
            if item.get('proposed_changes'):
                details += f"\nProposed Changes:\n{json.dumps(item.get('proposed_changes'), indent=2)}\n"
        elif mapped_name == "notifications":
            details = (
                f"\n=== Notification Details ===\n"
                f"ID: {item.notification_id}\n"
                f"Status: {item.status.name}\n"
                f"Type: {item.event_type.name}\n"
                f"Timestamp: {item.timestamp.isoformat()}\n"
                f"Message: {item.summary_message}\n"
            )
            if item.related_item_id:
                details += f"Related Item ID: {item.related_item_id} (Type: {item.related_item_type or 'N/A'})\n"
        
        if details:
            self.console_area.text += details + "\n"
            self.console_area.buffer.cursor_position = len(self.console_area.text)

    async def approve_suggestion(self, suggestion_id, notification_id=""):
        self.console_area.text += f"\nSystem: Approving suggestion '{suggestion_id}'...\n"
        res = manage_suggestion_status(suggestion_id, "approve", notification_manager=self.notification_manager)
        if res.get("status") == "success":
            self.console_area.text += f"System: Suggestion approved: {res.get('message')}\n"
            if notification_id:
                self.notification_manager.mark_as_read([notification_id])
            self.selected_indices["suggestions"] = 0
            self.update_dashboard_displays()
        else:
            self.console_area.text += f"System Error: Failed to approve suggestion: {res.get('message')}\n"
        self.console_area.buffer.cursor_position = len(self.console_area.text)

    async def deny_suggestion(self, suggestion_id, notification_id=""):
        self.console_area.text += f"\nSystem: Denying suggestion '{suggestion_id}'...\n"
        res = manage_suggestion_status(suggestion_id, "deny", notification_manager=self.notification_manager)
        if res.get("status") == "success":
            self.console_area.text += f"System: Suggestion denied: {res.get('message')}\n"
            if notification_id:
                self.notification_manager.mark_as_read([notification_id])
            self.selected_indices["suggestions"] = 0
            self.update_dashboard_displays()
        else:
            self.console_area.text += f"System Error: Failed to deny suggestion: {res.get('message')}\n"
        self.console_area.buffer.cursor_position = len(self.console_area.text)

    def get_active_agents_as_tasks(self):
        try:
            from ai_assistant.core.agent_manager import AgentManager
            am = AgentManager()
            base_path = am.base_path
            if not os.path.exists(base_path):
                return []
            agent_tasks = []
            for entry in os.listdir(base_path):
                agent_path = os.path.join(base_path, entry)
                if os.path.isdir(agent_path):
                    meta_file = os.path.join(agent_path, "metadata.json")
                    purpose = "unknown"
                    created_at_dt = datetime.now(timezone.utc)
                    if os.path.exists(meta_file):
                        try:
                            with open(meta_file, 'r', encoding='utf-8') as f:
                                meta = json.load(f)
                                purpose = meta.get("purpose", "unknown")
                                if "created_at" in meta:
                                    created_at_dt = datetime.fromtimestamp(meta["created_at"], tz=timezone.utc)
                        except Exception:
                            pass
                    agent_tasks.append(ActiveAgentTask(entry, purpose, created_at_dt))
            return agent_tasks
        except Exception:
            return []

    def update_dashboard_displays(self):
        tasks = self.task_manager.list_active_tasks()
        agent_tasks = self.get_active_agents_as_tasks()
        all_tasks = tasks + agent_tasks
        all_tasks.sort(key=lambda t: t.created_at, reverse=True)

        focused_pane = next((pane for pane in self.panes.values() if self.layout.has_focus(pane)), None)

        # 1. Unified Tasks Panel (Active & Queued)
        self.pane_items["tasks"] = all_tasks
        is_focused = (focused_pane == self.panes["active_tasks"])
        if not all_tasks:
            self.panes["active_tasks"].text = "No active or queued tasks.\n"
        else:
            lines = []
            for i, t in enumerate(all_tasks):
                prefix = "> " if (is_focused and i == self.selected_indices["active_tasks"]) else "  "
                status_tag = t.status.name[:4]
                lines.append(f"{prefix}[{status_tag}] {t.task_id[:12]}: {t.description[:35]}")
            self.panes["active_tasks"].text = "\n".join(lines) + "\n"

        # 2. AI Suggestions
        suggs = []
        try:
            suggs = list_formatted_suggestions(status_filter="pending")
        except Exception:
            pass
        self.pane_items["suggestions"] = suggs
        is_focused = (focused_pane == self.panes["suggestions"])
        if not suggs:
            self.panes["suggestions"].text = "No pending suggestions.\n"
        else:
            lines = []
            for i, s in enumerate(suggs):
                prefix = "> " if (is_focused and i == self.selected_indices["suggestions"]) else "  "
                lines.append(f"{prefix}{s.get('suggestion_id', 'N/A')[:8]}: {s.get('description', 'N/A')[:35]}")
            self.panes["suggestions"].text = "\n".join(lines) + "\n"

        # 3. Notifications
        unread = self.notification_manager.get_notifications(status_filter=NotificationStatus.UNREAD, limit=15)
        self.pane_items["notifications"] = unread
        is_focused = (focused_pane == self.panes["notifications"])
        if not unread:
            self.panes["notifications"].text = "No unread notifications.\n"
        else:
            lines = []
            for i, n in enumerate(unread):
                prefix = "> " if (is_focused and i == self.selected_indices["notifications"]) else "  "
                lines.append(f"{prefix}[{n.event_type.name[:4]}] {n.summary_message[:35]}")
            self.panes["notifications"].text = "\n".join(lines) + "\n"

        # 4. System Telemetry (System Status Pane)
        cwd = os.getcwd()
        branch = get_git_branch() or "N/A"
        facts_count = 0
        try:
            from ai_assistant.memory.persistent_memory import load_learned_facts
            facts = load_learned_facts()
            facts_count = len(facts) if facts else 0
        except Exception:
            pass
        blocked_tools = len(self.orchestrator.get_blocked_tools()) if self.orchestrator else 0
        
        status_lines = [
            f" CWD: {cwd}",
            f" Git Branch: {branch}",
            f" Learned Facts: {facts_count}",
            f" Blocked Tools: {blocked_tools}",
            " Ollama: Online" if self.orchestrator and getattr(self.orchestrator, "planner", None) else " Ollama: Offline",
            f" Debug Mode: {is_debug_mode()}"
        ]
        self.panes["system_status"].text = "\n".join(status_lines) + "\n"

        # Track cursor offsets
        for name, pane in self.panes.items():
            if focused_pane == pane:
                # We align active_tasks key in mapping
                mapped_key = "active_tasks" if name == "active_tasks" else name
                sel_idx = self.selected_indices.get(mapped_key, 0)
                lines_list = pane.text.split('\n')
                if sel_idx < len(lines_list):
                    pos = sum(len(line) + 1 for line in lines_list[:sel_idx])
                    pane.buffer.cursor_position = min(pos, len(pane.text))
                else:
                    pane.buffer.cursor_position = 0
            else:
                pane.buffer.cursor_position = 0

        self.app.invalidate()

    def get_dashboard_status_text(self):
        active_tasks = len(self.pane_items.get("tasks", []))
        facts_count = 0
        try:
            from ai_assistant.memory.persistent_memory import load_learned_facts
            facts = load_learned_facts()
            facts_count = len(facts) if facts else 0
        except Exception:
            pass
        
        # Determine focused name
        focused_name = "INPUT"
        for name, pane in self.panes.items():
            if self.layout.has_focus(pane):
                if name == "active_tasks":
                    focused_name = "TASKS"
                else:
                    focused_name = name.upper()
                break
        if self.layout.has_focus(self.console_area):
            focused_name = "CONSOLE"
            
        return (
            f" CWD: {os.getcwd()} | "
            f"Tasks: {active_tasks} | "
            f"Facts: {facts_count} | "
            f"Focus: {focused_name} | "
            f"Tab: Switch Focus | Enter/Right: Details | Left: Focus Input | Ctrl+C: Exit"
        )

    def get_chat_status_text(self):
        return self.get_dashboard_status_text()

    def get_status_text(self):
        return self.get_dashboard_status_text()

    def get_focused_pane_name(self):
        return self.get_focused_pane_name_str()

    async def handle_input(self, text):
        if text.startswith("/"):
            parts = text.split()
            command = parts[0].lower()
            args = parts[1:]

            if command in ["/exit", "/quit"]:
                self.app.exit()
                return
            elif command == "/help":
                self.console_area.text += (
                    "\n--- TUI Shell Help ---\n"
                    "/exit or /quit - Exit Weebo TUI\n"
                    "/help - Show this help menu\n"
                    "/tools <list|add|remove|info|update> [tool_name] - Manage custom tools\n"
                    "/projects <list|new|remove|info|status|set_status> [project_name] - Manage AI projects\n"
                    "/suggestions <list|approve|deny|status> [id] [reason] - Review code modifications\n"
                    "/notifications <list|mark_read|archive> [filter] [limit] - Manage alerts\n"
                    "/status [component|all|item type id] - View status reports\n"
                    "/tasks [list|clear] - View/Clear task database backlog\n"
                    "/task_plan <task_id> - View detailed plan & steps for a task\n"
                    "/review_insights - Trigger self-reflection & run actions\n"
                    "Native commands (git, dir, python, cd) - run inline (suspends TUI)\n"
                    "AI conversations (ordinary text) - processed by Weebo in background\n"
                    "Tab / Shift-Tab - Cycle panel focus\n"
                    "Arrows (Up/Down) - Scroll console logs or select task/notification\n"
                    "Hotkeys (in Tasks): C to cancel task, R to retry\n"
                    "Hotkeys (in AI Suggestions): A to approve, D to deny\n"
                    "Hotkeys (in Notifications): R to read, C to clear all\n"
                    "----------------------\n"
                )
                self.console_area.buffer.cursor_position = len(self.console_area.text)
                return
            elif command == "/tasks" and len(args) > 0 and args[0].lower() == "clear":
                self.task_manager.clear_all_tasks(clear_archive=True)
                self.console_area.text += "\nSystem: All tasks and archives have been cleared.\n"
                self.update_dashboard_displays()
                self.console_area.buffer.cursor_position = len(self.console_area.text)
                return
            elif command == "/tasks":
                active_limit_val = 5
                archived_limit_val = 3
                if len(args) == 0 or (len(args) > 0 and args[0].lower() == "list"):
                    list_args = args[1:] if args and args[0].lower() == "list" else []
                    if len(list_args) > 0:
                        try:
                            active_limit_val = int(list_args[0])
                        except ValueError:
                            print_formatted_text(format_message("ERROR", "Invalid active_limit, must be an integer.", CLIColors.ERROR_MESSAGE))
                            return
                    if len(list_args) > 1:
                        try:
                            archived_limit_val = int(list_args[1])
                        except ValueError:
                            print_formatted_text(format_message("ERROR", "Invalid archived_limit, must be an integer.", CLIColors.ERROR_MESSAGE))
                            return
                else:
                    print_formatted_text(format_message("ERROR", "Usage: /tasks [list [active_limit] [archived_limit]]", CLIColors.ERROR_MESSAGE))
                    return

                summary_output = get_system_status_summary(
                    task_manager=self.task_manager,
                    notification_manager=self.notification_manager,
                    active_limit=active_limit_val,
                    archived_limit=archived_limit_val
                )
                print_formatted_text(ANSI(summary_output))
                return
            elif command in ["/tools", "/generate_tool_code_with_llm"]:
                action = args[0].lower() if args else ""
                if command == "/generate_tool_code_with_llm" or action == "add":
                    description = ""
                    if command == "/generate_tool_code_with_llm":
                        description = " ".join(args)
                    else:
                        description = " ".join(args[1:])
                    
                    if not description:
                        self.console_area.text += "\nError: Please provide a description of the tool to generate.\n"
                        return
                    
                    async def run_gen():
                        await _handle_code_generation_and_registration(
                            description,
                            self.task_manager,
                            self.notification_manager
                        )
                    await self.run_interactive_command(run_gen)
                    self.update_dashboard_displays()
                    return
                elif action == "list":
                    tools = tool_system_instance.list_tools()
                    print_formatted_text(format_header("Available Tools"))
                    for name, desc in tools.items():
                        print_formatted_text(ANSI(f"{color_text(name, CLIColors.COMMAND)}: {color_text(desc, CLIColors.SYSTEM_MESSAGE)}"))
                elif action == "remove":
                    tool_name = args[1] if len(args) > 1 else None
                    if not tool_name:
                        print_formatted_text(format_message("ERROR", "Usage: /tools remove <tool_name>", CLIColors.ERROR_MESSAGE))
                        return
                    if tool_system_instance.remove_tool(tool_name):
                        print_formatted_text(format_message("SUCCESS", f"Tool '{tool_name}' removed successfully", CLIColors.SUCCESS))
                    else:
                        print_formatted_text(format_message("ERROR", f"Could not remove tool '{tool_name}'. It may not exist or be a system tool.", CLIColors.ERROR_MESSAGE))
                elif action == "info":
                    tool_name = args[1] if len(args) > 1 else None
                    if not tool_name:
                        print_formatted_text(format_message("ERROR", "Usage: /tools info <tool_name>", CLIColors.ERROR_MESSAGE))
                        return
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
                    tool_name = args[1] if len(args) > 1 else None
                    description = " ".join(args[2:]) if len(args) > 2 else None
                    if not tool_name or not description:
                        print_formatted_text(format_message("ERROR", "Usage: /tools update <tool_name> <new_description>", CLIColors.ERROR_MESSAGE))
                        return
                    try:
                        result = await tool_system_instance.execute_tool("system_update_tool_metadata", args=(tool_name, description))
                        if result:
                            print_formatted_text(format_message("SUCCESS", f"Tool '{tool_name}' updated successfully", CLIColors.SUCCESS))
                        else:
                            print_formatted_text(format_message("ERROR", f"Failed to update tool '{tool_name}'", CLIColors.ERROR_MESSAGE))
                    except Exception as e:
                        print_formatted_text(format_message("ERROR", f"Error updating tool '{tool_name}': {e}", CLIColors.ERROR_MESSAGE))
                else:
                    print_formatted_text(format_message("ERROR", f"Unknown tools action: {action}", CLIColors.ERROR_MESSAGE))
                return
            elif command == "/notifications":
                action = args[0].lower() if args else "list"
                if action == "list":
                    filter_str = args[1].lower() if len(args) > 1 else "unread"
                    limit_str = args[2] if len(args) > 2 else "10"
                    try:
                        limit = int(limit_str)
                        if limit == 0: limit = 1000
                    except ValueError:
                        print_formatted_text(ANSI(color_text(f"Invalid limit: {limit_str}. Defaulting to 10.", CLIColors.WARNING)))
                        limit = 10

                    notif_status_filter = None
                    title_filter_str = filter_str
                    if filter_str == "unread":
                        notif_status_filter = NotificationStatus.UNREAD
                    elif filter_str == "read":
                        notif_status_filter = NotificationStatus.READ
                    elif filter_str == "archived":
                        notif_status_filter = NotificationStatus.ARCHIVED
                    elif filter_str == "all":
                        notif_status_filter = None
                        title_filter_str = "All"
                    else:
                        print_formatted_text(ANSI(color_text(f"Invalid filter '{filter_str}'. Use 'unread', 'read', 'archived', or 'all'. Defaulting to 'unread'.", CLIColors.ERROR_MESSAGE)))
                        notif_status_filter = NotificationStatus.UNREAD
                        title_filter_str = "unread"

                    notifications_list = self.notification_manager.get_notifications(
                        status_filter=notif_status_filter, limit=limit
                    )
                    _print_notifications_list(notifications_list, f"Notifications ({title_filter_str.capitalize()})")
                elif action == "mark_read":
                    if len(args) < 2:
                        print_formatted_text(ANSI(color_text("Usage: /notifications mark_read <notification_id_or_all|comma,separated,ids>", CLIColors.ERROR_MESSAGE)))
                        return
                    ids_str = args[1]
                    ids_to_mark = []
                    if ids_str.lower() == "all":
                        unread_notifs = self.notification_manager.get_notifications(status_filter=NotificationStatus.UNREAD, limit=10000)
                        ids_to_mark = [n.notification_id for n in unread_notifs]
                    else:
                        ids_to_mark = [s.strip() for s in ids_str.split(',')]

                    if not ids_to_mark:
                        print_formatted_text(ANSI(color_text("No notifications specified or found to mark as read.", CLIColors.WARNING)))
                    elif self.notification_manager.mark_as_read(ids_to_mark):
                        print_formatted_text(ANSI(color_text(f"Marked {len(ids_to_mark)} notification(s) as read.", CLIColors.SUCCESS)))
                        self.selected_indices["notifications"] = 0
                        self.update_dashboard_displays()
                    else:
                        print_formatted_text(ANSI(color_text("No notifications were updated.", CLIColors.WARNING)))
                elif action == "archive":
                    if len(args) < 2:
                        print_formatted_text(ANSI(color_text("Usage: /notifications archive <notification_id_or_all|comma_separated_ids>", CLIColors.ERROR_MESSAGE)))
                        return
                    ids_str = args[1]
                    ids_to_archive = []
                    if ids_str.lower() == "all":
                        non_archived_unread = self.notification_manager.get_notifications(status_filter=NotificationStatus.UNREAD, limit=10000)
                        non_archived_read = self.notification_manager.get_notifications(status_filter=NotificationStatus.READ, limit=10000)
                        ids_to_archive = [n.notification_id for n in non_archived_unread]
                        ids_to_archive.extend([n.notification_id for n in non_archived_read if n.notification_id not in ids_to_archive])
                    else:
                        ids_to_archive = [s.strip() for s in ids_str.split(',')]

                    if not ids_to_archive:
                        print_formatted_text(ANSI(color_text("No notifications specified or found to archive.", CLIColors.WARNING)))
                    elif self.notification_manager.mark_as_archived(ids_to_archive):
                        print_formatted_text(ANSI(color_text(f"Archived {len(ids_to_archive)} notification(s).", CLIColors.SUCCESS)))
                        self.selected_indices["notifications"] = 0
                        self.update_dashboard_displays()
                    else:
                        print_formatted_text(ANSI(color_text("No notifications were updated.", CLIColors.WARNING)))
                else:
                    print_formatted_text(ANSI(color_text(f"Unknown action for /notifications: {action}.", CLIColors.ERROR_MESSAGE)))
                return
            elif command == "/projects":
                if not args:
                    print_formatted_text(format_message("ERROR", "Usage: /projects <list|new|remove|info|status|set_status> [project_name]", CLIColors.ERROR_MESSAGE))
                    return
                action = args[0].lower()
                project_name_or_id = args[1] if len(args) > 1 else None

                if action == "list":
                    projects = project_manager.list_projects()
                    if projects:
                        print_formatted_text(format_header("Projects List"))
                        for proj in projects:
                            print_formatted_text(ANSI(f"- Name: {color_text(proj['name'], CLIColors.COMMAND)} (ID: {proj['project_id']})"))
                            print_formatted_text(ANSI(f"  Status: {color_text(proj['status'], CLIColors.SYSTEM_MESSAGE)}, Created: {proj['created_at']}"))
                            if proj.get('description'): print_formatted_text(ANSI(f"  Description: {proj['description']}"))
                    else:
                        print_formatted_text(format_message("INFO", "No projects found.", CLIColors.SYSTEM_MESSAGE))
                elif action == "new":
                    if not project_name_or_id:
                        print_formatted_text(format_message("ERROR", "Usage: /projects new <project_name>", CLIColors.ERROR_MESSAGE))
                        return
                    description = " ".join(args[2:]) if len(args) > 2 else None
                    project_manager.create_project(project_name_or_id, description)
                elif action == "remove":
                    if not project_name_or_id:
                        print_formatted_text(format_message("ERROR", "Usage: /projects remove <project_name_or_id>", CLIColors.ERROR_MESSAGE))
                        return
                    project_manager.remove_project(project_name_or_id)
                elif action == "info":
                    if not project_name_or_id:
                        print_formatted_text(format_message("ERROR", "Usage: /projects info <project_name_or_id>", CLIColors.ERROR_MESSAGE))
                        return
                    info = project_manager.get_project_info(project_name_or_id)
                    if info:
                        print_formatted_text(format_header(f"Project Info: {info['name']} (ID: {info['project_id']})"))
                        for key, value in info.items():
                            print_formatted_text(ANSI(f"- {key.capitalize()}: {color_text(str(value), CLIColors.SYSTEM_MESSAGE)}"))
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
                        else:
                            print_formatted_text(format_message("ERROR", f"Project '{project_name_or_id}' not found.", CLIColors.ERROR_MESSAGE))
                elif action == "set_status":
                    if len(args) < 3:
                        print_formatted_text(format_message("ERROR", "Usage: /projects set_status <project_name_or_id> <new_status>", CLIColors.ERROR_MESSAGE))
                        return
                    project_identifier = args[1]
                    new_status_val = args[2]
                    valid_statuses = ["planning", "active", "completed", "on_hold", "archived"]
                    if new_status_val.lower() not in valid_statuses:
                        print_formatted_text(format_message("ERROR", f"Invalid status '{new_status_val}'. Valid statuses are: {', '.join(valid_statuses)}", CLIColors.ERROR_MESSAGE))
                        return
                    project_manager.update_project_status(project_identifier, new_status_val.lower())
                else:
                    print_formatted_text(format_message("ERROR", f"Unknown projects action: {action}", CLIColors.ERROR_MESSAGE))
                return
            elif command == "/suggestions":
                if not args:
                    print_formatted_text(format_message("ERROR", "Usage: /suggestions <list|approve|deny|status> [id] [reason]", CLIColors.ERROR_MESSAGE))
                    return
                action = args[0].lower()
                if action == "list":
                    status_query = args[1] if len(args) > 1 else "pending"
                    formatted_suggs = list_formatted_suggestions(status_filter=status_query)
                    if formatted_suggs:
                        print_formatted_text(format_header(f"Suggestions (Status: {status_query.capitalize()})"))
                        print_formatted_text(ANSI(json.dumps(formatted_suggs, indent=2)))
                    else:
                        print_formatted_text(format_message("INFO", f"No suggestions found with status '{status_query}'.", CLIColors.SYSTEM_MESSAGE))
                elif action in ["approve", "deny"]:
                    suggestion_id_arg = args[1] if len(args) > 1 else None
                    reason_arg = " ".join(args[2:]) if len(args) > 2 else None
                    if not suggestion_id_arg:
                        print_formatted_text(format_message("ERROR", f"Usage: /suggestions {action} <suggestion_id> [reason]", CLIColors.ERROR_MESSAGE))
                        return
                    result_dict = manage_suggestion_status(suggestion_id_arg, action, reason_arg)
                    if result_dict.get("status") == "success":
                        print_formatted_text(format_message("SUCCESS", result_dict.get("message",""), CLIColors.SUCCESS))
                        self.selected_indices["suggestions"] = 0
                        self.update_dashboard_displays()
                    else:
                        print_formatted_text(format_message("ERROR", result_dict.get("message",""), CLIColors.ERROR_MESSAGE))
                elif action == "status":
                    print_formatted_text(format_header("Overall Suggestions Status"))
                    status_info = suggestion_manager_module.get_suggestions_summary_status()
                    print_formatted_text(ANSI(color_text(status_info, CLIColors.SYSTEM_MESSAGE)))
                else:
                    print_formatted_text(format_message("ERROR", f"Unknown suggestions action: {action}.", CLIColors.ERROR_MESSAGE))
                return
            elif command == "/status":
                if not args:
                    print_formatted_text(format_message("ERROR", "Usage: /status <component|all|item type id>", CLIColors.ERROR_MESSAGE))
                    return
                component_or_action = args[0].lower()
                active_tasks_count = len(self.task_manager.list_active_tasks())

                if component_or_action == "item":
                    if len(args) < 3:
                        print_formatted_text(format_message("ERROR", "Usage: /status item <item_type> <item_id>", CLIColors.ERROR_MESSAGE))
                        return
                    item_type_arg = args[1].lower()
                    item_id_arg = args[2]
                    details_dict = get_item_details_by_id(item_id_arg, item_type_arg, task_manager=self.task_manager)
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
                    print_formatted_text(format_header("Legacy System Status"))
                    print_formatted_text(ANSI(color_text(status_reporting.get_system_status(active_tasks_count), CLIColors.SYSTEM_MESSAGE)))

                if component_or_action == "all":
                    if "tools" not in args:
                        print_formatted_text(format_header("Tools Status"))
                        print_formatted_text(ANSI(color_text(status_reporting.get_tools_status(), CLIColors.SYSTEM_MESSAGE)))
                    if "projects" not in args:
                        print_formatted_text(format_header("Projects Status"))
                        print_formatted_text(ANSI(color_text(status_reporting.get_projects_status(), CLIColors.SYSTEM_MESSAGE)))
                    if "suggestions" not in args:
                        print_formatted_text(format_header("Suggestions Status"))
                        print_formatted_text(ANSI(color_text(suggestion_manager_module.get_suggestions_summary_status(), CLIColors.SYSTEM_MESSAGE)))
                    if "system" not in args:
                        print_formatted_text(format_header("Legacy System Status"))
                        print_formatted_text(ANSI(color_text(status_reporting.get_system_status(active_tasks_count), CLIColors.SYSTEM_MESSAGE)))
                return
            elif command == "/task_plan":
                if not args or len(args) != 1:
                    print_formatted_text(format_message("ERROR", "Usage: /task_plan <task_id>", CLIColors.ERROR_MESSAGE))
                    return
                task_id_to_view = args[0]
                task = self.task_manager.get_task(task_id_to_view)
                if not task:
                    print_formatted_text(format_message("ERROR", f"Task with ID '{task_id_to_view}' not found.", CLIColors.ERROR_MESSAGE))
                    return
                if task.task_type != ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION:
                    print_formatted_text(format_message("ERROR", f"Task '{task_id_to_view}' is not a hierarchical project execution task.", CLIColors.ERROR_MESSAGE))
                    return

                print_formatted_text(format_header(f"Project Plan Details for Task: {task.task_id}"))
                print_formatted_text(ANSI(color_text(f"Overall Description: {task.description}", CLIColors.SYSTEM_MESSAGE)))
                print_formatted_text(ANSI(color_text(f"Overall Status: {task.status.name}", CLIColors.SYSTEM_MESSAGE)))

                project_name = task.details.get('project_name', 'Unnamed Project')
                user_goal_for_plan = task.details.get('user_goal', 'N/A')
                current_idx = task.details.get('current_plan_step_index', 0)

                print_formatted_text(ANSI(color_text(f"Project Name: {project_name}", CLIColors.SYSTEM_MESSAGE)))
                print_formatted_text(ANSI(color_text(f"Original User Goal: {user_goal_for_plan}", CLIColors.SYSTEM_MESSAGE)))
                print_formatted_text(ANSI(color_text(f"Current Step Index: {current_idx}", CLIColors.SYSTEM_MESSAGE)))

                plan_step_statuses = task.details.get('plan_step_statuses')
                if plan_step_statuses and isinstance(plan_step_statuses, list):
                    print_formatted_text(format_header("Plan Steps:"))
                    for i, step_info in enumerate(plan_step_statuses):
                        step_prefix = "  "
                        if i == current_idx and task.status == ActiveTaskStatus.EXECUTING_PROJECT_PLAN:
                            step_prefix = color_text("=>", CLIColors.AI_RESPONSE) + " "
                        elif i < current_idx:
                            step_prefix = color_text("✔ ", CLIColors.SUCCESS) + " "

                        print_formatted_text(ANSI(color_text(f"{step_prefix}Step {step_info.get('step_id', 'N/A')}: {step_info.get('description', 'N/A')}", CLIColors.COMMAND)))
                        print_formatted_text(ANSI(color_text(f"      Status: {step_info.get('status', 'N/A')}", CLIColors.SYSTEM_MESSAGE)))
                        if step_info.get('status') == 'failed' and step_info.get('error_message'):
                            print_formatted_text(ANSI(color_text(f"        Error: {step_info['error_message']}", CLIColors.ERROR_MESSAGE)))
                        if step_info.get('output_preview'):
                            print_formatted_text(ANSI(color_text(f"        Output Preview: {step_info['output_preview']}", CLIColors.SYSTEM_MESSAGE)))
                else:
                    print_formatted_text(format_message("WARNING", "Detailed plan step statuses not available or invalid.", CLIColors.WARNING))
                print_formatted_text(draw_separator())
                return
            elif command == "/review_insights":
                print_formatted_text(format_header("Reviewing Actionable Insights"))
                available_tools_for_reflection = tool_system_instance.list_tools()
                suggestions = run_self_reflection_cycle(available_tools_for_reflection, notification_manager=self.notification_manager)
                if suggestions:
                    print_formatted_text(format_message("INFO", f"Self-reflection generated {len(suggestions)} suggestions.", CLIColors.SYSTEM_MESSAGE))
                    selected_suggestion = await select_suggestion_for_autonomous_action(suggestions, notification_manager=self.notification_manager)
                    if selected_suggestion:
                        print_formatted_text(format_message("INFO", f"Autonomous action attempted for suggestion ID: {selected_suggestion.get('suggestion_id')}.", CLIColors.SUCCESS))
                        action_result = selected_suggestion.get('_action_result')
                        if action_result and isinstance(action_result, dict):
                            result_message = action_result.get('overall_message', action_result.get('message', 'No details.'))
                            raw_status = action_result.get('overall_status', action_result.get('status', False))
                            color = CLIColors.SYSTEM_MESSAGE
                            if isinstance(raw_status, bool):
                                color = CLIColors.SUCCESS if raw_status else CLIColors.ERROR_MESSAGE
                            elif isinstance(raw_status, str):
                                if "SUCCESS" in raw_status.upper(): color = CLIColors.SUCCESS
                                elif "PENDING" in raw_status.upper(): color = CLIColors.SYSTEM_MESSAGE
                                elif "FAIL" in raw_status.upper(): color = CLIColors.ERROR_MESSAGE
                            print_formatted_text(format_header("Autonomous Action Result"))
                            print_formatted_text(format_message("RESULT", result_message, color))
                        elif action_result:
                            print_formatted_text(format_header("Autonomous Action Result"))
                            print_formatted_text(format_message("RESULT", str(action_result), CLIColors.SYSTEM_MESSAGE))
                    else:
                        print_formatted_text(format_message("INFO", "No suggestion selected for autonomous action.", CLIColors.SYSTEM_MESSAGE))
                else:
                    print_formatted_text(format_message("INFO", "Self-reflection cycle did not produce suggestions.", CLIColors.SYSTEM_MESSAGE))
                return
            else:
                self.console_area.text += f"\nError: Unknown command '{command}'. Type /help for help.\n"
                self.console_area.buffer.cursor_position = len(self.console_area.text)
                return

        # Check shell command
        if is_shell_command(text):
            parts = text.strip().split()
            first_word = parts[0].lower()
            if first_word == "cd":
                dest_path = " ".join(parts[1:]).strip('"' + "'") if len(parts) > 1 else ""
                if not dest_path:
                    self.console_area.text += f"\n{os.getcwd()}\n"
                else:
                    try:
                        os.chdir(dest_path)
                        self.console_area.text += f"\nCWD changed to: {os.getcwd()}\n"
                    except Exception as e:
                        self.console_area.text += f"\nError: {e}\n"
                self.console_area.buffer.cursor_position = len(self.console_area.text)
                return
            elif first_word in ["clear", "cls"]:
                self.console_area.text = ""
                return

            await self.run_interactive_command(lambda: subprocess.run(text, shell=True))
            return

        # Conversational AI prompt
        self.console_area.text += f"\nWeebo (git:{get_git_branch() or 'no-git'}) [{os.getcwd()}] > {text}\n"
        self.console_area.text += f"AI Working on: '{text}'...\n"
        self.console_area.buffer.cursor_position = len(self.console_area.text)

        await _process_command_wrapper(text, self.orchestrator, self.results_queue)
        await _handle_cli_results(self.results_queue)

    def on_system_event(self, event_name: str, data: Dict[str, Any]):
        loop = self.app.loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self.handle_event_in_loop, event_name, data)

    def handle_event_in_loop(self, event_name: str, data: Dict[str, Any]):
        if event_name == "quarantine_update":
            blocked_data = data.get("blocked_tools", {})
            if isinstance(blocked_data, dict):
                self.blocked_tools = sorted(list(blocked_data.keys()))
            elif isinstance(blocked_data, list):
                self.blocked_tools = sorted(blocked_data)
        
        # Append standard thought/log updates to chat area or log to file
        if event_name == "thought_update":
            thought = data.get("thought", "")
            if thought:
                if is_verbose_mode():
                    self.console_area.text += f"Thinking: {thought}\n"
                    self.console_area.buffer.cursor_position = len(self.console_area.text)
                else:
                    log_file_path = os.path.join("logs", "weebo_tui.log")
                    try:
                        with open(log_file_path, "a", encoding="utf-8") as f:
                            f.write(f"Thinking: {thought}\n")
                    except Exception:
                        pass
        elif event_name == "log_event":
            msg = data.get("message", "")
            if msg:
                if is_verbose_mode():
                    self.console_area.text += f"{msg}\n"
                    self.console_area.buffer.cursor_position = len(self.console_area.text)
                else:
                    log_file_path = os.path.join("logs", "weebo_tui.log")
                    try:
                        with open(log_file_path, "a", encoding="utf-8") as f:
                            f.write(f"{msg}\n")
                    except Exception:
                        pass
                
        self.update_dashboard_displays()

    async def periodic_update_loop(self):
        while True:
            try:
                self.update_dashboard_displays()
            except Exception:
                pass
            await asyncio.sleep(1)

    def redirect_logging(self):
        # Redirect warnings
        self._original_showwarning = warnings.showwarning
        def custom_showwarning(message, category, filename, lineno, file=None, line=None):
            sys.stderr.write(f"Warning: {category.__name__}: {message} (at {filename}:{lineno})\n")
        warnings.showwarning = custom_showwarning

        # Redirect logging StreamHandlers (ONLY root logger handlers)
        self._original_streams = {}
        for i, handler in enumerate(logging.root.handlers):
            if isinstance(handler, logging.StreamHandler):
                self._original_streams[('root', i)] = handler.stream
                handler.stream = self.stdout_redirector

    def restore_logging(self):
        # Restore warnings
        if hasattr(self, '_original_showwarning'):
            warnings.showwarning = self._original_showwarning
            
        # Restore logging streams (ONLY root logger handlers)
        if hasattr(self, '_original_streams'):
            for i, handler in enumerate(logging.root.handlers):
                if isinstance(handler, logging.StreamHandler) and ('root', i) in self._original_streams:
                    handler.stream = self._original_streams[('root', i)]
            self._original_streams = {}

    async def start(self):
        sys.stdout = self.stdout_redirector
        sys.stderr = self.stderr_redirector
        self.redirect_logging()
        self.update_task = asyncio.create_task(self.periodic_update_loop())
        try:
            await self.app.run_async()
        finally:
            sys.stdout = self.stdout_redirector.original_stdout
            sys.stderr = sys.__stderr__
            self.restore_logging()
            if self.update_task:
                self.update_task.cancel()


async def start_cli():
    global _pending_tool_confirmation_details, _orchestrator, _results_queue, _task_manager_cli_instance, _notification_manager_cli_instance

    # Start buffering standard output/error immediately to avoid polluting the terminal
    startup_buffer = BufferedStream()
    sys.stdout = startup_buffer
    sys.stderr = startup_buffer

    # Also redirect logging stream handlers to our buffer (ONLY root logger handlers)
    original_logging_streams = {}
    for i, handler in enumerate(logging.root.handlers):
        if isinstance(handler, logging.StreamHandler):
            original_logging_streams[('root', i)] = handler.stream
            handler.stream = startup_buffer

    try:
        # Instantiate NotificationManager first
        _notification_manager_cli_instance = NotificationManager()

        # Instantiate TaskManager first as other components might need it.
        # It will load persisted active tasks.
        _task_manager_cli_instance = TaskManager(notification_manager=_notification_manager_cli_instance)

        # Resume interrupted tasks
        try:
            from ai_assistant.core.startup_services import resume_interrupted_tasks # Added import
            await resume_interrupted_tasks(_task_manager_cli_instance, _notification_manager_cli_instance)
        except Exception as e_startup_tasks: # pragma: no cover
            # Using print for critical startup error, assuming logger might not be fully ready or for visibility
            print(f"CRITICAL STARTUP ERROR: Failed to process resume_interrupted_tasks: {e_startup_tasks}")
            traceback.print_exc() # Print traceback for critical startup errors

        # Instantiate LLM Provider and Hierarchical Planner
        # Note: OllamaProvider default base_url is http://localhost:11434. Ensure it's running.
        # Consider making base_url configurable if needed.
        try:
            llm_provider = OllamaProvider()
            # Simple check to see if provider is responsive, can be expanded
            # await llm_provider.list_models_async() # Example check, might be too slow for startup
        except Exception as e_provider: # pragma: no cover
            print(f"CRITICAL STARTUP ERROR: Failed to initialize OllamaProvider: {e_provider}. Some features might not work.")
            print("Ensure Ollama is running and accessible at the configured base URL (default: http://localhost:11434).")
            llm_provider = None # Set to None so dependent services can check

        hierarchical_planner_instance = None
        if llm_provider:
            hierarchical_planner_instance = HierarchicalPlanner(llm_provider=llm_provider)
        else: # pragma: no cover
            print("WARNING: LLM Provider not available, HierarchicalPlanner will not be functional.")


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
        planner_agent = PlannerAgent() # Simple planner

        _orchestrator = DynamicOrchestrator(
            planner=planner_agent,
            executor=execution_agent,
            learning_agent=learning_agent,
            action_executor=action_executor_for_orchestrator,
            task_manager=_task_manager_cli_instance,
            notification_manager=_notification_manager_cli_instance,
            hierarchical_planner=hierarchical_planner_instance # Inject HierarchicalPlanner
        )
        _results_queue = asyncio.Queue()

        # Restore sys.stdout and sys.stderr temporarily so prompt_toolkit gets the real console
        sys.stdout = startup_buffer.original_stdout
        sys.stderr = startup_buffer.original_stderr

        # Restore logging stream handlers (ONLY root logger handlers)
        for i, handler in enumerate(logging.root.handlers):
            if isinstance(handler, logging.StreamHandler) and ('root', i) in original_logging_streams:
                handler.stream = original_logging_streams[('root', i)]

        # Start TUI Application
        tui = WeeboTUI(_task_manager_cli_instance, _notification_manager_cli_instance, _orchestrator)
        _results_queue = tui.results_queue

        # Register an event listener to update the TUI when events occur
        def tui_event_listener(event_name: str, data: Dict[str, Any]):
            tui.on_system_event(event_name, data)

        from ai_assistant.core.events import EventEmitter
        EventEmitter.register_listener(tui_event_listener)

        # Feed the buffered startup output into the WeeboTUI console area
        startup_logs = "".join(startup_buffer.buffer)
        clean_startup_logs = strip_ansi(startup_logs)
        tui.console_area.text += clean_startup_logs
        tui.console_area.buffer.cursor_position = len(tui.console_area.text)

        await tui.start()
    finally:
        # Restore sys.stdout and sys.stderr
        sys.stdout = startup_buffer.original_stdout
        sys.stderr = startup_buffer.original_stderr

        # Restore logging stream handlers (ONLY root logger handlers)
        for i, handler in enumerate(logging.root.handlers):
            if isinstance(handler, logging.StreamHandler) and ('root', i) in original_logging_streams:
                handler.stream = original_logging_streams[('root', i)]

        if global_reflection_log:
            global_reflection_log.save_log()
            print_formatted_text(format_status("Reflection log saved", True))


if __name__ == '__main__': # pragma: no cover
    try:
        asyncio.run(start_cli())
    except KeyboardInterrupt:
        print_formatted_text(ANSI(color_text("\nCLI terminated by user (KeyboardInterrupt in __main__).", CLIColors.SYSTEM_MESSAGE)))
    except Exception as e:
        print_formatted_text(ANSI(color_text(f"\nCLI terminated due to unexpected error: {e}", CLIColors.ERROR_MESSAGE)))
        traceback.print_exc()
