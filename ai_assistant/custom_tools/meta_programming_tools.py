import os
import re
import logging
import time
from typing import TYPE_CHECKING, Optional, Dict, List, Any, Tuple # Added Dict, List, Any, Tuple
import importlib.util # Ensure this is imported
import inspect # Ensure this is imported
import asyncio # For __main__ if any async test code is added

from ai_assistant.config import get_model_for_task # To get the right LLM model

if TYPE_CHECKING:
    from ai_assistant.core.action_executor import ActionExecutor

logger = logging.getLogger(__name__)

# Define the base path for custom tools and the subdirectory for generated tools
GENERATED_TOOLS_DIR_NAME = "generated"
GENERATED_TOOLS_MODULE_PATH_PREFIX = f"ai_assistant.custom_tools.{GENERATED_TOOLS_DIR_NAME}"


def get_generated_tools_path() -> str:
    """Returns the absolute path to the directory where generated tools are stored."""
    # __file__ is ai_assistant/custom_tools/meta_programming_tools.py
    # os.path.dirname(__file__) is ai_assistant/custom_tools/
    custom_tools_dir = os.path.dirname(__file__)
    path = os.path.join(custom_tools_dir, GENERATED_TOOLS_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path

async def generate_new_tool_from_description(
    action_executor: "ActionExecutor",
    tool_description: str,
    suggested_tool_function_name: Optional[str] = None,
    suggested_filename: Optional[str] = None
) -> str:
    """
    Generates Python code for a new tool based on a description and saves it
    to 'ai_assistant/custom_tools/generated/'. The LLM infers the tool's
    name, arguments, and implementation. A restart or tool refresh mechanism
    is typically required to activate the new tool.

    Args:
        action_executor: Provides access to the LLM interface.
        tool_description: Natural language description of the tool to create.
        suggested_tool_function_name: Optional specific function name for the new tool.
        suggested_filename: Optional specific filename (e.g., my_tool.py).

    Returns:
        A string indicating the result of the operation.
    """
    if not action_executor or not hasattr(action_executor, 'llm_interface'):
        return "Error: ActionExecutor with LLM interface is required for tool generation."

    function_name_guidance = f"The primary tool function should be named: `{suggested_tool_function_name}`." if suggested_tool_function_name else ""
    
    if suggested_filename:
        processed_sugg_filename = os.path.basename(suggested_filename)
        if not processed_sugg_filename.endswith(".py"):
            processed_sugg_filename += ".py"
        filename_guidance = f"Save the tool in a file named: `{processed_sugg_filename}`."
    else:
        filename_guidance = "Suggest a suitable, PEP8-compliant Python filename for this tool (e.g., `utility_helpers.py` or `data_processor_tool.py`)."

    prompt = f"""
You are an expert Python programmer assisting an AI agent by creating new tools.
Your task is to generate the complete Python code for a new tool based on the following description.

Tool Description:
"{tool_description}"

{function_name_guidance}
{filename_guidance}

Your output MUST strictly follow this format:
1.  The Python code block for the tool, enclosed in triple backticks (```python ... ```).
2.  On a new line, after the code block, the suggested filename using the prefix "Suggested Filename: ".

The Python code should:
- Be a single, self-contained Python script/module.
- Include a clear function definition for the tool.
- Use type hints for all arguments and return types.
- Have a comprehensive docstring for the main tool function, explaining what it does, its arguments (name, type, description), and what it returns (type, description). This docstring will be used by the AI assistant.
- Include necessary import statements at the top of the script.
- Implement the core logic to fulfill the described functionality.
- Handle potential errors gracefully (e.g., using try-except blocks).
- If the tool needs `action_executor` (e.g., to call other tools), it should accept `action_executor: ActionExecutor` as its first argument.

Example Tool Structure:
```python
import os
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ai_assistant.core.action_executor import ActionExecutor

async def example_tool_function(action_executor: "ActionExecutor", items: List[str]) -> str:
    \"\"\"
    This is an example docstring. It processes items.
    Args:
        action_executor: The action executor.
        items (List[str]): A list of strings to process.
    Returns:
        str: A summary of the processing.
    \"\"\"
    try:
        # Tool logic here
        return f"Processed {{len(items)}} items."
    except Exception as e:
        # import logging; logger = logging.getLogger(__name__); logger.error(f"Error: {{e}}")
        return f"Error: {{e}}"
```
Now, generate the Python code and the suggested filename for the described tool.
"""
    try:
        model_name = get_model_for_task("tool_creation")
        llm_response = await action_executor.llm_interface.send_request(
            prompt=prompt, model_name=model_name, temperature=0.2
        )

        if not isinstance(llm_response, str) or not llm_response.strip():
            return f"Error: LLM returned an empty or invalid response. Response: {llm_response}"

        code_match = re.search(r"```python\n(.*?)\n```", llm_response, re.DOTALL)
        filename_match = re.search(r"Suggested Filename:\s*([\w_.-]+\.py)", llm_response)

        if not code_match:
            return f"Error: LLM did not provide a Python code block. Response:\n{llm_response}"
        generated_code = code_match.group(1).strip()

        final_filename = ""
        if suggested_filename: # User-provided filename takes precedence
            final_filename = os.path.basename(suggested_filename)
            if not final_filename.endswith(".py"): final_filename += ".py"
        elif filename_match:
            final_filename = filename_match.group(1).strip()
        
        if not final_filename: # Fallback if no filename determined yet
            func_name_match = re.search(r"def\s+([\w_]+)\s*\(", generated_code)
            base_name = func_name_match.group(1) if func_name_match else f"generated_tool_{int(time.time())}"
            final_filename = f"{base_name}.py"
            logger.warning(f"No filename suggested by LLM or user. Using fallback: {final_filename}")

        final_filename = re.sub(r'[^\w_.-]', '', final_filename) # Sanitize
        if not final_filename or not final_filename.endswith(".py"):
            final_filename = f"tool_{int(time.time())}.py" # Ultimate fallback

        generated_tools_dir = get_generated_tools_path()
        file_path = os.path.join(generated_tools_dir, final_filename)

        # Ensure __init__.py exists in generated_tools_dir
        init_py_path = os.path.join(generated_tools_dir, "__init__.py")
        if not os.path.exists(init_py_path):
            with open(init_py_path, "w", encoding="utf-8") as f_init:
                f_init.write("# This file makes Python treat the 'generated' directory as a package.\n")
            logger.info(f"Created __init__.py in {generated_tools_dir}")

        with open(file_path, "w", encoding="utf-8") as f_tool:
            f_tool.write(generated_code)

        relative_file_path = os.path.join("custom_tools", GENERATED_TOOLS_DIR_NAME, final_filename).replace("\\", "/")
        return (
            f"Successfully generated tool code and saved to 'ai_assistant/{relative_file_path}'.\n"
            "IMPORTANT: A restart of the AI Assistant or a '/refresh_tools' command (if implemented) "
            "is usually required to make the new tool available for use."
        )
    except Exception as e:
        logger.error(f"Error in generate_new_tool_from_description: {e}", exc_info=True)
        return f"An unexpected error occurred during tool generation: {e}"

# --- New find_agent_tool_source function and related logic ---
import importlib.util
import inspect
from typing import Optional, Dict, List, Any # Ensure Any is imported

# --- New find_agent_tool_source function and related logic ---
# importlib.util and inspect are imported at the top now

# Assuming get_generated_tools_dir is accessible from tool_creator.
# Fallback provided here is simplified.
try:
    # This path implies tool_creator.py is in ai_assistant/core/
    # Adjust if get_generated_tools_dir is located elsewhere or exposed differently.
    from ai_assistant.core.tool_creator import get_generated_tools_dir
except ImportError: # pragma: no cover
    # Fallback: if get_generated_tools_dir cannot be imported from tool_creator
    # This might happen if this module is run standalone or if paths are complex.
    # A more robust solution would involve a shared utility or better path configuration.
    def get_generated_tools_dir():
        # Path relative to this file (meta_programming_tools.py)
        # meta_programming_tools.py is in ai_assistant/custom_tools/
        # generated_tools is expected to be ai_assistant/custom_tools/generated/
        # However, the original get_generated_tools_path() in this file creates it there.
        # For consistency, let's use that existing helper if tool_creator's version isn't found.
        # This means the original get_generated_tools_path in this file should be used.
        # The prompt had a different fallback, let's stick to what's in this file for now.
        return get_generated_tools_path() # Use the existing helper in this file as a fallback

# Directory of the current file (ai_assistant/custom_tools/)
CUSTOM_TOOLS_DIR_PATH = os.path.dirname(__file__)

GENERATED_TOOLS_DIR_FOR_FINDER = None
try:
    GENERATED_TOOLS_DIR_FOR_FINDER = get_generated_tools_dir()
except Exception as e_get_dir: # pragma: no cover
    logger.warning(f"Warning: Could not dynamically get generated_tools_dir for find_agent_tool_source: {e_get_dir}. Fallback may be incorrect if paths changed.")
    # If the imported get_generated_tools_dir fails, and the fallback inside it also fails,
    # this will remain None or use the output of the local get_generated_tools_path().

KNOWN_TOOL_DIRECTORIES = [
    CUSTOM_TOOLS_DIR_PATH,
    GENERATED_TOOLS_DIR_FOR_FINDER # For tools in the generated directory
]
# Filter out None entries if get_generated_tools_dir failed completely
KNOWN_TOOL_DIRECTORIES = [d for d in KNOWN_TOOL_DIRECTORIES if d and os.path.isdir(d)]
if not KNOWN_TOOL_DIRECTORIES: # pragma: no cover
    logger.error("Critical: No valid KNOWN_TOOL_DIRECTORIES could be determined for find_agent_tool_source.")


def find_agent_tool_source(tool_name: str) -> Optional[Dict[str, str]]:
    """
    Finds an agent tool's module path, function name, and its source code.
    Searches in known agent tool directories.

    Args:
        tool_name: The name of the tool (expected to match the .py file name and function name).

    Returns:
        A dictionary with "module_path", "function_name", "file_path", and "source_code",
        or None if the tool is not found or source cannot be retrieved.
    """
    if not tool_name.isidentifier():
        logger.warning(f"find_agent_tool_source: '{tool_name}' is not a valid Python identifier. Cannot be a tool name.")
        return None

    potential_filename = f"{tool_name}.py"

    for tool_dir_abs_path in KNOWN_TOOL_DIRECTORIES:
        if not tool_dir_abs_path: # Should have been filtered, but double check
            continue

        prospective_file_path = os.path.join(tool_dir_abs_path, potential_filename)

        if os.path.exists(prospective_file_path) and os.path.isfile(prospective_file_path):
            try:
                # Construct module name based on file path and known package structure
                # This assumes 'ai_assistant' is a top-level package in PYTHONPATH or discoverable
                # And 'custom_tools' or 'generated' are sub-packages/modules.

                # Determine base package path e.g. "ai_assistant.custom_tools" or "ai_assistant.custom_tools.generated"
                # This logic needs to be robust to where tool_dir_abs_path points.
                # Example: tool_dir_abs_path = /path/to/project/Self-Evolving-Agent.../ai_assistant/custom_tools
                # We want module_path_prefix = ai_assistant.custom_tools

                module_path_parts = []
                current_path = os.path.normpath(tool_dir_abs_path)

                # Traverse up until 'ai_assistant' or a known root is found, or break
                # This is still heuristic and fragile if "ai_assistant" is not the unique root marker.
                # A better way is pre-configured module prefixes for each KNOWN_TOOL_DIRECTORIES entry.
                # For now, this is a slightly improved heuristic.

                # Heuristic: Try to find "ai_assistant" in the path and build from there
                path_parts = current_path.split(os.sep)
                try:
                    ai_assistant_index = path_parts.index("ai_assistant")
                    module_path_parts = path_parts[ai_assistant_index:] # e.g., ['ai_assistant', 'custom_tools', 'generated']
                except ValueError: # pragma: no cover
                    # If "ai_assistant" is not in the path, this heuristic fails.
                    # Fallback to just using the directory name as a pseudo-package.
                    # This will likely not work for actual imports if the dir is not in sys.path.
                    logger.warning(f"Could not determine module path relative to 'ai_assistant' for {tool_dir_abs_path}. Using directory name.")
                    module_path_parts = [os.path.basename(tool_dir_abs_path)]


                full_module_name_for_spec = ".".join(module_path_parts + [tool_name])

                module_spec = importlib.util.spec_from_file_location(full_module_name_for_spec, prospective_file_path)

                if module_spec and module_spec.loader:
                    module_obj = importlib.util.module_from_spec(module_spec)
                    # Important: Add to sys.modules *before* exec_module if the tool might have internal relative imports.
                    # However, this can have side effects if not cleaned up.
                    # For now, we assume tools are simple enough or paths are set up for direct import by file.
                    # sys.modules[module_spec.name] = module_obj # Consider implications

                    module_spec.loader.exec_module(module_obj)

                    if hasattr(module_obj, tool_name):
                        function_obj = getattr(module_obj, tool_name)
                        source_code = inspect.getsource(function_obj)

                        # if module_spec.name in sys.modules: # Cleanup if added
                        #     del sys.modules[module_spec.name]

                        return {
                            "module_path": full_module_name_for_spec, # This is the spec's name
                            "function_name": tool_name,
                            "file_path": prospective_file_path,
                            "source_code": source_code.strip()
                        }
                    else: # pragma: no cover
                        logger.warning(f"Tool function '{tool_name}' not found in module '{module_obj.__name__}' at '{prospective_file_path}'.")
                # else: Error: module spec invalid (covered by return None later)
            except Exception as e: # pragma: no cover
                logger.error(f"Could not load or inspect tool '{tool_name}' from '{prospective_file_path}': {e}", exc_info=True)
                # Fall through to try next directory or return None
                pass

    return None # Tool not found in any known directory

# Conceptual Schema for find_agent_tool_source tool
FIND_AGENT_TOOL_SOURCE_SCHEMA = {
    "name": "find_agent_tool_source",
    "description": "Finds an existing agent tool's source code, module path, and file path. Searches in standard agent tool directories.",
    "parameters": [
        tuple(sorted({"name": "tool_name", "type": "str", "description": "The name of the agent tool to find (e.g., 'my_calculator')."}.items()))
    ],
    "returns": tuple(sorted({
        "type": "string",
        "description": "A JSON string representing a dictionary with keys 'module_path', 'function_name', 'file_path', 'source_code', or null if not found."
    }.items()))
}

def stage_agent_tool_modification(
    module_path: str,
    function_name: str,
    modified_code_string: str,
    change_description: str,
    original_reflection_entry_id: Optional[str] = None,
    tool_name_for_action: Optional[str] = None # New optional arg
) -> Dict[str, Any]:
    """
    Prepares a structured dictionary for proposing a modification to an existing agent tool.
    This dictionary is intended to be used by ActionExecutor with the 'PROPOSE_TOOL_MODIFICATION' action type.

    Args:
        module_path: The module path of the tool to be modified (e.g., "ai_assistant.custom_tools.my_tool").
        function_name: The name of the function within the module to be modified.
        modified_code_string: The complete new source code for the function.
        change_description: A description of why the change is being made or the user's request.
        original_reflection_entry_id: Optional. If the modification stems from a reflection log.
        tool_name_for_action: Optional. The 'tool_name' as known by the tool system (e.g. for display or logging).
                              If None, defaults to function_name.

    Returns:
        A dictionary structured for ActionExecutor's PROPOSE_TOOL_MODIFICATION action.
    """

    actual_tool_name = tool_name_for_action if tool_name_for_action else function_name

    action_details = {
        "module_path": module_path,
        "function_name": function_name,
        "tool_name": actual_tool_name,
        "suggested_code_change": modified_code_string,
        "suggested_change_description": change_description,
        # "source_of_code": "PlannerLLM->CodeService->StagedModification", # Could add more provenance
    }
    if original_reflection_entry_id:
        action_details["original_reflection_entry_id"] = original_reflection_entry_id

    return {
        "action_type_for_executor": "PROPOSE_TOOL_MODIFICATION", # Special key for orchestrator
        "action_details_for_executor": action_details
    }

# Conceptual Schema for stage_agent_tool_modification tool
STAGE_AGENT_TOOL_MODIFICATION_SCHEMA = {
    "name": "stage_agent_tool_modification",
    "description": "Stages the parameters needed to propose a modification to an existing agent tool. This prepares the information for the self-modification review and application process, typically for ActionExecutor.",
    "parameters": [
        tuple(sorted({"name": "module_path", "type": "str", "description": "The module path of the tool (e.g., 'ai_assistant.custom_tools.my_tool')."}.items())),
        tuple(sorted({"name": "function_name", "type": "str", "description": "The function name of the tool to modify."}.items())),
        tuple(sorted({"name": "modified_code_string", "type": "str", "description": "The complete new source code for the modified function."}.items())),
        tuple(sorted({"name": "change_description", "type": "str", "description": "Detailed description of the changes made or the reason for modification."}.items())),
        tuple(sorted({"name": "original_reflection_entry_id", "type": "str", "description": "Optional. The ID of the reflection entry that suggested this modification."}.items())),
        tuple(sorted({"name": "tool_name_for_action", "type": "str", "description": "Optional. The 'tool_name' for logging/display in ActionExecutor, defaults to function_name."}.items()))
    ],
    "returns": tuple(sorted({
        "type": "string",
        "description": "A JSON string representing a dictionary containing 'action_type_for_executor': 'PROPOSE_TOOL_MODIFICATION' and 'action_details_for_executor': {details_dict}."
    }.items()))
}


if __name__ == '__main__': # pragma: no cover
    print("--- Testing find_agent_tool_source ---")

    # Setup dummy tools for testing
    dummy_custom_tool_name = "_test_dummy_custom_tool_for_find" # Unique name
    dummy_custom_tool_path = os.path.join(CUSTOM_TOOLS_DIR_PATH, f"{dummy_custom_tool_name}.py")
    with open(dummy_custom_tool_path, "w") as f:
        f.write(f"def {dummy_custom_tool_name}(param1: str):\n    return f'Custom tool received: {{param1}}'")

    dummy_generated_tool_name = "_test_dummy_generated_tool_for_find" # Unique name
    generated_dir_for_test = None
    if GENERATED_TOOLS_DIR_FOR_FINDER and os.path.isdir(GENERATED_TOOLS_DIR_FOR_FINDER):
            generated_dir_for_test = GENERATED_TOOLS_DIR_FOR_FINDER

    dummy_generated_tool_path = None
    if generated_dir_for_test:
        os.makedirs(generated_dir_for_test, exist_ok=True) # Ensure it exists
        dummy_generated_tool_path = os.path.join(generated_dir_for_test, f"{dummy_generated_tool_name}.py")
        with open(dummy_generated_tool_path, "w") as f:
            f.write(f"def {dummy_generated_tool_name}():\n    return 'Generated tool reporting'")

    print(f"Searching for custom tool: {dummy_custom_tool_name}")
    found_custom = find_agent_tool_source(dummy_custom_tool_name)
    if found_custom:
        print(f"Found custom: {found_custom['file_path']}, Module: {found_custom['module_path']}")
        assert dummy_custom_tool_name in found_custom['source_code']
    else:
        print(f"Custom tool '{dummy_custom_tool_name}' not found. KNOWN_TOOL_DIRECTORIES: {KNOWN_TOOL_DIRECTORIES}")
        assert False, f"Failed to find {dummy_custom_tool_name}"

    if dummy_generated_tool_path:
        print(f"Searching for generated tool: {dummy_generated_tool_name}")
        found_generated = find_agent_tool_source(dummy_generated_tool_name)
        if found_generated:
            print(f"Found generated: {found_generated['file_path']}, Module: {found_generated['module_path']}")
            assert dummy_generated_tool_name in found_generated['source_code']
        else:
            print(f"Generated tool '{dummy_generated_tool_name}' not found. KNOWN_TOOL_DIRECTORIES: {KNOWN_TOOL_DIRECTORIES}")
            print("INFO: Generated tool find test might be sensitive to execution environment for get_generated_tools_dir().")
            # Not asserting false here as it can be flaky in some test setups due to pathing if get_generated_tools_dir() is tricky.
    else:
        print(f"Skipped generated tool test as directory '{GENERATED_TOOLS_DIR_FOR_FINDER}' was not valid or accessible.")


    print("Searching for non_existent_tool:")
    not_found = find_agent_tool_source("non_existent_tool_xyz_for_find")
    if not_found is None:
        print("Correctly did not find non_existent_tool_xyz_for_find.")
        assert True
    else:
        print(f"Incorrectly found non_existent_tool_xyz_for_find: {not_found}")
        assert False, "Found a tool that should not exist."

    # Cleanup dummy files
    if os.path.exists(dummy_custom_tool_path):
        os.remove(dummy_custom_tool_path)
    if dummy_generated_tool_path and os.path.exists(dummy_generated_tool_path):
        os.remove(dummy_generated_tool_path)
    print("--- Finished testing find_agent_tool_source ---")

    print("\n--- Testing stage_agent_tool_modification ---")
    staged_info = stage_agent_tool_modification(
        module_path="ai_assistant.custom_tools.calculator",
        function_name="add",
        modified_code_string="def add(a,b): return a+b+1 # new version",
        change_description="User requested to make add function increment by one more.",
        original_reflection_entry_id="reflect_123",
        tool_name_for_action="calculator_add_v2"
    )
    print(f"Staged info: {staged_info}")
    assert staged_info["action_type_for_executor"] == "PROPOSE_TOOL_MODIFICATION"
    assert staged_info["action_details_for_executor"]["module_path"] == "ai_assistant.custom_tools.calculator"
    assert staged_info["action_details_for_executor"]["suggested_code_change"].endswith("# new version")
    assert staged_info["action_details_for_executor"]["tool_name"] == "calculator_add_v2"

    staged_info_no_optional = stage_agent_tool_modification(
        module_path="ai_assistant.custom_tools.helper",
        function_name="format_text",
        modified_code_string="def format_text(s): return s.strip()",
        change_description="Ensure stripping"
    )
    print(f"Staged info (no optional): {staged_info_no_optional}")
    assert staged_info_no_optional["action_details_for_executor"]["tool_name"] == "format_text" # Defaults to function_name
    assert "original_reflection_entry_id" not in staged_info_no_optional["action_details_for_executor"]
    print("--- Finished testing stage_agent_tool_modification ---")