# ai_assistant/core/system_executor.py
import subprocess
import os
import sys
import logging
from typing import Dict, Any, List

from ai_assistant.config import get_projects_dir

logger = logging.getLogger(__name__)

def execute_terminal_command(
    command: str,
    timeout_seconds: int = 60,
    working_directory: str = None
) -> Dict[str, Any]:
    """
    Executes a terminal command and captures its output.

    Args:
        command (str): The command string to execute.
        timeout_seconds (int): Timeout for the command execution.
        working_directory (str, optional): The directory to execute the command in. Defaults to current dir.

    Returns:
        Dict[str, Any]: A dictionary containing:
            - 'success' (bool): True if command executed and returned exit code 0.
            - 'stdout' (str): Standard output of the command.
            - 'stderr' (str): Standard error of the command.
            - 'exit_code' (int): Exit code of the command. None if timeout or other execution error.
            - 'error' (str, optional): Description of error if subprocess call failed or timed out.
    """
    response = {
        "success": False,
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "error": None,
    }
    try:
        logger.info(f"Executing terminal command: '{command}' in '{working_directory or os.getcwd()}' with timeout {timeout_seconds}s")
        process = subprocess.run(
            command,
            shell=True,  # Be cautious with shell=True due to security implications
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=working_directory
        )
        response["stdout"] = process.stdout.strip()
        response["stderr"] = process.stderr.strip()
        response["exit_code"] = process.returncode
        if process.returncode == 0:
            response["success"] = True
            logger.info(f"Command '{command}' executed successfully. Exit code: {process.returncode}")
        else:
            logger.warning(f"Command '{command}' failed. Exit code: {process.returncode}. Stderr: {process.stderr.strip()}")
            response["error"] = f"Command exited with non-zero code: {process.returncode}"

    except subprocess.TimeoutExpired:
        response["error"] = f"Command '{command}' timed out after {timeout_seconds} seconds."
        logger.error(response["error"])
    except FileNotFoundError:
        response["error"] = f"Command or executable not found: {command.split()[0]}"
        logger.error(response["error"])
    except Exception as e:
        response["error"] = f"Failed to execute command '{command}': {str(e)}"
        logger.error(response["error"], exc_info=True)

    return response

def execute_project_script(
    script_name: str,
    args: List[str] = None,
    timeout_seconds: int = 300
) -> Dict[str, Any]:
    """
    Executes a Python script located in the projects directory.

    Args:
        script_name (str): The name of the Python script (e.g., 'my_project.py').
        args (List[str], optional): List of arguments to pass to the script.
        timeout_seconds (int): Timeout for the script execution.

    Returns:
        Dict[str, Any]: Similar to execute_terminal_command, detailing script execution outcome.
    """
    projects_dir = get_projects_dir()
    script_path = os.path.join(projects_dir, script_name)

    if not os.path.isfile(script_path):
        error_msg = f"Project script not found: {script_path}"
        logger.error(error_msg)
        return {"success": False, "stdout": "", "stderr": "", "exit_code": None, "error": error_msg}

    if not script_name.endswith(".py"):
        error_msg = f"Script '{script_name}' is not a Python (.py) file."
        logger.error(error_msg)
        return {"success": False, "stdout": "", "stderr": "", "exit_code": None, "error": error_msg}

    command_parts = [sys.executable, script_path]
    if args:
        command_parts.extend(args)
    
    command_str = " ".join(command_parts) # For logging
    logger.info(f"Attempting to execute project script: {command_str} from directory {projects_dir}")

    return execute_terminal_command(command_str, timeout_seconds, working_directory=projects_dir)

# --- Schemas for AI Tool Usage ---

EXECUTE_TERMINAL_COMMAND_SCHEMA = {
    "name": "execute_terminal_command",
    "description": "Executes a shell command and returns its stdout, stderr, and exit code. Use with caution.",
    "parameters": [
        {"name": "command", "type": "str", "description": "The command to execute (e.g., 'ls -l', 'pip install package')."},
        {"name": "timeout_seconds", "type": "int", "description": "Optional timeout in seconds. Default 60."},
        {"name": "working_directory", "type": "str", "description": "Optional directory to run the command in. Defaults to agent's current working directory."}
    ]
}

EXECUTE_PROJECT_SCRIPT_SCHEMA = {
    "name": "execute_project_script",
    "description": "Executes a Python script located in the agent's projects directory. The script runs as a separate process.",
    "parameters": [
        {"name": "script_name", "type": "str", "description": "Name of the Python script file in the projects directory (e.g., 'data_processing.py')."},
        {"name": "args", "type": "list", "description": "Optional list of string arguments to pass to the script."},
        {"name": "timeout_seconds", "type": "int", "description": "Optional timeout in seconds. Default 300."}
    ]
}

# List of tool schemas provided by this module, to be used by the AI agent's tool registration system.
SYSTEM_EXECUTOR_TOOL_SCHEMAS = [
    EXECUTE_TERMINAL_COMMAND_SCHEMA,
    EXECUTE_PROJECT_SCRIPT_SCHEMA,
]