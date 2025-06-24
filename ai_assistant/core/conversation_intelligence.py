### START FILE: ai_assistant/core/conversation_intelligence.py ###
# ai_assistant/core/conversation_intelligence.py
import json
import asyncio
import os
import re
from typing import Dict, Optional, List, Any

from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task, CONVERSATION_HISTORY_TURNS, is_debug_mode, get_data_dir
from ai_assistant.planning.execution import ExecutionAgent # Assuming ExecutionAgent is the correct type
from ai_assistant.tools.tool_system import ToolSystem # Assuming ToolSystem is the correct type
from .reflection import global_reflection_log
from ai_assistant.memory.event_logger import log_event, get_recent_events
from ai_assistant.custom_tools.knowledge_tools import recall_facts # Added import

from ai_assistant.learning.learning import LearningAgent # Import LearningAgent
TOOL_CONFIRMATION_CONFIG_FILENAME_CI = "tool_confirmation_config.json" # For consistency
TOOL_CONFIRMATION_CONFIG_PATH_CI = os.path.join(get_data_dir(), TOOL_CONFIRMATION_CONFIG_FILENAME_CI)

MISSED_TOOL_OPPORTUNITY_PROMPT_TEMPLATE = """
You are an AI assistant evaluating if a previous user statement could have been addressed by one of your available tools, or if it's a direct response to a question you just asked.

User's Statement: {user_statement}

Recent Conversation History (AI's last turn might be a question):
{conversation_history}

Relevant Learned Facts:
{learned_facts_str}

Available Tools:
{tools_json_string}

**Project Management & Execution Tools to Consider if Relevant:**
If the user's statement relates to starting, developing, or running a software project, pay special attention to these tools if they are listed in "Available tools":
1.  `initiate_ai_project(project_name: str, project_description: str)`:
    * Use if the user wants to start a new software project.
    * `project_name` should be a concise name (e.g., "MyWebApp", "DataAnalyzer").
    * `project_description` should be the user's stated goal or a summary.
2.  `generate_code_for_project_file(project_name: str, filename: str)`:
    * Use if the user wants to generate code for a specific file within an *existing* project.
    * Identify `project_name` and `filename`.
3.  `execute_project_coding_plan(project_name: str)`:
    * Use if the user wants to generate all remaining planned code for an *existing* project.
    * Identify `project_name`.
4. `execute_python_script_in_project(project_name: str, script_filename: str, args: Optional[List[str]], timeout_seconds: int)`:
    * Use to run a specific Python script within an existing project.
    * Positional arguments for this tool are: `project_name` (string), `script_filename` (string), `args_for_the_script` (JSON list of strings), `timeout_seconds` (string representation of an integer).
    * `args_for_the_script` (the third argument to this tool) MUST be a JSON list of strings. If the script itself takes no arguments, pass an empty JSON list `[]` for this third argument.
    * `timeout_seconds` (the fourth argument) should be a string representing an integer (e.g., "60").
    * Example (no script arguments, 30s timeout): `"tool_name": "execute_python_script_in_project", "args": ["game_project", "main.py", [], "30"], "kwargs": {{}}`
    * Example (with script arguments, 60s timeout): `"tool_name": "execute_python_script_in_project", "args": ["data_project", "process.py", ["--input", "data.csv", "--output", "out.txt"], "60"], "kwargs": {{}}`

**Important Decision-Making Rules for Project-Related Tasks:**
1.  **Project Continuation/Updates**: If the user asks to 'update the game', 'continue the project', 'work on the project', 'do it', 'proceed', 'get the project started' or similar, and a specific project (e.g., "hangman", "snake") was recently discussed or initiated (check conversation history and learned facts), interpret this as a request to continue work on that project. The most appropriate tool is likely `execute_project_coding_plan` with the identified `project_name`.
2.  **Project Initiation vs. File Generation**: If the user expresses intent to create a new game, application, or any new software entity (e.g., "create the game X"), and it's not clear that a project for this entity has already been initiated in the conversation or known facts, you **MUST** prioritize suggesting or using the `initiate_ai_project` tool first. Do not suggest `generate_code_for_project_file` for a project that has not been explicitly initiated.
3.  **Argument Format**: The "args" field in your JSON response **MUST be a LIST**. Each element in this list corresponds to a positional argument for the tool.
    *   If a positional argument is expected to be a simple type (string, number, boolean), provide its string representation (e.g., `"value1"`, `"123"`, `"true"`).
    *   If a positional argument is *itself* expected to be a list (like the `args_for_the_script` parameter of `execute_python_script_in_project`), provide it as a JSON list within the main "args" list (e.g., `["project_name", "script.py", ["script_arg1", "script_arg2"], "60"]`). If this list argument is empty, use `[]`.
    The "kwargs" field **MUST be a dictionary**, e.g., `{{"key1": "valueA"}}`. If no keyword arguments are needed, use an empty dictionary `{{}}`.
 
**Clarified Rules for Continuing Project Work:**
1.  **Generating/Completing Code**: If the user asks to 'update the game', 'continue the project', 'work on the project', 'add features', or similar, AND it's implied that there are *pending coding tasks or new files to generate* for a known project (check conversation history and learned facts for project status), suggest `execute_project_coding_plan` with the identified `project_name`. This tool is for generating code based on the project's plan.
2.  **Running or Testing a Project**:
    *   If the user explicitly asks to 'run the game', 'test the project', 'see if it works', 'find the errors by running it', 'run the damn game', or similar for a known project (e.g., "hangman").
    *   OR, if the user implies the project's code generation phase is complete (e.g., `execute_project_coding_plan` recently ran and reported "no files in 'planned' state" or "nothing to do" for "hangman" or a similar game project) AND they now want to check its functionality or find errors.
    *   In these cases, **you MUST strongly prefer** suggesting or using `execute_python_script_in_project`.
    *   You will need to infer the `project_name` (e.g., "hangman") and the main `script_filename` (often "main.py", "app.py", or a name related to the project like "hangman.py"). Check learned facts or conversation history for the project's main script if known. If not known, you might need to ask or make a reasonable guess for `script_filename`.
3.  **Sequential Logic for Fixing Errors**: If `execute_project_coding_plan` was the last tool used for a project and it indicated completion (e.g., "no files in 'planned' state"), and the user then asks to "fix errors" or "see it run", the next logical step is `execute_python_script_in_project` to identify runtime issues. **Do NOT call `execute_project_coding_plan` again in this immediate sequence unless new coding tasks have been explicitly identified.** The goal of "fixing errors" often requires *finding* them first by running the code.

Analyze the user's statement in the context of the conversation history and learned facts.
**Pay close attention to the output of previous tool executions in the conversation history.** For example, if `execute_project_coding_plan` just reported that all tasks are complete for "hangman" (e.g., "Info: No files in 'planned' state found... Nothing to do."), and the user now says "run it" or "fix the errors", `execute_python_script_in_project` is the appropriate next step, not `execute_project_coding_plan` again.


Evaluation Steps:
1.  **Direct Confirmation/Response:** If the AI's last turn in history was a question (e.g., "Would you like me to run tool X?") and the user's current statement is a direct affirmation (e.g., "yes", "sure", "ok") or negation ("no", "don't"), respond with:
    `{{"is_confirmation_response": true, "confirmed_action": true/false, "tool_to_confirm": "tool_X_name_if_applicable"}}`
    If it's a confirmation for a generic question, `tool_to_confirm` can be null.
2.  **Tool Opportunity:** If not a direct confirmation, evaluate if an available tool (respecting the guidelines above) could address the user's statement:
    - Respond with a JSON object: `{{"tool_name": "...", "inferred_args": ["list", "of", "args"], "inferred_kwargs": {{"kwarg_name": "value"}}, "reasoning": "..."}}`
3.  **Tool Confirmation Settings Management:** If the user seems to want to manage tool confirmation settings (e.g., "always ask before searching", "don't ask for search anymore"), suggest 'manage_tool_confirmation_settings' if available:
    `{{"tool_name": "manage_tool_confirmation_settings", "inferred_args": ["action_to_infer", "tool_name_to_manage_if_any"], "inferred_kwargs": {{}}, "reasoning": "User wants to manage tool confirmation settings."}}`
4.  **No Tool/Confirmation:** If none of the above, respond with the exact string "NO_TOOL_RELEVANT".

Respond ONLY with the JSON object or "NO_TOOL_RELEVANT". Do not include other text or markdown.
"""

