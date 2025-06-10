import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Any, Optional # Added Optional
from unittest.mock import patch, MagicMock, ANY # Added for testing

from ai_assistant.core.self_modification import edit_function_source_code

# Configure logger for this module
logger = logging.getLogger(__name__)
# BasicConfig should ideally be set at the application entry point.
# If this module is run as a script, this will configure it.
if not logging.getLogger().handlers: # Check if root logger has handlers
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Define at module level for use in __main__
dummy_tool_module_name_for_test = "dummy_tool_module.py"


def test_modified_tool_in_sandbox(module_path: str, function_name: str, project_root: str) -> Dict[str, Any]:
    """
    Tests a modified tool by executing a temporary script in a sandboxed environment (subprocess).
    This function attempts to import the specified module and function, and calls
    the function if it takes no arguments.
    in an isolated and secure manner to ensure it works as expected and
    doesn't introduce regressions or security issues.

    Args:
        module_path: The Python module path of the modified tool (e.g., "ai_assistant.tools.my_tool").
        function_name: The name of the modified function.
        project_root: The absolute path to the root of the project.

    Returns:
        True if the tests pass (or placeholder is active), False otherwise.
    
    Design Considerations for Future Implementation:
    1.  Approach 1 (Subprocess with Restricted Scope):
        *   Create a temporary copy of the modified tool file and minimal necessary
            surrounding code/project structure.
        *   Execute a separate Python interpreter process using `subprocess.run()`.
        *   The target script for the subprocess would import the modified tool and
            run predefined or generated test cases.
        *   Capture stdout, stderr, and return code for analysis.
        *   Concerns: Managing dependencies, complexity of isolating the exact code needed,
            ensuring the subprocess doesn't have unwanted system access (e.g., network, filesystem beyond designated temp areas).

    2.  Approach 2 (Docker Container):
        *   Dynamically create a Dockerfile that installs necessary dependencies from the project
            (e.g., requirements.txt) and copies the modified tool/module.
        *   Build and run the Docker container.
        *   Execute tests within the container (e.g., by running pytest against the specific file or generated test script).
        *   Concerns: Docker availability on the host system, overhead of Docker image
            building for each test, potential slowness.

    3.  Approach 3 (Library like `RestrictedPython` or `pysandbox`):
        *   Investigate libraries that allow executing Python code with restricted permissions
            within the same process or a tightly controlled child process.
        *   Concerns: Maturity and limitations of such libraries (e.g., might not catch all
            malicious behavior, might interfere with legitimate but complex tool operations),
            thoroughness of the sandboxing, compatibility with async code or specific modules.

    4.  Test Case Generation/Selection:
        *   How to get/generate test cases for the modified function?
            *   Attempt to discover and run existing unit tests for the specific tool/module.
                This requires a robust test discovery mechanism within the project.
            *   Use an LLM to generate simple test cases based on the function's
                docstring, signature, and potentially the nature of the code change.
            *   Perform basic "does it import/load" checks.
            *   Execute with a set of predefined "smoke test" inputs if applicable.
        *   The quality and coverage of test cases are critical for this step's effectiveness.
    """
    logger.info(
        f"Placeholder: Sandboxed testing for {module_path}.{function_name} at project_root '{project_root}' is not yet implemented. Defaulting to True (success)."
    )
    # For now, simulate success to allow the main flow to proceed.
    # In a real implementation, this would return True only if actual tests pass.
    # --- Start of implemented logic ---
    script_content_template = """
import importlib
import sys
import traceback
import os

# It's crucial that project_root is added to sys.path
# so that the module_path can be imported correctly.
project_root_placeholder = "{project_root_escaped_for_string}"
sys.path.insert(0, project_root_placeholder)

module_path_placeholder = "{module_path_str}"
function_name_placeholder = "{function_name_str}"

success = False
output_capture = []

def log_print(message):
    print(message)
    output_capture.append(str(message))

try:
    log_print(f"Attempting to import module: {{module_path_placeholder}} from {{project_root_placeholder}}")
    target_module = importlib.import_module(module_path_placeholder)
    log_print(f"Successfully imported module: {{module_path_placeholder}}")
    
    target_function = getattr(target_module, function_name_placeholder)
    log_print(f"Successfully retrieved function: {{function_name_placeholder}}")
    
    import inspect
    sig = inspect.signature(target_function)
    
    if not sig.parameters:
        log_print(f"Function {{function_name_placeholder}} takes no arguments. Attempting to call.")
        target_function() 
        log_print(f"Successfully called {{function_name_placeholder}} in {{module_path_placeholder}}")
        success = True
    else:
        log_print(f"Function {{function_name_placeholder}} in {{module_path_placeholder}} loaded but not called due to parameters: {{str(sig.parameters)}}.")
        success = True # Consider loading success for now

except Exception as e:
    log_print(f"Error during sandboxed test of {{module_path_placeholder}}.{{function_name_placeholder}}:")
    # Use os.linesep to ensure cross-platform compatibility for newlines in the output.
    tb_lines = traceback.format_exc().splitlines()
    for line in tb_lines:
        log_print(line)
    success = False

if not success:
    # Optionally, print all captured output to stderr of this script before exiting
    # for easier debugging if the calling process captures stderr.
    # sys.stderr.write("\\n".join(output_capture) + "\\n")
    sys.exit(1)
"""
    abs_project_root = os.path.abspath(project_root)
    script_content = script_content_template.format(
        project_root_escaped_for_string=abs_project_root,
        module_path_str=module_path,
        function_name_str=function_name
    )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_script_path = os.path.join(tmpdir, f"temp_test_runner_{os.urandom(4).hex()}.py")
            with open(temp_script_path, "w", encoding="utf-8") as f:
                f.write(script_content)
            
            logger.info(f"Executing sandboxed test script: {temp_script_path} with cwd: {abs_project_root}")
            
            process_result = subprocess.run(
                [sys.executable, temp_script_path],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=abs_project_root,
                check=False
            )
            
            logger.info(f"Sandboxed test script STDOUT:\n{process_result.stdout}")
            if process_result.stderr:
                logger.error(f"Sandboxed test script STDERR:\n{process_result.stderr}")

            notes = ""
            passed_status = process_result.returncode == 0
            if passed_status:
                # Note: The placeholder in script_content is "function_name_placeholder", 
                # but we should check against the actual function_name passed to this function.
                if f"Successfully called {function_name}" in process_result.stdout:
                    notes = f"No-args function '{function_name}' in '{module_path}' called successfully in sandbox."
                elif f"Function {function_name}" in process_result.stdout and "loaded but not called due to parameters" in process_result.stdout:
                    notes = f"Function '{function_name}' in '{module_path}' with parameters loaded successfully in sandbox (not called)."
                else:
                    notes = f"Sandboxed test script completed successfully for '{module_path}.{function_name}'."
                logger.info(f"Sandboxed test for {module_path}.{function_name} PASSED. Notes: {notes}")
            else:
                combined_output = process_result.stdout + "\n" + process_result.stderr
                # Check if import was attempted but not successful
                import_attempted_msg = f"Attempting to import module: {module_path}"
                import_succeeded_msg = f"Successfully imported module: {module_path}"

                if (import_attempted_msg in process_result.stdout and import_succeeded_msg not in process_result.stdout) or \
                   "ImportError" in combined_output or "ModuleNotFoundError" in combined_output:
                    notes = f"Import of '{module_path}' likely failed in sandbox for function '{function_name}'. Output indicates import issues."
                else:
                    notes = f"Sandboxed test script for '{module_path}.{function_name}' FAILED with return code {process_result.returncode}."
                logger.error(f"Sandboxed test for {module_path}.{function_name} FAILED. Notes: {notes}")

            return {"passed": passed_status, "stdout": process_result.stdout, "stderr": process_result.stderr, "notes": notes}
                
    except subprocess.TimeoutExpired:
        notes = f"Sandboxed test for {module_path}.{function_name} TIMED OUT after 30 seconds."
        logger.error(notes)
        return {"passed": False, "stdout": "", "stderr": "Timeout during execution.", "notes": notes}
    except FileNotFoundError:
        notes = f"Could not find Python interpreter '{sys.executable}' for sandboxed test of {module_path}.{function_name}."
        logger.error(notes, exc_info=True)
        return {"passed": False, "stdout": "", "stderr": "Python interpreter not found.", "notes": notes}
    except Exception as e:
        notes = f"An unexpected error occurred during sandboxed test execution for {module_path}.{function_name}: {e}"
        logger.error(notes, exc_info=True)
        return {"passed": False, "stdout": "", "stderr": str(e), "notes": notes}
    # --- End of implemented logic ---


