import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Any, Optional
from unittest.mock import patch, MagicMock, ANY, AsyncMock # Added AsyncMock

from ai_assistant.core.self_modification import edit_function_source_code
import asyncio

# Configure logger for this module
logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

dummy_tool_module_name_for_test = "dummy_tool_module.py"


def test_modified_tool_in_sandbox(module_path: str, function_name: str, project_root: str) -> Dict[str, Any]:
    logger.info(
        f"Placeholder: Sandboxed testing for {module_path}.{function_name} at project_root '{project_root}' is not yet implemented. Defaulting to True (success)."
    )
    script_content_template = """
import importlib
import sys
import traceback
import os

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
        success = True

except Exception as e:
    log_print(f"Error during sandboxed test of {{module_path_placeholder}}.{{function_name_placeholder}}:")
    tb_lines = traceback.format_exc().splitlines()
    for line in tb_lines:
        log_print(line)
    success = False

if not success:
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
                if f"Successfully called {function_name}" in process_result.stdout:
                    notes = f"No-args function '{function_name}' in '{module_path}' called successfully in sandbox."
                elif f"Function {function_name}" in process_result.stdout and "loaded but not called due to parameters" in process_result.stdout:
                    notes = f"Function '{function_name}' in '{module_path}' with parameters loaded successfully in sandbox (not called)."
                else:
                    notes = f"Sandboxed test script completed successfully for '{module_path}.{function_name}'."
                logger.info(f"Sandboxed test for {module_path}.{function_name} PASSED. Notes: {notes}")
            else:
                combined_output = process_result.stdout + "\n" + process_result.stderr
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

async def commit_tool_change(
    module_path: str,
    function_name: str,
    project_root: str,
    suggestion_id: Optional[str] = None,
    suggestion_details: Optional[Dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    logger.info(f"Attempting Git commit for {module_path}.{function_name} in project {project_root}")

    git_path = shutil.which("git")
    if not git_path:
        logger.error("Git command not found. Cannot perform commit. Please ensure Git is installed and in PATH.")
        return False, None
    logger.info(f"Git executable found at: {git_path}")

    if not os.path.isdir(os.path.join(project_root, ".git")):
        logger.error(f"Project root '{project_root}' is not a Git repository (missing .git directory). Cannot perform commit.")
        return False, None
    logger.info(f"Project root '{project_root}' appears to be a Git repository.")

    path_parts = module_path.split('.')
    relative_module_file_path = os.path.join(*path_parts) + ".py"
    full_module_file_path = os.path.join(project_root, relative_module_file_path)
    
    if not os.path.exists(full_module_file_path):
        logger.error(f"Module file to commit does not exist at calculated path: {full_module_file_path}")
        return False, None
        
    logger.info(f"File path for git add (relative to repo root): {relative_module_file_path}")

    commit_subject = f"AI Autocommit: Modified {function_name} in {module_path}"
    if suggestion_id:
        commit_subject += f" (Suggestion ID: {suggestion_id})"
    
    commit_body_content = ""
    if suggestion_details:
        change_desc = suggestion_details.get("suggested_change_description") or suggestion_details.get("suggestion_text")
        if change_desc and isinstance(change_desc, str):
            commit_body_content = change_desc.strip()

    full_commit_message = commit_subject
    if commit_body_content and commit_body_content.lower() != commit_subject.lower():
        full_commit_message += f"\n\n{commit_body_content}"

    try:
        logger.info(f"Running: {git_path} add {relative_module_file_path} (cwd: {project_root})")
        add_result = await asyncio.to_thread(
            subprocess.run,
            [git_path, 'add', relative_module_file_path],
            capture_output=True, text=True, cwd=project_root, check=False, timeout=15
        )
        logger.debug(f"Git add STDOUT:\n{add_result.stdout}")
        if add_result.stderr: 
            logger.info(f"Git add STDERR:\n{add_result.stderr}")
        if add_result.returncode != 0:
            logger.error(f"Git add command failed for '{relative_module_file_path}' with return code {add_result.returncode}.")
            return False, None
        logger.info(f"Git add successful for '{relative_module_file_path}'.")

        commit_command = [git_path, 'commit', '-m', commit_subject]
        if commit_body_content and commit_body_content.lower() != commit_subject.lower():
            commit_command.extend(['-m', commit_body_content])
        
        commit_command_str_for_log = " ".join(f"'{arg}'" if " " in arg else arg for arg in commit_command)
        logger.info(f"Running: {commit_command_str_for_log} (cwd: {project_root})")
        
        commit_result = await asyncio.to_thread(
            subprocess.run,
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
                 return True, full_commit_message
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


async def apply_code_modification(suggestion: Dict[str, Any]) -> Dict[str, Any]:
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
    relative_target_file = os.path.join(*module_path.split('.')) + ".py"
    full_target_file_path = os.path.join(project_root, relative_target_file)

    try:
        if not os.path.exists(full_target_file_path):
            msg = f"Target file for modification '{full_target_file_path}' does not exist."
            logger.error(msg)
            result["overall_message"] = msg
            result["edit_outcome"]["message"] = msg
            return result

        edit_message = await edit_function_source_code(module_path, function_name, new_code_string, project_root_path=project_root)
        
        result["edit_outcome"]["message"] = edit_message
        if "success" not in edit_message.lower():
            logger.error(f"Failed to apply code modification: {edit_message}")
            result["overall_message"] = f"Code editing failed: {edit_message}"
            return result
        
        result["edit_outcome"]["status"] = True
        result["edit_outcome"]["backup_path"] = full_target_file_path + ".bak"
        logger.info(f"Code modification successful: {edit_message}")

        logger.info(f"Proceeding to sandboxed testing for {module_path}.{function_name}.")
        test_results_dict = test_modified_tool_in_sandbox(module_path, function_name, project_root)
        result["test_outcome"] = test_results_dict

        if not test_results_dict.get("passed"):
            test_failure_notes = test_results_dict.get('notes', 'No specific notes from test.')
            msg = f"Sandboxed testing of modified tool {module_path}.{function_name} FAILED. Notes: {test_failure_notes}"
            logger.critical(msg)
            result["overall_message"] = msg
            
            revert_outcome_dict = {"status": False, "message": ""}
            backup_to_restore_from = result["edit_outcome"]["backup_path"]
            if backup_to_restore_from and os.path.exists(backup_to_restore_from):
                try:
                    shutil.move(backup_to_restore_from, full_target_file_path)
                    revert_msg = f"Successfully reverted {full_target_file_path} from backup {backup_to_restore_from}."
                    logger.info(revert_msg)
                    revert_outcome_dict["status"] = True
                    revert_outcome_dict["message"] = revert_msg
                except Exception as e_revert:
                    revert_err_msg = f"CRITICAL: Failed to revert {full_target_file_path} from backup. Manual intervention required. Error: {e_revert}"
                    logger.error(revert_err_msg, exc_info=True)
                    revert_outcome_dict["message"] = revert_err_msg
                    result["overall_message"] += f" {revert_err_msg}"
            else:
                revert_not_found_msg = f"CRITICAL: Backup file {backup_to_restore_from} not found. Cannot revert."
                logger.error(revert_not_found_msg)
                revert_outcome_dict["message"] = revert_not_found_msg
                result["overall_message"] += f" {revert_not_found_msg}"
            result["revert_outcome"] = revert_outcome_dict
            return result

        logger.info(f"Sandboxed testing of modified tool {module_path}.{function_name} passed.")
        
        if result["edit_outcome"]["backup_path"] and os.path.exists(result["edit_outcome"]["backup_path"]):
            try:
                os.remove(result["edit_outcome"]["backup_path"])
                logger.info(f"Removed backup file: {result['edit_outcome']['backup_path']}")
                result["edit_outcome"]["backup_path"] = None
            except OSError as e_remove:
                logger.warning(f"Could not remove backup file {result['edit_outcome']['backup_path']}: {e_remove}")
        
        commit_status, generated_commit_msg = await commit_tool_change(
            module_path, function_name, project_root,
            suggestion_id=suggestion_id,
            suggestion_details=suggestion
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
            commit_fail_msg = f"Tool modification and testing successful, but local commit failed for {module_path}.{function_name}."
            logger.error(commit_fail_msg)
            result["commit_outcome"]["error_message"] = "Commit command failed or Git prerequisites not met. See logs."
            result["overall_message"] = commit_fail_msg
        
        return result

    except Exception as e:
        msg = f"An unexpected error occurred during apply_code_modification for {module_path}.{function_name}: {e}"
        logger.error(msg, exc_info=True)
        if not result["overall_message"]:
            result["overall_message"] = msg
        if result["edit_outcome"]["status"] is False and not result["edit_outcome"]["message"]:
            result["edit_outcome"]["message"] = f"Unexpected error during edit phase: {e}"
        return result

if __name__ == '__main__':
    from unittest.mock import MagicMock, call, AsyncMock # Ensure AsyncMock is imported
    from subprocess import CompletedProcess

    async def main_tests_evolution():
        TEST_WORKSPACE_PARENT_DIR = "temp_evolution_test_sandbox"
        TEST_PROJECT_ROOT_FOR_DUMMY = os.path.join(TEST_WORKSPACE_PARENT_DIR, "dummy_project_root")
        DUMMY_MODULE_DIR_STRUCTURE = os.path.join(TEST_PROJECT_ROOT_FOR_DUMMY, "ai_assistant", "dummy_modules")
        dummy_tool_fs_path = os.path.join(DUMMY_MODULE_DIR_STRUCTURE, dummy_tool_module_name_for_test)
        dummy_module_py_path = f"ai_assistant.dummy_modules.{dummy_tool_module_name_for_test.replace('.py', '')}"

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
            backup_path_for_tests = os.path.join(TEST_PROJECT_ROOT_FOR_DUMMY, *dummy_module_py_path.split('.')) + ".py.bak"
            with open(backup_path_for_tests, "w", encoding="utf-8") as f:
                f.write("# This is a dummy backup content for testing revert\n" + original_dummy_file_content)
            logger.info(f"Created dummy .bak file for testing revert: {backup_path_for_tests}")

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

            logger.info("--- Test 1: Successful modification, sandbox, and commit (with body) ---")
            with patch('subprocess.run') as mock_subprocess_run_t1, \
                 patch('shutil.which', MagicMock(return_value="/usr/bin/git")) as mock_which_t1, \
                 patch('os.path.isdir', MagicMock(return_value=True)) as mock_isdir_t1, \
                 patch('ai_assistant.core.self_modification.edit_function_source_code', new_callable=AsyncMock, return_value="Successfully updated function.") as mock_edit_t1:

                mock_subprocess_run_t1.side_effect = [
                    subprocess.CompletedProcess(args=[sys.executable, ANY], returncode=0, stdout="Sandbox success", stderr=""),
                    subprocess.CompletedProcess(args=['git', 'add', ANY], returncode=0, stdout="Added", stderr=""),
                    subprocess.CompletedProcess(args=['git', 'commit', ANY, ANY, ANY, ANY], returncode=0, stdout="Committed", stderr="")
                ]

                suggestion_t1 = {
                    "suggestion_id": "SUG001_T1",
                    "suggested_change_description": "This is a detailed description of the change for the commit body.",
                    "module_path": dummy_module_py_path,
                    "function_name": "sample_tool_function_no_args",
                    "suggested_code_change": "def sample_tool_function_no_args(): print('Modified no-args version')\n"
                }
                result_t1 = await apply_code_modification(suggestion_t1)
                logger.info(f"Test 1 Result: {result_t1}")
                assert result_t1["overall_status"] is True, f"Test 1 Failed: Should return overall_status True. Got: {result_t1}"
                mock_edit_t1.assert_called_once()

                expected_rel_path = os.path.join("ai_assistant", "dummy_modules", dummy_tool_module_name_for_test)
                expected_commit_subject = f"AI Autocommit: Modified sample_tool_function_no_args in {dummy_module_py_path} (Suggestion ID: SUG001_T1)"
                expected_commit_body = "This is a detailed description of the change for the commit body."

                assert mock_subprocess_run_t1.call_args_list[0][0][0][0] == sys.executable
                assert mock_subprocess_run_t1.call_args_list[0][1]['cwd'] == os.path.abspath(TEST_PROJECT_ROOT_FOR_DUMMY)
                assert mock_subprocess_run_t1.call_args_list[1][0][0] == [mock_which_t1.return_value, 'add', expected_rel_path]
                assert mock_subprocess_run_t1.call_args_list[2][0][0] == [mock_which_t1.return_value, 'commit', '-m', expected_commit_subject, '-m', expected_commit_body]
            logger.info("Test 1 Passed.")

            logger.info("--- Test 2: Successful modification and sandbox, failed 'git commit' (no body) ---")
            os.chdir(original_cwd)
            setup_test_environment()
            os.chdir(TEST_PROJECT_ROOT_FOR_DUMMY)

            with patch('subprocess.run') as mock_subprocess_run_t2, \
                 patch('shutil.which', MagicMock(return_value="/usr/bin/git")) as mock_which_t2, \
                 patch('os.path.isdir', MagicMock(return_value=True)) as mock_isdir_t2, \
                 patch('ai_assistant.core.self_modification.edit_function_source_code', new_callable=AsyncMock, return_value="Successfully updated function.") as mock_edit_t2:

                mock_subprocess_run_t2.side_effect = [
                    subprocess.CompletedProcess(args=[sys.executable, ANY], returncode=0, stdout="Sandbox OK"),
                    subprocess.CompletedProcess(args=['git', 'add', ANY], returncode=0, stdout="Add OK"),
                    subprocess.CompletedProcess(args=['git', 'commit', ANY, ANY], returncode=1, stderr="Commit Fail")
                ]
                suggestion_t2 = { "suggestion_id": "SUG002_T2", "module_path": dummy_module_py_path, "function_name": "sample_tool_function_no_args", "suggested_code_change": "def f(): pass" }
                result_t2 = await apply_code_modification(suggestion_t2)
                logger.info(f"Test 2 Result: {result_t2}")
                assert result_t2.get("overall_status") is False, f"Test 2 Failed: overall_status False expected for commit fail. Got: {result_t2}"
                assert "commit failed" in result_t2.get("overall_message", "").lower()
            logger.info("Test 2 Passed.")

            logger.info("--- Test 3: Successful modification, failed sandbox test (revert) ---")
            os.chdir(original_cwd)
            setup_test_environment()
            os.chdir(TEST_PROJECT_ROOT_FOR_DUMMY)
            with patch('subprocess.run', MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=1, stderr="Sandbox script error"))) as mock_subprocess_run_t3, \
                 patch('shutil.which', MagicMock(return_value="/usr/bin/git")), \
                 patch('os.path.isdir', MagicMock(return_value=True)), \
                 patch('ai_assistant.core.self_modification.edit_function_source_code', new_callable=AsyncMock, return_value="Successfully updated function.") as mock_edit_t3, \
                 patch('shutil.move') as mock_shutil_move_t3:

                suggestion_t3 = { "suggestion_id": "SUG003_T3", "module_path": dummy_module_py_path, "function_name": "sample_tool_function_no_args", "suggested_code_change": "def f(): pass" }
                result_t3 = await apply_code_modification(suggestion_t3)
                logger.info(f"Test 3 Result: {result_t3}")
                assert result_t3.get("overall_status") is False, f"Test 3 Failed: overall_status False for sandbox fail. Got: {result_t3}"
                assert "sandboxed testing" in result_t3.get("overall_message", "").lower() and "failed" in result_t3.get("overall_message", "").lower()
                mock_shutil_move_t3.assert_called_once()
            logger.info("Test 3 Passed.")

            logger.info("--- Test 4: edit_function_source_code fails ---")
            os.chdir(original_cwd)
            setup_test_environment()
            os.chdir(TEST_PROJECT_ROOT_FOR_DUMMY)
            with patch('subprocess.run'), \
                 patch('shutil.which', MagicMock(return_value="/usr/bin/git")), \
                 patch('os.path.isdir', MagicMock(return_value=True)), \
                 patch('ai_assistant.core.self_modification.edit_function_source_code', new_callable=AsyncMock, return_value="Error: Function not found.") as mock_edit_t4:

                suggestion_t4 = {"suggestion_id": "SUG004_T4", "module_path": dummy_module_py_path, "function_name": "non_existent", "suggested_code_change": "def f(): pass"}
                result_t4 = await apply_code_modification(suggestion_t4)
                logger.info(f"Test 4 Result: {result_t4}")
                assert result_t4.get("overall_status") is False, f"Test 4 Failed: overall_status False for edit fail. Got: {result_t4}"
                assert "Error: Function not found" in result_t4.get("overall_message", "")
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

    asyncio.run(main_tests_evolution())
