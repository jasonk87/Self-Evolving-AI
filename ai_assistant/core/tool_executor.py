# ai_assistant/core/tool_executor.py
import importlib.util
import os
import sys
import io
import logging
import traceback
from typing import Any, Dict, Tuple

from ai_assistant.core.tool_creator import get_generated_tools_dir

logger = logging.getLogger(__name__)

class ToolExecutionError(Exception):
    """Custom exception for errors during tool execution."""
    pass

def execute_tool(
    tool_name: str,
    tool_arguments: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Dynamically loads and executes a specified tool with given arguments.

    Args:
        tool_name (str): The name of the tool to execute (should match the .py file name and function name).
        tool_arguments (Dict[str, Any]): A dictionary of arguments to pass to the tool's function.

    Returns:
        Dict[str, Any]: A dictionary containing:
            - 'success' (bool): True if execution was successful, False otherwise.
            - 'result' (Any): The return value of the tool if successful, None otherwise.
            - 'stdout' (str): Captured standard output from the tool.
            - 'stderr' (str): Captured standard error from the tool.
            - 'error' (str): Error message if an exception occurred, None otherwise.
    """
    generated_tools_dir = get_generated_tools_dir()
    tool_file_name = f"{tool_name}.py"
    tool_module_path = os.path.join(generated_tools_dir, tool_file_name)

    response = {
        "success": False,
        "result": None,
        "stdout": "",
        "stderr": "",
        "error": None,
    }

    if not os.path.exists(tool_module_path):
        response["error"] = f"Tool module not found: {tool_module_path}"
        logger.error(response["error"])
        return response

    # Capture stdout and stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = captured_stdout = io.StringIO()
    sys.stderr = captured_stderr = io.StringIO()

    try:
        spec = importlib.util.spec_from_file_location(tool_name, tool_module_path)
        if spec is None or spec.loader is None:
            raise ToolExecutionError(f"Could not create module spec for {tool_module_path}")

        tool_module = importlib.util.module_from_spec(spec)
        sys.modules[tool_name] = tool_module # Add to sys.modules to handle relative imports within the tool if any
        spec.loader.exec_module(tool_module)

        if not hasattr(tool_module, tool_name):
            raise ToolExecutionError(f"Tool function '{tool_name}' not found in module {tool_module_path}")

        tool_function = getattr(tool_module, tool_name)
        
        logger.info(f"Executing tool '{tool_name}' with arguments: {tool_arguments}")
        result = tool_function(**tool_arguments)
        
        response["success"] = True
        response["result"] = result
        logger.info(f"Tool '{tool_name}' executed successfully. Result: {result}")

    except Exception as e:
        response["error"] = f"Error executing tool '{tool_name}': {str(e)}\n{traceback.format_exc()}"
        logger.error(response["error"], exc_info=False) # exc_info=False as traceback is already in the message
    finally:
        response["stdout"] = captured_stdout.getvalue()
        response["stderr"] = captured_stderr.getvalue()
        sys.stdout = old_stdout # Restore stdout
        sys.stderr = old_stderr # Restore stderr
        if tool_name in sys.modules: # Clean up module from sys.modules
            del sys.modules[tool_name]

    return response