def _load_requires_confirmation_list_ci() -> List[str]:
    """
    Loads the list of tools that require user confirmation from the JSON configuration file.
    If the file doesn't exist or is invalid, it returns an empty list.
    """
    default_list: List[str] = []
    if not os.path.exists(TOOL_CONFIRMATION_CONFIG_PATH_CI):
        return default_list
    try:
        with open(TOOL_CONFIRMATION_CONFIG_PATH_CI, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                return default_list
            data = json.load(f)
        loaded_list = data.get("requires_confirmation_tools", default_list)
        if not isinstance(loaded_list, list) or not all(isinstance(item, str) for item in loaded_list):
            print(f"Warning: 'requires_confirmation_tools' in '{TOOL_CONFIRMATION_CONFIG_PATH_CI}' is not a list of strings. Using default (empty list).") # pragma: no cover
            return default_list # pragma: no cover
        return loaded_list
    except (json.JSONDecodeError, IOError) as e: # pragma: no cover
        print(f"Warning: Could not load or parse '{TOOL_CONFIRMATION_CONFIG_PATH_CI}'. Defaulting to no tools requiring confirmation. Error: {e}")
        return default_list


async def detect_missed_tool_opportunity(
    user_statement: str,
    available_tools: Dict[str, str],
    executor: ExecutionAgent,
    tool_system_instance: ToolSystem,
    learning_agent: LearningAgent, # Add LearningAgent
    llm_model_name: Optional[str] = None # Keep this last for compatibility if not all callers update immediately
) -> Optional[Dict[str, Any]]:
    """
    Detects if a user's statement could have been addressed by an available tool,
    suggests the tool, or autonomously executes it if appropriate.
    """
    if not user_statement or not available_tools:
        if is_debug_mode(): # pragma: no cover
            print(f"[DEBUG CONV_INTEL] detect_missed_tool_opportunity returning None due to empty user_statement or available_tools.")
        return None

    try:
        tools_json_string = json.dumps(available_tools, indent=2)
    except TypeError: # pragma: no cover
        print("Error: Could not serialize available_tools to JSON for LLM prompt.")
        return None

    recent_events = get_recent_events(limit=CONVERSATION_HISTORY_TURNS * 2)
    formatted_history_lines = []
    for event in reversed(recent_events):
        if event.get("event_type") == "USER_INPUT_RECEIVED":
            formatted_history_lines.append(f"User: {event.get('description', '')}")
        elif event.get("event_type") in ["AI_CONVERSATIONAL_RESPONSE", "AI_TOOL_SUGGESTION_PROMPT", "AI_AUTONOMOUS_TOOL_RESPONSE", "WEEBO_RESPONSE", "AI_TOOL_EXECUTION_RESPONSE", "AI_TOOL_EXECUTION_DECLINED", "AI_TOOL_EXECUTION_FAILURE"]:
            formatted_history_lines.append(f"AI: {event.get('description', '')}")
        if len(formatted_history_lines) >= CONVERSATION_HISTORY_TURNS * 2 : # pragma: no cover
            break
    conversation_history_for_prompt = "\n".join(reversed(formatted_history_lines))
    if not conversation_history_for_prompt:
        conversation_history_for_prompt = "No recent history available."

    # ---- Retrieve and format learned facts ----
    try:
        recalled_facts_list = recall_facts() 
        if recalled_facts_list:
            facts_for_prompt = "\n".join([f"- {fact}" for fact in recalled_facts_list[:5]])
            if len(recalled_facts_list) > 5: # pragma: no cover
                facts_for_prompt += f"\n- ...and {len(recalled_facts_list) - 5} more facts."
        else:
            facts_for_prompt = "No specific facts currently learned that seem relevant."
    except Exception as e_facts: # pragma: no cover
        if is_debug_mode():
            print(f"[DEBUG CONV_INTEL] Error recalling facts for tool detection: {e_facts}")
        facts_for_prompt = "Could not retrieve learned facts at this time."
    # ---- END Fact Retrieval ----
    
    # Escape content that might contain stray {} characters which could break .format()
    escaped_user_statement = user_statement.replace('{', '{{').replace('}', '}}')
    escaped_conversation_history = conversation_history_for_prompt.replace('{', '{{').replace('}', '}}')
    escaped_learned_facts_str = facts_for_prompt.replace('{', '{{').replace('}', '}}')
    # tools_json_string also needs escaping, especially if it becomes "{}" for an empty dict.
    escaped_tools_json_string = tools_json_string.replace('{', '{{').replace('}', '}}')

    try:
        prompt = MISSED_TOOL_OPPORTUNITY_PROMPT_TEMPLATE.format(
            user_statement=escaped_user_statement,
            tools_json_string=escaped_tools_json_string, # Use the escaped version
            conversation_history=escaped_conversation_history,
            learned_facts_str=escaped_learned_facts_str
        )
    except IndexError as e_format: # pragma: no cover
        # This is where the user's error was happening.
        print(f"CRITICAL ERROR formatting MISSED_TOOL_OPPORTUNITY_PROMPT_TEMPLATE: {e_format}")
        print("This usually means there's a stray positional placeholder '{}' in the template string,")
        print("or a named placeholder is missing from the .format() call's keyword arguments.")
        print(f"Template preview (check for stray {{}}):\n{MISSED_TOOL_OPPORTUNITY_PROMPT_TEMPLATE[:1000]}...")
        return None


    if is_debug_mode(): # pragma: no cover
        print(f"[DEBUG CONV_INTEL] Missed tool detection prompt:\n{prompt[:1000]}\n---END PROMPT (TRUNCATED)---")

    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("conversation_intelligence")

    if is_debug_mode(): # pragma: no cover
        print(f"[DEBUG CONV_INTEL] About to call invoke_ollama_model_async for tool detection. Model: {model_to_use}")

    llm_response = await invoke_ollama_model_async(prompt, model_name=model_to_use)

    if is_debug_mode(): # pragma: no cover
        print(f"[DEBUG CONV_INTEL] Raw LLM response for missed tool detection:\n'{llm_response}'")

    if not llm_response or not llm_response.strip(): # pragma: no cover
        print(f"Warning: Received no or empty response from LLM ({model_to_use}) for missed tool detection.")
        return None

    llm_response = llm_response.strip()

    if llm_response == "NO_TOOL_RELEVANT":
        if is_debug_mode(): # pragma: no cover
            print(f"[DEBUG CONV_INTEL] LLM determined NO_TOOL_RELEVANT.")
        return None

    if llm_response.startswith("```json"):
        llm_response = llm_response.lstrip("```json").rstrip("```").strip()
    elif llm_response.startswith("```"): # pragma: no cover
        llm_response = llm_response.lstrip("```").rstrip("```").strip()

    try:
        parsed_response = json.loads(llm_response)
        if not isinstance(parsed_response, dict): # pragma: no cover
            print(f"Warning: LLM response for tool/confirmation detection was not a JSON dictionary. Response: {llm_response}")
            return None

        if parsed_response.get("is_confirmation_response") is True:
            if is_debug_mode(): # pragma: no cover
                print(f"[DEBUG CONV_INTEL] LLM identified a confirmation response: {parsed_response}")
            return parsed_response

        required_keys = ["tool_name", "inferred_args", "inferred_kwargs", "reasoning"]
        for key in required_keys:
            if key not in parsed_response: # pragma: no cover
                print(f"Warning: LLM response (tool suggestion) JSON is missing key '{key}'. Response: {llm_response}")
                return None
        
        # --- Robust argument parsing ---
        tool_name_detected = parsed_response.get("tool_name")
        raw_inferred_args = parsed_response.get("inferred_args")
        raw_inferred_kwargs = parsed_response.get("inferred_kwargs", {})

        final_args_list = []
        final_kwargs_dict = {}

        if isinstance(raw_inferred_kwargs, dict):
            final_kwargs_dict = {str(k): str(v) for k, v in raw_inferred_kwargs.items()}
        elif raw_inferred_kwargs is not None: # pragma: no cover
             print(f"Warning: 'inferred_kwargs' from LLM for tool '{tool_name_detected}' was not a dict (got {type(raw_inferred_kwargs)}). Using empty dict.")

        if isinstance(raw_inferred_args, list):
            final_args_list = [str(arg) for arg in raw_inferred_args]
        elif isinstance(raw_inferred_args, dict):
            print(f"Warning: 'inferred_args' from LLM was a dictionary for tool '{tool_name_detected}'. Attempting specific extraction. Dict: {raw_inferred_args}")
            if tool_name_detected == "initiate_ai_project":
                project_name_val = raw_inferred_args.get("project_name")
                project_description_val = raw_inferred_args.get("project_description")
                if project_description_val is None and "project_description" in final_kwargs_dict: # Check if it's in kwargs
                    project_description_val = final_kwargs_dict.pop("project_description")
                
                if project_name_val is not None and project_description_val is not None:
                    final_args_list = [str(project_name_val), str(project_description_val)]
                elif project_name_val is not None: # Only name found
                    final_args_list = [str(project_name_val), f"Project description for {project_name_val} (based on user query: {user_statement})"]
                    print(f"Warning: 'project_description' was not fully resolved for '{tool_name_detected}'. Using a placeholder.")
                else: # pragma: no cover
                    print(f"Error: Could not reliably extract 'project_name' and 'project_description' for '{tool_name_detected}' from dict: {raw_inferred_args}")
            elif tool_name_detected == "generate_code_for_project_file":
                project_name_val = raw_inferred_args.get("project_name")
                filename_val = raw_inferred_args.get("filename")
                if project_name_val is not None and filename_val is not None:
                    final_args_list = [str(project_name_val), str(filename_val)]
                else: # pragma: no cover
                    print(f"Warning: Could not determine project_name and filename from inferred_args dict for {tool_name_detected}")
            else: # General fallback if it's a dict for an unknown tool structure
                final_args_list = [str(v) for v in raw_inferred_args.values()]
        elif raw_inferred_args is not None: # Present but not list or dict
             print(f"Warning: 'inferred_args' from LLM for tool '{tool_name_detected}' was not a list or dict (got {type(raw_inferred_args)}). Using empty list.")
        
        parsed_response['inferred_args'] = final_args_list
        parsed_response['inferred_kwargs'] = final_kwargs_dict
        # --- End robust argument parsing ---


        if not isinstance(parsed_response["tool_name"], str) or \
           not isinstance(parsed_response["inferred_args"], list) or \
           not all(isinstance(arg, (str, int, float, bool)) or arg is None for arg in parsed_response["inferred_args"]) or \
           not isinstance(parsed_response["inferred_kwargs"], dict) or \
           not all(isinstance(k, str) and (isinstance(v, (str, int, float, bool)) or v is None) for k, v in parsed_response["inferred_kwargs"].items()) or \
           not isinstance(parsed_response["reasoning"], str): # pragma: no cover
            print(f"Warning: LLM response (tool suggestion) JSON has incorrect types for args/kwargs after processing. Response: {parsed_response}")
            pass # Allow to proceed, but log warning. Execution might fail if types are critical.

        if tool_name_detected not in available_tools and tool_name_detected != "manage_tool_confirmation_settings": # Allow this special tool
            print(f"Warning: LLM suggested tool '{tool_name_detected}' which is not in the available tools list (and not manage_tool_confirmation_settings).") # pragma: no cover
            return None 

        if tool_name_detected == "generate_code_for_project_file":
            project_name_arg_check = final_args_list[0] if final_args_list else None
            if project_name_arg_check: # project_name_arg_check is final_args_list[0]
                from ai_assistant.custom_tools.file_system_tools import sanitize_project_name, BASE_PROJECTS_DIR
                s_name = sanitize_project_name(project_name_arg_check)
                manifest_path_check = os.path.join(BASE_PROJECTS_DIR, s_name, "_ai_project_manifest.json")
                if not os.path.exists(manifest_path_check): # pragma: no cover
                    print(f"CONV_INTEL: Project '{project_name_arg_check}' manifest not found. Proposing 'initiate_ai_project' instead.")
                    desc_for_init = f"Create project '{project_name_arg_check}' based on user query: {user_statement}"
                    new_suggestion_prompt = f"It seems the project '{project_name_arg_check}' hasn't been set up yet. Shall I create it first with the description: '{desc_for_init}'?"
                    return {
                        "is_tool_suggestion": True,
                        "tool_name": "initiate_ai_project",
                        "inferred_args": [project_name_arg_check, desc_for_init],
                        "inferred_kwargs": {},
                        "reasoning": f"Project '{project_name_arg_check}' needs to be initiated first.",
                        "suggestion_prompt": new_suggestion_prompt 
                    }
        
        if tool_name_detected == "get_self_awareness_info_and_converse" and not parsed_response.get("inferred_args"): # pragma: no cover
            if is_debug_mode():
                print(f"[DEBUG CONV_INTEL] Auto-populating 'user_input' for '{tool_name_detected}' with current user_statement: '{user_statement}'")
            parsed_response["inferred_args"] = [user_statement]

        requires_confirmation_tools = _load_requires_confirmation_list_ci()

        if is_debug_mode(): # pragma: no cover
            print(f"[DEBUG CONV_INTEL] LLM suggested tool: {tool_name_detected}. Loaded 'requires confirmation' list: {requires_confirmation_tools}")

        if tool_name_detected not in requires_confirmation_tools:
            if is_debug_mode(): # pragma: no cover
                print(f"[DEBUG CONV_INTEL] Tool '{tool_name_detected}' is NOT in 'requires confirmation' list. Proceeding with autonomous execution.")
            
            inferred_args_tuple = tuple(parsed_response['inferred_args']) # Should be a list now
            inferred_kwargs_dict = parsed_response['inferred_kwargs'] # Should be a dict

            log_event(
                event_type="AUTONOMOUS_TOOL_EXECUTION_INITIATED",
                description=f"Autonomously executing tool '{tool_name_detected}' for user input: '{user_statement}'.",
                source="detect_missed_tool_opportunity",
                metadata={
                    "tool_name": tool_name_detected, "inferred_args": inferred_args_tuple,
                    "inferred_kwargs": inferred_kwargs_dict, "original_user_input": user_statement,
                    "reasoning": parsed_response.get("reasoning", "N/A")
                }
            )
            single_step_plan_auto = [{"tool_name": tool_name_detected, "args": inferred_args_tuple, "kwargs": inferred_kwargs_dict}]
            goal_for_auto_execution = f"Autonomously execute tool '{tool_name_detected}' based on user statement: {user_statement}"

            execution_results_auto_str = "No result or error during execution."
            try:
                if is_debug_mode(): # pragma: no cover
                    print(f"[DEBUG CONV_INTEL] Calling executor.execute_plan for autonomous execution. Goal: '{goal_for_auto_execution}'")
                from ai_assistant.planning.planning import PlannerAgent 
                temp_planner = PlannerAgent()
                execution_results_auto = await executor.execute_plan(
                    goal_description=goal_for_auto_execution,
                    initial_plan=single_step_plan_auto,
                    tool_system=tool_system_instance,
                    planner_agent=temp_planner,
                    learning_agent=learning_agent # Pass learning_agent
                )
                execution_results_auto_str = str(execution_results_auto)

                if is_debug_mode(): # pragma: no cover
                    print(f"[DEBUG CONV_INTEL] Autonomous execution result: {execution_results_auto_str}")

                augmented_history_for_response_gen = (
                    f"{conversation_history_for_prompt}\n"
                    f"{facts_for_prompt}\n" # Also include facts here
                    f"User: {user_statement}\n"
                    f"System: I autonomously ran the tool '{tool_name_detected}'. Result: {execution_results_auto_str[:500]}"
                )
                natural_response_after_tool = await generate_conversational_response(
                    user_input=f"(System note: I just ran '{tool_name_detected}' and got this: {execution_results_auto_str[:200]}. Now, how should I respond to the user's original statement: '{user_statement}'?)",
                    conversation_history=augmented_history_for_response_gen
                )
                return {
                    "autonomously_executed": True, "tool_name": tool_name_detected,
                    "results": execution_results_auto, "original_user_input": user_statement,
                    "conversational_response": natural_response_after_tool
                }
            except Exception as e_auto: # pragma: no cover
                print(f"Error during autonomous execution of '{tool_name_detected}': {e_auto}")
                augmented_history_for_error_response = (
                    f"{conversation_history_for_prompt}\n"
                    f"{facts_for_prompt}\n"
                    f"User: {user_statement}\n"
                    f"System: I tried to autonomously run the tool '{tool_name_detected}' but encountered an error: {str(e_auto)[:200]}"
                )
                natural_error_response = await generate_conversational_response(
                     user_input=f"(System note: I tried to run '{tool_name_detected}' but got an error: {str(e_auto)[:200]}. How should I inform the user about this regarding their original statement: '{user_statement}'?)",
                    conversation_history=augmented_history_for_error_response
                )
                return {
                    "autonomously_executed": False, "error": str(e_auto),
                    "tool_name": tool_name_detected,
                    "conversational_response": natural_error_response
                }
        else: # pragma: no cover
            if is_debug_mode():
                print(f"[DEBUG CONV_INTEL] Tool '{tool_name_detected}' IS in 'requires confirmation' list. Generating suggestion prompt.")
            args_str_manual = ", ".join(map(str, parsed_response['inferred_args']))
            kwargs_str_manual = ", ".join(f"{k}={str(v)}" for k, v in parsed_response['inferred_kwargs'].items())
            suggestion_prompt_text_manual = f"I found a tool called '{parsed_response['tool_name']}' that might help with that. "
            if args_str_manual: suggestion_prompt_text_manual += f"Based on your statement, I've inferred these arguments: [{args_str_manual}]. "
            if kwargs_str_manual: suggestion_prompt_text_manual += f"And these keyword arguments: {{{kwargs_str_manual}}}. "
            if not args_str_manual and not kwargs_str_manual: suggestion_prompt_text_manual += "It doesn't seem to require specific arguments based on your statement. "
            suggestion_prompt_text_manual += "Would you like me to run it?"
            parsed_response["suggestion_prompt"] = suggestion_prompt_text_manual
            parsed_response["is_tool_suggestion"] = True
            return parsed_response

    except json.JSONDecodeError: # pragma: no cover
        print(f"Warning: Failed to parse LLM response as JSON for tool/confirmation detection. Response: {llm_response}")
        return None
    except Exception as e: # pragma: no cover
        print(f"Warning: An unexpected error occurred during LLM response processing for tool/confirmation detection: {e}")
        return None


FORMULATE_TOOL_DESCRIPTION_PROMPT_TEMPLATE = """
You are an AI assistant helping to clarify a user's request for a new tool. Your goal is to transform the user's raw request into a concise and actionable description that can be fed into a code generation system.

User's raw request for a new tool:
"{user_raw_request}"

Based on this request:
1.  Identify the core functionality of the desired tool.
2.  Suggest a potential Python function name (e.g., `summarize_text`, `calculate_area`).
3.  Briefly describe what the tool should do.
4.  Mention its expected inputs (and their likely types if obvious, like string, list of numbers, etc.).
5.  Mention its expected output.

Combine these points into a single, clear, and concise paragraph. This paragraph will be used as a prompt for a code-generating LLM.

Example:
User's raw request: "I need a tool that can take a long article and just give me the main points, maybe like 3-4 bullet points."
Concise Tool Description: "A Python function, possibly named `summarize_text_to_bullets`, that takes a long string of text as input. It should analyze the text and return a list of strings, where each string is a key bullet point summarizing the input text (aim for 3-4 bullet points)."

User's raw request: "Make something to convert Celsius to Fahrenheit for me."
Concise Tool Description: "A Python function, possibly named `celsius_to_fahrenheit`, that takes a float representing a temperature in Celsius as input and returns a float representing the equivalent temperature in Fahrenheit."

Now, formulate a concise tool description for the following user's raw request:
User's raw request: "{user_raw_request}"
Concise Tool Description:
"""

async def formulate_tool_description_from_conversation(user_raw_request: str, llm_model_name: Optional[str] = None) -> Optional[str]:
    """
    Uses an LLM to transform a user's raw request for a new tool into a concise
    description suitable for a code generation system.
    """
    if not user_raw_request: # pragma: no cover
        if is_debug_mode():
            print(f"[DEBUG CONV_INTEL] formulate_tool_description_from_conversation returning None due to empty user_raw_request.")
        return None
    prompt = FORMULATE_TOOL_DESCRIPTION_PROMPT_TEMPLATE.format(user_raw_request=user_raw_request)
    if is_debug_mode(): # pragma: no cover
        print(f"[DEBUG CONV_INTEL] Formulate tool description prompt (first 300 chars):\n{prompt[:300]}...")
    model_to_use = llm_model_name if llm_model_name is not None else get_model_for_task("conversation_intelligence")

    if is_debug_mode(): # pragma: no cover
        print(f"[DEBUG CONV_INTEL] About to call invoke_ollama_model_async for tool formulation. Model: {model_to_use}")

    llm_response = await invoke_ollama_model_async(prompt, model_name=model_to_use)

    if is_debug_mode(): # pragma: no cover
         print(f"[DEBUG CONV_INTEL] Raw LLM response for tool formulation:\n'{llm_response}'")

    if not llm_response or not llm_response.strip(): # pragma: no cover
        print(f"Warning: Received no or empty response from LLM ({model_to_use}) for tool description formulation.")
        return None
    cleaned_response = llm_response.strip()
    if cleaned_response.startswith("Concise Tool Description:"): # pragma: no cover
        cleaned_response = cleaned_response[len("Concise Tool Description:"):].strip()
    if not cleaned_response or len(cleaned_response) < 20: # pragma: no cover
        print(f"Warning: LLM response for tool description formulation seems too short or empty. Response: '{llm_response}'")
        return None
    return cleaned_response

CONVERSATIONAL_RESPONSE_PROMPT_TEMPLATE = """
You are a helpful AI assistant. Engage in a natural and friendly conversation with the user.
Consider the recent conversation history provided below to maintain context and coherence.

If you know the user's name is {user_name}, try to use it naturally in your response if appropriate.
Otherwise, address them generally (e.g., "you", "User").

If relevant facts about the user or the world are provided below, incorporate them naturally into your response if they help address the user's input or enrich the conversation. Do not just list the facts.

Relevant facts I know (if any):
{retrieved_facts_str}

Recent Conversation History:
{conversation_history}

User's latest input:
"{user_input}"

Your Conversational Response:
"""

async def generate_conversational_response(user_input: str, conversation_history: str) -> str:
    """
    Generates a conversational response from the AI, considering history and known facts.
    """
    if not user_input: # pragma: no cover
        if is_debug_mode():
            print(f"[DEBUG CONV_INTEL] generate_conversational_response received empty user_input. Returning default.")
        return "Is there something specific you'd like to talk about?"

    user_name = "User" 
    all_recalled_facts: List[str] = []
    try:
        all_recalled_facts = recall_facts()
        name_fact_prefix = "The user's name is "
        name_from_facts = None
        other_facts_for_prompt = []
        for fact in all_recalled_facts:
            if fact.lower().startswith(name_fact_prefix.lower()):
                potential_name = fact[len(name_fact_prefix):].strip()
                if potential_name: 
                    name_from_facts = potential_name
            else:
                other_facts_for_prompt.append(fact)
        if name_from_facts:
            user_name = name_from_facts
            if is_debug_mode(): # pragma: no cover
                print(f"[DEBUG CONV_INTEL] Recalled user name: {user_name}")
        elif is_debug_mode(): # pragma: no cover
            print(f"[DEBUG CONV_INTEL] No specific 'user's name is' fact found in recalled facts.")
        retrieved_facts_str = "\n".join([f"- {fact}" for fact in other_facts_for_prompt[:5]])
        if not retrieved_facts_str:
            retrieved_facts_str = "No specific relevant facts known at this moment."
    except Exception as e: # pragma: no cover
        if is_debug_mode():
            print(f"[DEBUG CONV_INTEL] Error recalling facts: {e}")
        retrieved_facts_str = "Error retrieving some contextual facts."

    prompt = CONVERSATIONAL_RESPONSE_PROMPT_TEMPLATE.format(
        user_input=user_input,
        conversation_history=conversation_history if conversation_history else "No prior conversation history for this turn.",
        user_name=user_name,
        retrieved_facts_str=retrieved_facts_str
    )
    if is_debug_mode(): # pragma: no cover
        print(f"[DEBUG CONV_INTEL] Conversational response prompt (user_name: {user_name}, retrieved_facts: {retrieved_facts_str[:100]}..., first 300 chars of prompt):\n{prompt[:300]}...")

    model_to_use = get_model_for_task("conversation_intelligence")

    if is_debug_mode(): # pragma: no cover
        print(f"[DEBUG CONV_INTEL] About to call invoke_ollama_model_async for conversational response. Model: {model_to_use}")

    llm_response = await invoke_ollama_model_async(prompt, model_name=model_to_use, max_tokens=2048)

    if is_debug_mode(): # pragma: no cover
        print(f"[DEBUG CONV_INTEL] Raw LLM response for conversational response:\n'{llm_response}'")

    if not llm_response or not llm_response.strip(): # pragma: no cover
        print(f"Warning: Received no or empty response from LLM ({model_to_use}) for conversational response generation.")
        return "I'm not sure how to respond to that right now. Could you try rephrasing?"

    cleaned_response = llm_response.strip()
    if cleaned_response.startswith("Your Conversational Response:"): # pragma: no cover
        cleaned_response = cleaned_response[len("Your Conversational Response:"):].strip()
    return cleaned_response


if __name__ == '__main__': # pragma: no cover
    async def run_conv_intel_tests():
        print("--- Testing Conversation Intelligence Module (with Mocks & Broader Name/Fact Usage) ---")
        # ... (your existing __main__ test setup and cases) ...
        # (Ensure mocks for invoke_ollama_model_async, recall_facts, get_recent_events, 
        #  _load_requires_confirmation_list_ci, ExecutionAgent, ToolSystem are in place if running this directly)
        pass # Placeholder for actual test calls if this file were run standalone
    
    # To run the tests if this file is executed:
    # asyncio.run(run_conv_intel_tests())
### END FILE: ai_assistant/core/conversation_intelligence.py ###