import os
import re
import logging
import time
from typing import TYPE_CHECKING, Optional

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