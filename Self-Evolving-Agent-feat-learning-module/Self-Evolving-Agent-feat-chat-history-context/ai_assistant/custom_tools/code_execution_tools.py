# ai_assistant/custom_tools/code_execution_tools.py
import os
import subprocess
import sys
import shutil # For test cleanup
import asyncio
import json # For parsing stringified list of script arguments
from typing import List, Dict, Any, Optional, Union # Keep Union

# Attempt to import from the correct location for runtime
try:
    from ai_assistant.custom_tools.file_system_tools import sanitize_project_name, BASE_PROJECTS_DIR as FS_BASE_PROJECTS_DIR
except ImportError:
    # Fallback for local testing if the above fails (e.g. when running __main__)
    # This assumes file_system_tools.py is in the same directory for local testing
    try:
        from .file_system_tools import sanitize_project_name, BASE_PROJECTS_DIR as FS_BASE_PROJECTS_DIR
    except ImportError:
        # If run as a script directly, file_system_tools might not be found without further path manipulation
        # This is a common issue with Python's import system for standalone scripts vs. package modules.
        # For the purpose of this tool, we'll assume it's run as part of the package.
        # If direct script execution for testing is needed, PYTHONPATH might need adjustment.
        print("Warning: Could not import file_system_tools. Assuming standard BASE_PROJECTS_DIR for runtime.")
        FS_BASE_PROJECTS_DIR = "ai_generated_projects"  # Default fallback
        def sanitize_project_name(name: str) -> str:  # Changed parameter name
            return "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in name)


# This global will be overridden during tests
BASE_PROJECTS_DIR = FS_BASE_PROJECTS_DIR