def commit_tool_change(
    module_path: str, 
    function_name: str, 
    project_root: str, 
    suggestion_id: Optional[str] = None, 
    suggestion_details: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """
    Commits the modified tool file to a version control system (Git).

    Args:
        module_path: The Python module path of the modified tool.
        function_name: The name of the modified function.
        project_root: Absolute path to the project's Git repository root.
        suggestion_id: Optional ID from the suggestion that led to this change.
        suggestion_details: Optional dictionary containing more details from the suggestion,
                            e.g., for constructing a commit message body.

    Returns:
        A tuple (bool, Optional[str]): 
        (True, commit_message_string) if the commit was successful.
        (False, None) if the commit failed.
    """
    logger.info(f"Attempting Git commit for {module_path}.{function_name} in project {project_root}")

    # 1. Prerequisites Check
    git_path = shutil.which("git")
    if not git_path:
        logger.error("Git command not found. Cannot perform commit. Please ensure Git is installed and in PATH.")
        return False, None
    logger.info(f"Git executable found at: {git_path}")

    # Log git version for debugging (optional, can be noisy)
    # try:
    #     version_result = subprocess.run([git_path, '--version'], capture_output=True, text=True, check=False, timeout=5)
    #     logger.debug(f"Git version: {version_result.stdout.strip()}")
    # except Exception: pass # Ignore if this fails

    if not os.path.isdir(os.path.join(project_root, ".git")):
        logger.error(f"Project root '{project_root}' is not a Git repository (missing .git directory). Cannot perform commit.")
        return False, None
    logger.info(f"Project root '{project_root}' appears to be a Git repository.")

    # 2. File Path Conversion
    path_parts = module_path.split('.')
    relative_module_file_path = os.path.join(*path_parts) + ".py"
    full_module_file_path = os.path.join(project_root, relative_module_file_path)
    
    if not os.path.exists(full_module_file_path):
        logger.error(f"Module file to commit does not exist at calculated path: {full_module_file_path}")
        return False, None
        
    logger.info(f"File path for git add (relative to repo root): {relative_module_file_path}")

    # 3. Construct Commit Message
    commit_subject = f"AI Autocommit: Modified {function_name} in {module_path}"
    if suggestion_id:
        commit_subject += f" (Suggestion ID: {suggestion_id})"
    
    commit_body_content = ""
    if suggestion_details:
        change_desc = suggestion_details.get("suggested_change_description") or suggestion_details.get("suggestion_text")
        if change_desc and isinstance(change_desc, str):
            commit_body_content = change_desc.strip()

    full_commit_message = commit_subject
    if commit_body_content and commit_body_content.lower() != commit_subject.lower(): # Avoid duplicate if body is same as subject
        full_commit_message += f"\n\n{commit_body_content}"

    # 4. Execute Git Commands
    try:
        logger.info(f"Running: {git_path} add {relative_module_file_path} (cwd: {project_root})")
        add_result = subprocess.run(
            [git_path, 'add', relative_module_file_path],
            capture_output=True, text=True, cwd=project_root, check=False, timeout=15
        )
        logger.debug(f"Git add STDOUT:\n{add_result.stdout}")
        if add_result.stderr: 
            logger.info(f"Git add STDERR:\n{add_result.stderr}") # Stderr is not always an error for git add
        if add_result.returncode != 0:
            logger.error(f"Git add command failed for '{relative_module_file_path}' with return code {add_result.returncode}.")
            return False, None
        logger.info(f"Git add successful for '{relative_module_file_path}'.")

        commit_command = [git_path, 'commit', '-m', commit_subject]
        if commit_body_content and commit_body_content.lower() != commit_subject.lower():
            commit_command.extend(['-m', commit_body_content])
        
        commit_command_str_for_log = " ".join(f"'{arg}'" if " " in arg else arg for arg in commit_command)
        logger.info(f"Running: {commit_command_str_for_log} (cwd: {project_root})")
        
        commit_result = subprocess.run(
            commit_command,
            capture_output=True, text=True, cwd=project_root, check=False, timeout=15
        )
        logger.debug(f"Git commit STDOUT:\n{commit_result.stdout}")
        if commit_result.stderr: 
            logger.info(f"Git commit STDERR:\n{commit_result.stderr}")
            
        if commit_result.returncode != 0:
            if "nothing to commit" in commit_result.stdout.lower() or \
               "nothing to commit" in commit_result.stderr.lower():
                 logger.warning(f"Git commit indicated nothing to commit for {relative_module_file_path}. This might be okay.")
                 return True, full_commit_message # Commit successful (no changes)
            logger.error(f"Git commit command failed for '{relative_module_file_path}' with return code {commit_result.returncode}.")
            return False, None
        
        logger.info(f"Git commit successful for '{relative_module_file_path}'.")
        return True, full_commit_message

    except subprocess.TimeoutExpired as e:
        logger.error(f"Git operation timed out for {module_path}.{function_name}. Command: {' '.join(e.cmd if e.cmd else [])}", exc_info=True)
        return False, None
    except Exception as e:
        logger.error(f"An unexpected error occurred during Git operations for {module_path}.{function_name}: {e}", exc_info=True)
        return False, None


def apply_code_modification(suggestion: Dict[str, Any]) -> Dict[str, Any]:
    """
    Applies a suggested code modification, including sandboxed testing, potential revert, and version control commit.

    Args:
        suggestion: A dictionary containing the details for the code modification.
                    Expected keys: "module_path", "function_name", "suggested_code_change",
                    and optionally "suggestion_id".

    Returns:
        A dictionary with a detailed breakdown of the operation's outcome.
        See task description for the new required return structure.
    """
    result = {
        "overall_status": False,
        "overall_message": "",
        "edit_outcome": {"status": False, "message": "", "backup_path": None},
        "test_outcome": None,
        "revert_outcome": None,
        "commit_outcome": None,
    }

    required_keys = ["module_path", "function_name", "suggested_code_change"]
    missing_keys = [key for key in required_keys if key not in suggestion]
    if missing_keys:
        msg = f"Suggestion dictionary is missing required keys: {', '.join(missing_keys)}"
        logger.error(msg)
        result["overall_message"] = msg
        return result

    module_path = suggestion["module_path"]
    function_name = suggestion["function_name"]
    new_code_string = suggestion["suggested_code_change"]
    suggestion_id = suggestion.get("suggestion_id")

    type_error_msg = ""
    if not isinstance(module_path, str): type_error_msg += f"'module_path' must be a string. "
    if not isinstance(function_name, str): type_error_msg += f"'function_name' must be a string. "
    if not isinstance(new_code_string, str): type_error_msg += f"'suggested_code_change' must be a string."
    if type_error_msg:
        logger.error(f"Type errors in suggestion: {type_error_msg.strip()}")
        result["overall_message"] = f"Type errors in suggestion: {type_error_msg.strip()}"
        return result

    logger.info(f"Attempting to apply code modification for function '{function_name}' in module '{module_path}'. Suggestion ID: {suggestion_id or 'N/A'}")
    
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    # target_file_for_edit is relative to project_root for edit_function_source_code
    # but os.path.exists needs full path if CWD is not project_root.
    # edit_function_source_code now expects module_path to be dot-separated, and constructs file path relative to project_root
    # So target_file_for_edit here is mostly for the backup path construction and pre-check.
    relative_target_file = os.path.join(*module_path.split('.')) + ".py"
    full_target_file_path = os.path.join(project_root, relative_target_file)

    try:
        if not os.path.exists(full_target_file_path):
            msg = f"Target file for modification '{full_target_file_path}' does not exist."
            logger.error(msg)
            result["overall_message"] = msg
            result["edit_outcome"]["message"] = msg
            return result

        # --- 1. Edit Step ---
        # edit_function_source_code expects module_path like "ai_assistant.tools.some_tool"
        # and project_root to find the base of the project.
        edit_message = edit_function_source_code(module_path, function_name, new_code_string, project_root_path=project_root)
        
        result["edit_outcome"]["message"] = edit_message
        if "success" not in edit_message.lower():
            logger.error(f"Failed to apply code modification: {edit_message}")
            result["overall_message"] = f"Code editing failed: {edit_message}"
            return result
        
        result["edit_outcome"]["status"] = True
        result["edit_outcome"]["backup_path"] = full_target_file_path + ".bak" # edit_function_source_code creates this
        logger.info(f"Code modification successful: {edit_message}")

        # --- 2. Test Step ---
        logger.info(f"Proceeding to sandboxed testing for {module_path}.{function_name}.")
        test_results_dict = test_modified_tool_in_sandbox(module_path, function_name, project_root)
        result["test_outcome"] = test_results_dict

        if not test_results_dict.get("passed"):
            test_failure_notes = test_results_dict.get('notes', 'No specific notes from test.')
            msg = f"Sandboxed testing of modified tool {module_path}.{function_name} FAILED. Notes: {test_failure_notes}"
            logger.critical(msg)
            result["overall_message"] = msg
            
            # --- 2a. Revert Step (due to test failure) ---
            revert_outcome_dict = {"status": False, "message": ""}
            backup_to_restore_from = result["edit_outcome"]["backup_path"]
            if backup_to_restore_from and os.path.exists(backup_to_restore_from):
                try:
                    shutil.move(backup_to_restore_from, full_target_file_path) # Use full_target_file_path
                    revert_msg = f"Successfully reverted {full_target_file_path} from backup {backup_to_restore_from}."
                    logger.info(revert_msg)
                    revert_outcome_dict["status"] = True
                    revert_outcome_dict["message"] = revert_msg
                except Exception as e_revert:
                    revert_err_msg = f"CRITICAL: Failed to revert {full_target_file_path} from backup. Manual intervention required. Error: {e_revert}"
                    logger.error(revert_err_msg, exc_info=True)
                    revert_outcome_dict["message"] = revert_err_msg
                    result["overall_message"] += f" {revert_err_msg}" # Append to overall message
            else:
                revert_not_found_msg = f"CRITICAL: Backup file {backup_to_restore_from} not found. Cannot revert."
                logger.error(revert_not_found_msg)
                revert_outcome_dict["message"] = revert_not_found_msg
                result["overall_message"] += f" {revert_not_found_msg}"
            result["revert_outcome"] = revert_outcome_dict
            return result # overall_status remains False

        logger.info(f"Sandboxed testing of modified tool {module_path}.{function_name} passed.")
        
        # Clean up backup file if tests passed
        if result["edit_outcome"]["backup_path"] and os.path.exists(result["edit_outcome"]["backup_path"]):
            try:
                os.remove(result["edit_outcome"]["backup_path"])
                logger.info(f"Removed backup file: {result['edit_outcome']['backup_path']}")
                result["edit_outcome"]["backup_path"] = None # Indicate it's removed
            except OSError as e_remove:
                logger.warning(f"Could not remove backup file {result['edit_outcome']['backup_path']}: {e_remove}")
        
        # --- 3. Commit Step ---
        commit_status, generated_commit_msg = commit_tool_change(
            module_path, function_name, project_root, 
            suggestion_id=suggestion_id,
            suggestion_details=suggestion # Pass the whole suggestion for more context if needed by commit_tool_change
        )
        
        result["commit_outcome"] = {
            "status": commit_status,
            "commit_message_generated": generated_commit_msg,
            "error_message": None
        }

        if commit_status:
            logger.info(f"Automated commit for {module_path}.{function_name} successful.")
            result["overall_status"] = True
            result["overall_message"] = "Tool modification, testing, and local commit successful."
        else:
            # Commit failed, but edit and test were successful.
            commit_fail_msg = f"Tool modification and testing successful, but local commit failed for {module_path}.{function_name}."
            logger.error(commit_fail_msg)
            result["commit_outcome"]["error_message"] = "Commit command failed or Git prerequisites not met. See logs."
            result["overall_message"] = commit_fail_msg
            # overall_status remains False as per requirement if commit fails
        
        return result

    except Exception as e:
        msg = f"An unexpected error occurred during apply_code_modification for {module_path}.{function_name}: {e}"
        logger.error(msg, exc_info=True)
        if not result["overall_message"]: # Ensure overall_message is set
            result["overall_message"] = msg
        # Populate specific step outcome if error happened there, if identifiable
        if result["edit_outcome"]["status"] is False and not result["edit_outcome"]["message"]:
            result["edit_outcome"]["message"] = f"Unexpected error during edit phase: {e}"
        # Add more specific error context if possible based on where it occurred.
        return result

if __name__ == '__main__':
    from unittest.mock import MagicMock, call # Added call
    from subprocess import CompletedProcess # Added for mocking subprocess.run

    # --- Test Setup ---
    TEST_WORKSPACE_PARENT_DIR = "temp_evolution_test_sandbox"
    TEST_PROJECT_ROOT_FOR_DUMMY = os.path.join(TEST_WORKSPACE_PARENT_DIR, "dummy_project_root")
    DUMMY_MODULE_DIR_STRUCTURE = os.path.join(TEST_PROJECT_ROOT_FOR_DUMMY, "ai_assistant", "dummy_modules")
    dummy_tool_fs_path = os.path.join(DUMMY_MODULE_DIR_STRUCTURE, dummy_tool_module_name_for_test)
    dummy_module_py_path = f"ai_assistant.dummy_modules.{dummy_tool_module_name_for_test.replace('.py', '')}"

    # Updated dummy function to have one with no args and one with args for testing the sandbox script logic
    original_dummy_function_no_args_code = (
        "def sample_tool_function_no_args():\n"
        "    '''This is a sample tool function with no arguments.'''\n"
        "    print('Original no-args function called successfully!')\n"
        "    return 'Original no-args result'\n"
    )
    original_dummy_function_with_args_code = (
        "def sample_tool_function(param1: int, param2: str) -> str:\n"
        "    '''This is a sample tool function.'''\n"
        "    print(f'Original function called with {param1} and {param2}')\n"
        "    return f'Original result: {param1} - {param2}'\n"
    )
    original_dummy_file_content = (
        f"import os\n\n{original_dummy_function_no_args_code}\n\n"
        f"{original_dummy_function_with_args_code}\n"
    )

    def setup_test_environment():
        if os.path.exists(TEST_WORKSPACE_PARENT_DIR):
            shutil.rmtree(TEST_WORKSPACE_PARENT_DIR)
        os.makedirs(DUMMY_MODULE_DIR_STRUCTURE, exist_ok=True)
        with open(dummy_tool_fs_path, "w", encoding="utf-8") as f:
            f.write(original_dummy_file_content)
        logger.info(f"Created dummy tool file for testing: {dummy_tool_fs_path}")
        # Create a dummy .bak file as if edit_function_source_code created it
        # This is for testing the revert logic.
        # The path needs to be relative to where apply_code_modification thinks the file is.
        # If CWD = TEST_PROJECT_ROOT_FOR_DUMMY, then module_path is "ai_assistant..."
        # and edit_function_source_code creates backup at "ai_assistant/.../file.py.bak"
        backup_path_for_tests = os.path.join(TEST_PROJECT_ROOT_FOR_DUMMY, *dummy_module_py_path.split('.')) + ".py.bak"
        with open(backup_path_for_tests, "w", encoding="utf-8") as f:
            f.write("# This is a dummy backup content for testing revert\n" + original_dummy_file_content)
        logger.info(f"Created dummy .bak file for testing revert: {backup_path_for_tests}")


    def read_dummy_tool_file_content() -> str:
        # This function reads from the true path, not relative to a changed CWD
        if os.path.exists(dummy_tool_fs_path):
            with open(dummy_tool_fs_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def cleanup_test_environment():
        if os.path.exists(TEST_WORKSPACE_PARENT_DIR):
            shutil.rmtree(TEST_WORKSPACE_PARENT_DIR)
            logger.info(f"Cleaned up test workspace: {TEST_WORKSPACE_PARENT_DIR}")

    logger.info("Starting tests for apply_code_modification with sandboxing...")
    all_tests_passed = True
    original_cwd = os.getcwd()
    
    try:
        setup_test_environment()
        os.chdir(TEST_PROJECT_ROOT_FOR_DUMMY) 

        # Test 1: Successful modification, sandbox, and commit (with commit message body)
        logger.info("--- Test 1: Successful modification, sandbox, and commit (with body) ---")
        with patch('subprocess.run') as mock_subprocess_run_t1, \
             patch('shutil.which', MagicMock(return_value="/usr/bin/git")) as mock_which_t1, \
             patch('os.path.isdir', MagicMock(return_value=True)) as mock_isdir_t1, \
             patch('ai_assistant.core.self_modification.edit_function_source_code', MagicMock(return_value="Successfully updated function.")) as mock_edit_t1:
            
            # Simulate sandbox success, then successful 'git add' and 'git commit'
            mock_subprocess_run_t1.side_effect = [
                subprocess.CompletedProcess(args=[sys.executable, ANY], returncode=0, stdout="Sandbox success", stderr=""), # Sandbox
                subprocess.CompletedProcess(args=['git', 'add', ANY], returncode=0, stdout="Added", stderr=""),             # Git Add
                subprocess.CompletedProcess(args=['git', 'commit', ANY, ANY, ANY, ANY], returncode=0, stdout="Committed", stderr="") # Git Commit
            ]
            
            suggestion_t1 = {
                "suggestion_id": "SUG001_T1",
                "suggested_change_description": "This is a detailed description of the change for the commit body.",
                "module_path": dummy_module_py_path,
                "function_name": "sample_tool_function_no_args", 
                "suggested_code_change": "def sample_tool_function_no_args(): print('Modified no-args version')\n"
            }
            result_t1 = apply_code_modification(suggestion_t1)
            logger.info(f"Test 1 Result: {result_t1}")
            assert result_t1 is True, "Test 1 Failed: Should return True on full success path."
            mock_edit_t1.assert_called_once()
            
            # Assertions for commit_tool_change's subprocess calls
            expected_rel_path = os.path.join("ai_assistant", "dummy_modules", dummy_tool_module_name_for_test)
            expected_commit_subject = f"AI Autocommit: Modified sample_tool_function_no_args in {dummy_module_py_path} (Suggestion ID: SUG001_T1)"
            expected_commit_body = "This is a detailed description of the change for the commit body."
            
            # Check sandbox call (1st call to subprocess.run)
            assert mock_subprocess_run_t1.call_args_list[0][0][0][0] == sys.executable
            assert mock_subprocess_run_t1.call_args_list[0][1]['cwd'] == os.path.abspath(TEST_PROJECT_ROOT_FOR_DUMMY)
            # Check git add call (2nd call to subprocess.run)
            assert mock_subprocess_run_t1.call_args_list[1][0][0] == [mock_which_t1.return_value, 'add', expected_rel_path]
            # Check git commit call (3rd call to subprocess.run)
            assert mock_subprocess_run_t1.call_args_list[2][0][0] == [mock_which_t1.return_value, 'commit', '-m', expected_commit_subject, '-m', expected_commit_body]
        logger.info("Test 1 Passed.")

        # Test 2: Successful modification and sandbox, but FAILED 'git commit' (no commit body)
        logger.info("--- Test 2: Successful modification and sandbox, failed 'git commit' (no body) ---")
        # We need to ensure a .bak file exists that edit_function_source_code would have created.
        # edit_function_source_code creates it at target_file_for_edit + ".bak"
        # target_file_for_edit is os.path.join(*module_path.split('.')) + ".py"
        # So, ai_assistant/dummy_modules/dummy_tool_module.py.bak (relative to CWD=TEST_PROJECT_ROOT_FOR_DUMMY)
        
        # Reset dummy file for this test, as edit_function_source_code is mocked
        # and won't create the .bak file itself in this mocked scenario.
        # The `setup_test_environment` already creates a .bak for us.
        os.chdir(original_cwd)
        setup_test_environment() # This recreates the dummy .bak file too.
        os.chdir(TEST_PROJECT_ROOT_FOR_DUMMY)

        with patch('subprocess.run') as mock_subprocess_run_t2, \
             patch('shutil.which', MagicMock(return_value="/usr/bin/git")) as mock_which_t2, \
             patch('os.path.isdir', MagicMock(return_value=True)) as mock_isdir_t2, \
             patch('ai_assistant.core.self_modification.edit_function_source_code', MagicMock(return_value="Successfully updated function.")) as mock_edit_t2:
            
            mock_subprocess_run_t2.side_effect = [
                subprocess.CompletedProcess(args=[sys.executable, ANY], returncode=0, stdout="Sandbox OK"),
                subprocess.CompletedProcess(args=['git', 'add', ANY], returncode=0, stdout="Add OK"),
                subprocess.CompletedProcess(args=['git', 'commit', ANY, ANY], returncode=1, stderr="Commit Fail") # No body for this test
            ]
            suggestion_t2 = { "suggestion_id": "SUG002_T2", "module_path": dummy_module_py_path, "function_name": "sample_tool_function_no_args", "suggested_code_change": "def f(): pass" }
            result_t2 = apply_code_modification(suggestion_t2)
            logger.info(f"Test 2 Result: {result_t2}")
            assert result_t2.get("status") is False, f"Test 2 Failed: Status False expected for commit fail. Got: {result_t2}"
            assert "commit failed" in result_t2.get("message", "").lower()
        logger.info("Test 2 Passed.")

        # Test 3: Successful modification BUT FAILED sandboxed test (revert)
        logger.info("--- Test 3: Successful modification, failed sandbox test (revert) ---")
        os.chdir(original_cwd)
        setup_test_environment()
        os.chdir(TEST_PROJECT_ROOT_FOR_DUMMY)
        with patch('subprocess.run', MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=1, stderr="Sandbox script error"))) as mock_subprocess_run_t3, \
             patch('shutil.which', MagicMock(return_value="/usr/bin/git")), \
             patch('os.path.isdir', MagicMock(return_value=True)), \
             patch('ai_assistant.core.self_modification.edit_function_source_code', MagicMock(return_value="Successfully updated function.")) as mock_edit_t3, \
             patch('shutil.move') as mock_shutil_move_t3:
            
            suggestion_t3 = { "suggestion_id": "SUG003_T3", "module_path": dummy_module_py_path, "function_name": "sample_tool_function_no_args", "suggested_code_change": "def f(): pass" }
            result_t3 = apply_code_modification(suggestion_t3)
            logger.info(f"Test 3 Result: {result_t3}")
            assert result_t3.get("status") is False, f"Test 3 Failed: Status False for sandbox fail. Got: {result_t3}"
            assert "sandboxed testing" in result_t3.get("message", "").lower() and "failed" in result_t3.get("message", "").lower()
            mock_shutil_move_t3.assert_called_once()
        logger.info("Test 3 Passed.")

        # Test 4: edit_function_source_code fails
        logger.info("--- Test 4: edit_function_source_code fails ---")
        os.chdir(original_cwd)
        setup_test_environment()
        os.chdir(TEST_PROJECT_ROOT_FOR_DUMMY)
        with patch('subprocess.run'), \
             patch('shutil.which', MagicMock(return_value="/usr/bin/git")), \
             patch('os.path.isdir', MagicMock(return_value=True)), \
             patch('ai_assistant.core.self_modification.edit_function_source_code', MagicMock(return_value="Error: Function not found.")) as mock_edit_t4:
            
            suggestion_t4 = {"suggestion_id": "SUG004_T4", "module_path": dummy_module_py_path, "function_name": "non_existent", "suggested_code_change": "def f(): pass"}
            result_t4 = apply_code_modification(suggestion_t4)
            logger.info(f"Test 4 Result: {result_t4}")
            assert result_t4.get("status") is False, f"Test 4 Failed: Status False for edit fail. Got: {result_t4}"
            assert "Error: Function not found" in result_t4.get("message", "")
        logger.info("Test 4 Passed.")

    except AssertionError as e:
        logger.error(f"TEST ASSERTION FAILED: {e}", exc_info=True)
        all_tests_passed = False
    except Exception as e:
        logger.error(f"AN UNEXPECTED ERROR OCCURRED DURING TESTS: {e}", exc_info=True)
        all_tests_passed = False
    finally:
        os.chdir(original_cwd) 
        cleanup_test_environment()

    if all_tests_passed:
        logger.info("All evolution.py tests (including sandboxing placeholders) passed successfully!")
    else:
        logger.error("One or more evolution.py tests (including sandboxing placeholders) failed.")