async def execute_python_script_in_project(
    project_name: str,
    script_filename: str,
    *pos_args, # Catch all positional arguments after the first two
    **kwargs
) -> Dict[str, Any]:
    """
    Executes a specified Python script within a given project directory.

    Args:
        project_name (str): The name of the project. The script will be run from this project's root directory.
        script_filename (str): The filename of the Python script to execute (e.g., "main.py", "scripts/my_script.py").
                               This path should be relative to the project's root directory.
        *pos_args: Positional arguments that might contain script arguments or timeout.
        **kwargs: Keyword arguments, expected to contain 'args' (for script_args)
                  and/or 'timeout_seconds'.

    Returns:
        Dict[str, Any]: A dictionary containing the execution results:
            - "stdout" (str): The standard output from the script.
            - "stderr" (str): The standard error output from the script.
            - "return_code" (Optional[int]): The return code of the script. None if the script couldn't be run.
            - "error" (Optional[str]): A description of any error that occurred during tool execution
                                       (e.g., file not found, timeout). None if execution started successfully.
            - "ran_successfully" (bool): True if the script ran and returned an exit code of 0, False otherwise.
    """
    result: Dict[str, Any] = {
        "stdout": "",
        "stderr": "",
        "return_code": None,
        "error": "Tool execution failed",  # Default error
        "ran_successfully": False
    }

    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        result["error"] = "Project name must be a non-empty string."
        return result
    if not script_filename or not isinstance(script_filename, str) or not script_filename.strip():
        result["error"] = "Script filename must be a non-empty string."
        return result

    # --- Argument processing for script_args and timeout_seconds ---
    processed_script_args: List[str] = [] # Default to empty list
    actual_timeout_seconds: int = 600  # Default

    # Check kwargs first
    if 'timeout_seconds' in kwargs:
        val = kwargs.pop('timeout_seconds') # Remove from kwargs
        if isinstance(val, (int, float)):
            actual_timeout_seconds = int(val)
        elif isinstance(val, str) and val.isdigit():
            actual_timeout_seconds = int(val)
        else:
            result["error"] = f"Invalid type or value for 'timeout_seconds' in kwargs: {val}. Must be int or digit string."
            return result

    if 'args' in kwargs: # 'args' in kwargs is assumed to be script_args
        val = kwargs.pop('args') # Remove from kwargs
        if isinstance(val, list) and all(isinstance(item, str) for item in val):
            processed_script_args = val
        elif isinstance(val, str):
            try:
                loaded_args = json.loads(val)
                if isinstance(loaded_args, list) and all(isinstance(item, str) for item in loaded_args):
                    processed_script_args = loaded_args
                else:
                    result["error"] = "If 'args' in kwargs is a JSON string, it must decode to a list of strings."
                    return result
            except json.JSONDecodeError:
                result["error"] = "'args' in kwargs was a string but not a valid JSON list. Expected format like '[\"arg1\"]'."
                return result
        elif val is not None: # Not a list, not a string, but not None
            result["error"] = "'args' in kwargs must be a list of strings, a JSON string list, or None."
            return result
    
    # Process positional arguments (*pos_args) if not fully defined by kwargs
    temp_pos_args = list(pos_args) # Make a mutable copy

    # Try to extract timeout from the end of positional args if 'timeout_seconds' wasn't in kwargs
    if 'timeout_seconds' not in kwargs and temp_pos_args: # Check if timeout_seconds was already processed via kwargs
        # Check if the last element could be a timeout
        if isinstance(temp_pos_args[-1], (int, float)):
            actual_timeout_seconds = int(temp_pos_args.pop(-1))
        elif isinstance(temp_pos_args[-1], str) and temp_pos_args[-1].isdigit():
            actual_timeout_seconds = int(temp_pos_args.pop(-1))

    # Process remaining positional args as script_args if 'args' (for script_args) wasn't in kwargs
    if 'args' not in kwargs and temp_pos_args: # Check if script_args were already processed via kwargs
        if len(temp_pos_args) == 1 and isinstance(temp_pos_args[0], str): # Potentially a JSON string list
            try:
                loaded_args = json.loads(temp_pos_args[0])
                if isinstance(loaded_args, list) and all(isinstance(item, str) for item in loaded_args):
                    processed_script_args = loaded_args
                else: # String was not a list of strings, treat as single arg
                    processed_script_args = [temp_pos_args[0]]
            except json.JSONDecodeError: # Not a JSON string, treat as single arg
                processed_script_args = [temp_pos_args[0]]
        else: # Treat all remaining as literal string args
            processed_script_args = [str(arg) for arg in temp_pos_args]
    
    # Final validation of parsed arguments
    if not isinstance(actual_timeout_seconds, int): # Should be int by now
        result["error"] = "Timeout seconds must be an integer."
        return result
    if actual_timeout_seconds <= 0:
        result["error"] = "Timeout seconds must be a positive integer."
        return result
    
    # Ensure processed_script_args is a list of strings
    if not isinstance(processed_script_args, list) or not all(isinstance(arg, str) for arg in processed_script_args):
        result["error"] = "Script arguments, after parsing, must be a list of strings."
        return result
    # --- End argument processing ---

    try:
        sanitized_proj_name = sanitize_project_name(project_name)
        project_root_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_proj_name)

        if not os.path.isdir(project_root_dir):
            result["error"] = f"Project directory not found: {project_root_dir}"
            return result

        script_full_path = os.path.join(project_root_dir, script_filename)

        if not os.path.isfile(script_full_path):
            result["error"] = f"Script not found: {script_full_path}"
            return result
        
        if not script_full_path.endswith(".py"):
            result["error"] = f"Script must be a Python (.py) file: {script_filename}"
            return result

        command = [sys.executable, script_full_path]
        if processed_script_args: # Use the processed version of script arguments
            command.extend(processed_script_args)

        completed_process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=project_root_dir, # Run script from the project's root directory
            timeout=actual_timeout_seconds, # Use parsed timeout
            check=False  # Do not raise an exception for non-zero exit codes
        )

        result["stdout"] = completed_process.stdout.strip() if completed_process.stdout else ""
        result["stderr"] = completed_process.stderr.strip() if completed_process.stderr else ""
        result["return_code"] = completed_process.returncode
        result["error"] = None  # Clear default tool error as execution started

        if result["return_code"] == 0:
            result["ran_successfully"] = True
        else:
            result["ran_successfully"] = False
            result["error"] = f"Script executed with non-zero return code: {result['return_code']}"
            if not result["stderr"] and result["return_code"] !=0 : # Add stdout to stderr if stderr is empty but there was an error
                result["stderr"] = result["stdout"]


    except subprocess.TimeoutExpired:
        # Construct command string for error message, handling potential None in command list
        command_str = ' '.join(filter(None, command)) if 'command' in locals() else "Unknown command"
        result["error"] = f"Script execution timed out after {actual_timeout_seconds} seconds."
        result["stderr"] = f"TimeoutExpired: Command '{command_str}' timed out after {actual_timeout_seconds} seconds."
        result["return_code"] = -1 # Using a custom code for timeout
        result["ran_successfully"] = False
    except FileNotFoundError:
        command_str = ' '.join(filter(None, command)) if 'command' in locals() else "Unknown command"
        result["error"] = f"Execution failed: File not found. Command: {command_str}"
        result["ran_successfully"] = False
    except Exception as e:
        result["error"] = f"An unexpected error occurred during script execution: {str(e)}"
        result["stderr"] = str(e)
        result["ran_successfully"] = False

    return result

# --- Test Suite ---
_TEST_BASE_PROJECTS_DIR = "temp_test_code_execution_projects"
_ORIGINAL_BASE_PROJECTS_DIR = None

def setup_test_environment():
    global BASE_PROJECTS_DIR, _ORIGINAL_BASE_PROJECTS_DIR
    _ORIGINAL_BASE_PROJECTS_DIR = BASE_PROJECTS_DIR
    BASE_PROJECTS_DIR = _TEST_BASE_PROJECTS_DIR
    if os.path.exists(BASE_PROJECTS_DIR):
        shutil.rmtree(BASE_PROJECTS_DIR)
    os.makedirs(BASE_PROJECTS_DIR, exist_ok=True)

def teardown_test_environment():
    global BASE_PROJECTS_DIR, _ORIGINAL_BASE_PROJECTS_DIR
    if os.path.exists(BASE_PROJECTS_DIR):
        shutil.rmtree(BASE_PROJECTS_DIR)
    if _ORIGINAL_BASE_PROJECTS_DIR:
        BASE_PROJECTS_DIR = _ORIGINAL_BASE_PROJECTS_DIR

async def run_all_tests():
    setup_test_environment()
    try:
        print("Running execute_python_script_in_project tests...")

        # Test 1: Success Case
        print("\n--- Test 1: Success Case ---")
        proj_success = "test_proj_success"
        script_success_name = "success_script.py"
        os.makedirs(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_success)), exist_ok=True)
        with open(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_success), script_success_name), "w") as f:
            f.write("import sys\nprint('Success!')\nprint('Error output to stderr', file=sys.stderr)\nsys.exit(0)")
        result1 = await execute_python_script_in_project(proj_success, script_success_name)
        print(f"Result 1: {result1}")
        assert result1["ran_successfully"] is True
        assert result1["return_code"] == 0
        assert "Success!" in result1["stdout"]
        assert "Error output to stderr" in result1["stderr"] # Stderr can still have content on success
        assert result1["error"] is None

        # Test 2: Failure Case (Script Error)
        print("\n--- Test 2: Script Error Case ---")
        proj_fail = "test_proj_fail"
        script_fail_name = "fail_script.py"
        os.makedirs(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_fail)), exist_ok=True)
        with open(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_fail), script_fail_name), "w") as f:
            f.write("import sys\nprint('About to fail...', file=sys.stdout)\nraise ValueError('This is a test error')")
        result2 = await execute_python_script_in_project(proj_fail, script_fail_name)
        print(f"Result 2: {result2}")
        assert result2["ran_successfully"] is False
        assert result2["return_code"] != 0
        assert "About to fail..." in result2["stdout"] # Check stdout even on failure
        assert "ValueError: This is a test error" in result2["stderr"]
        assert "Script executed with non-zero return code" in result2["error"]

        # Test 3: Script Not Found
        print("\n--- Test 3: Script Not Found Case ---")
        proj_notfound = "test_proj_notfound"
        os.makedirs(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_notfound)), exist_ok=True)
        result3 = await execute_python_script_in_project(proj_notfound, "non_existent_script.py")
        print(f"Result 3: {result3}")
        assert result3["ran_successfully"] is False
        assert "Script not found" in result3["error"]

        # Test 4: Timeout Case
        print("\n--- Test 4: Timeout Case ---")
        proj_timeout = "test_proj_timeout"
        script_timeout_name = "timeout_script.py"
        os.makedirs(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_timeout)), exist_ok=True)
        with open(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_timeout), script_timeout_name), "w") as f:
            f.write("import time\nprint('Starting sleep')\ntime.sleep(5)\nprint('Sleep ended')")
        result4 = await execute_python_script_in_project(proj_timeout, script_timeout_name, timeout_seconds=2)
        print(f"Result 4: {result4}")
        assert result4["ran_successfully"] is False
        assert "Script execution timed out" in result4["error"]
        assert "TimeoutExpired" in result4["stderr"]
        assert result4["return_code"] == -1 # Custom code for timeout

        # Test 5: Arguments Case
        print("\n--- Test 5: Arguments Case ---")
        proj_args = "test_proj_args"
        script_args_name = "args_script.py"
        os.makedirs(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_args)), exist_ok=True)
        with open(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_args), script_args_name), "w") as f:
            f.write("import sys\nprint(f'Number of args: {len(sys.argv)}')\nprint(f'Script name: {sys.argv[0]}')\nprint('Args:', sys.argv[1:])")
        test_args = ["hello", "world with space", "arg3"]
        result5 = await execute_python_script_in_project(proj_args, script_args_name, args=test_args)
        print(f"Result 5: {result5}")
        assert result5["ran_successfully"] is True
        assert result5["return_code"] == 0
        assert "Args: ['hello', 'world with space', 'arg3']" in result5["stdout"]
        assert result5["error"] is None
        
        # Test 6: Project Not Found
        print("\n--- Test 6: Project Not Found Case ---")
        result6 = await execute_python_script_in_project("non_existent_project", "any_script.py")
        print(f"Result 6: {result6}")
        assert result6["ran_successfully"] is False
        assert "Project directory not found" in result6["error"]

        # Test 7: Invalid Script Filename (not .py)
        print("\n--- Test 7: Invalid Script Filename (not .py) ---")
        proj_invalid_ext = "test_proj_invalid_ext"
        script_invalid_ext_name = "script.txt"
        os.makedirs(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_invalid_ext)), exist_ok=True)
        with open(os.path.join(BASE_PROJECTS_DIR, sanitize_project_name(proj_invalid_ext), script_invalid_ext_name), "w") as f:
            f.write("This is not a python script.")
        result7 = await execute_python_script_in_project(proj_invalid_ext, script_invalid_ext_name)
        print(f"Result 7: {result7}")
        assert result7["ran_successfully"] is False
        assert "Script must be a Python (.py) file" in result7["error"]
        
        print("\nAll execute_python_script_in_project tests passed!")

    except Exception as e:
        print(f"An error occurred during testing: {e}")
        import traceback
        traceback.print_exc()
    finally:
        teardown_test_environment()

if __name__ == '__main__':
    asyncio.run(run_all_tests())
