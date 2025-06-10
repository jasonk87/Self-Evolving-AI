# Code for the AI assistant's self-modification capabilities.
import importlib
import inspect
from typing import Optional
import ast
import os
import shutil
import logging
import sys
from .diff_utils import generate_diff
from .critical_reviewer import CriticalReviewCoordinator
from .reviewer import ReviewerAgent # Needed to instantiate default reviewers
import asyncio # For running the async review process
from unittest.mock import patch, AsyncMock # For __main__ block mocking
from typing import Optional # Ensure Optional is imported for type hints
from .task_manager import TaskManager, ActiveTaskStatus, ActiveTaskType


# Configure logger for this module
logger = logging.getLogger(__name__)
if not logger.handlers: # Avoid adding multiple handlers if script is reloaded/run multiple times
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


def get_function_source_code(module_path: str, function_name: str) -> Optional[str]:
    """
    Retrieves the source code of a specified function within a given module.

    Args:
        module_path: The Python module path (e.g., "ai_assistant.communication.cli").
        function_name: The name of the function.

    Returns:
        The source code of the function as a string, or None if an error occurs.
    """
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        print(f"Error: Module '{module_path}' not found.")
        return None
    except Exception as e: # pragma: no cover
        print(f"Error importing module '{module_path}': {e}")
        return None

    try:
        function_obj = getattr(module, function_name)
    except AttributeError:
        print(f"Error: Function '{function_name}' not found in module '{module_path}'.")
        return None
    except Exception as e: # pragma: no cover
        print(f"Error getting attribute '{function_name}' from module '{module_path}': {e}")
        return None

    try:
        source_code = inspect.getsource(function_obj)
        return source_code
    except TypeError: # pragma: no cover
        print(f"Error: Source code for '{function_name}' in '{module_path}' is not available (e.g., C extension, built-in).")
        return None
    except OSError: # pragma: no cover
        print(f"Error: Source file for '{module_path}' likely not found, cannot get source for '{function_name}'.")
        return None
    except Exception as e: # pragma: no cover
        print(f"Error getting source code for '{function_name}' in '{module_path}': {e}")
        return None

def _update_parent_task(tm: Optional[TaskManager], p_task_id: Optional[str], status: ActiveTaskStatus, reason: Optional[str] = None, step: Optional[str] = None):
    if tm and p_task_id:
        tm.update_task_status(p_task_id, status, reason=reason, step_desc=step)

def edit_function_source_code(module_path: str, function_name: str, new_code_string: str, project_root_path: str, change_description: str, task_manager: Optional[TaskManager] = None, parent_task_id: Optional[str] = None) -> str:
    """
    Edits the source code of a specified function within a given module file using AST,
    after critical review. Updates status of a parent_task_id via task_manager if provided.

    Args:
        module_path: The Python module path (e.g., "ai_assistant.custom_tools.my_extra_tools").
        function_name: The name of the function to modify.
        new_code_string: A string containing the new, complete source code for the function.
        project_root_path: The absolute path to the root of the project.
        change_description: A description of the change being made, for review context.

    Returns:
        A success message if the modification was successful, or an error message string if not.
    """
    file_path = ""
    try:
        if not os.path.isabs(project_root_path):
            # Attempt to make it absolute, or raise error if it's critical for your setup
            # For now, we'll log a warning and proceed, but this might need stricter handling.
            logger.warning(f"project_root_path '{project_root_path}' is not absolute. Attempting to resolve.")
            project_root_path = os.path.abspath(project_root_path)
            if not os.path.isdir(project_root_path): # pragma: no cover
                 err_msg = f"Error: Resolved project_root_path '{project_root_path}' is not a valid directory."
                 logger.error(err_msg)
                 return err_msg

        relative_module_file_path = os.path.join(*module_path.split('.')) + ".py"
        file_path = os.path.join(project_root_path, relative_module_file_path)

        original_function_code_for_diff = get_function_source_code(module_path, function_name)
        if original_function_code_for_diff is None:
            err_msg = f"Error: Could not retrieve original source code for function '{function_name}' in module '{module_path}' for review."
            logger.error(err_msg)
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step="Get original code for diff")
            return err_msg

        code_diff = generate_diff(original_function_code_for_diff, new_code_string, file_name=f"{module_path}/{function_name}")
        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc="Generated diff, awaiting critical review")

        if not code_diff:
            logger.info(f"Proposed code for '{function_name}' in '{module_path}' is identical to the current code. No changes to apply.")
            # If no diff, it's technically a success for this function's scope, but no change applied.
            # The parent task might still complete successfully if this was the only action.
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Code identical, no changes applied.", step_desc="Diff generation found no changes")
            return f"No changes detected for function '{function_name}' in module '{module_path}'. Code is identical."

        # --- Critical Review Step ---
        critic1 = ReviewerAgent()
        critic2 = ReviewerAgent()
        coordinator = CriticalReviewCoordinator(critic1, critic2)

        logger.info(f"Requesting critical review for changes to '{function_name}' in '{module_path}'...")
        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc=f"Performing critical review for {function_name}")
        try:
            unanimous_approval, reviews = asyncio.run(coordinator.request_critical_review(
                original_code=original_function_code_for_diff,
                new_code_string=new_code_string,
                code_diff=code_diff,
                original_requirements=change_description,
                related_tests=None
            ))
        except Exception as e_review:
            err_msg = f"Error during critical review process for '{function_name}': {e_review}"
            logger.error(err_msg, exc_info=True)
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="Critical review process error")
            return err_msg

        if not unanimous_approval:
            review_summaries = []
            for i, r in enumerate(reviews):
                review_summaries.append(f"Critic {i+1} ({r.get('status')}): {r.get('comments', 'No comments.')}")
            err_msg = (f"Change to function '{function_name}' in module '{module_path}' rejected by critical review. "
                       f"No modifications will be applied. Reviews: {' | '.join(review_summaries)}")
            logger.warning(err_msg)
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.CRITIC_REVIEW_REJECTED, reason=err_msg, step_desc="Critical review rejected")
            return err_msg
        else:
            logger.info(f"Change to function '{function_name}' in '{module_path}' approved by critical review. Proceeding with file modification.")
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.CRITIC_REVIEW_APPROVED, step_desc="Critical review approved")
        # --- End Critical Review Step ---

        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc="Validating file path for modification")
        if not os.path.exists(file_path):
            err_msg = f"Error: Module file not found at '{file_path}' derived from module path '{module_path}'. (Post-review check)"
            logger.error(err_msg)
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason=err_msg, step_desc="File path validation failed")
            return err_msg

        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc="Creating backup of original file")
        backup_file_path = file_path + ".bak"
        shutil.copy2(file_path, backup_file_path)
        logger.info(f"Backup of '{file_path}' created at '{backup_file_path}'.")

        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc="Parsing original source file via AST")
        with open(file_path, 'r', encoding='utf-8') as f:
            original_source = f.read()

        original_ast = ast.parse(original_source, filename=file_path)

        try:
            new_function_ast_module = ast.parse(new_code_string)
        except SyntaxError as e_new_code_syn:
            err_msg = f"SyntaxError in new_code_string: {e_new_code_syn.msg} (line {e_new_code_syn.lineno}, offset {e_new_code_syn.offset})" # pragma: no cover
            logger.error(f"{err_msg} - New code: \n{new_code_string}") # pragma: no cover
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="Syntax error in new code") # pragma: no cover
            return err_msg # pragma: no cover
        
        if not new_function_ast_module.body or not isinstance(new_function_ast_module.body[0], ast.FunctionDef):
            err_msg = "Error: new_code_string does not seem to be a valid single function definition."
            logger.error(err_msg)
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="Invalid new code structure")
            return err_msg
        
        new_function_node = new_function_ast_module.body[0]

        if new_function_node.name != function_name:
            logger.warning(
                f"The new code defines a function named '{new_function_node.name}', "
                f"but the target function name is '{function_name}'. "
                f"The function name in the new code will be used for replacement, effectively renaming the function."
            )
            # If strict name matching is required, this should return an error.
            # For now, we proceed with replacing the old named function with the new one (which might have a new name).
            # The function being replaced is identified by 'function_name'.

        function_found_and_replaced = False
        new_body = []
        for node in original_ast.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                new_body.append(new_function_node)
                function_found_and_replaced = True
                logger.info(f"Function '{function_name}' found in '{file_path}' and marked for replacement with '{new_function_node.name}'.")
            else:
                new_body.append(node)
        
        if not function_found_and_replaced:
            err_msg = f"Error: Function '{function_name}' not found in module '{module_path}' (file '{file_path}')."
            logger.error(err_msg)
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason=err_msg, step_desc="Target function not found in AST")
            return err_msg

        original_ast.body = new_body
        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc="Unparsing modified AST")
        try:
            new_source_code = ast.unparse(original_ast)
        except AttributeError:
            err_msg = "Error: ast.unparse is not available. Python 3.9+ is required." # pragma: no cover
            logger.error(err_msg) # pragma: no cover
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason=err_msg, step_desc="AST unparse failed (version issue)") # pragma: no cover
            return err_msg # pragma: no cover
        except Exception as e_unparse:
            err_msg = f"Error unparsing modified AST for '{file_path}': {e_unparse}" # pragma: no cover
            logger.error(err_msg, exc_info=True) # pragma: no cover
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason=err_msg, step_desc="AST unparse failed") # pragma: no cover
            return err_msg # pragma: no cover

        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc="Writing modified code to file")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_source_code)
        
        # The final COMPLETED_SUCCESSFULLY for the parent task will be set by the caller (ActionExecutor) after post-modification tests.
        # This function signals its own success by returning a non-error message.
        # We can update the step description for clarity for the parent task.
        success_step_desc = f"Code for '{function_name}' in '{module_path}' successfully written to disk."
        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc=success_step_desc) # Indicate this part is done.
                                                                                                                         # ActionExecutor will then move to POST_MOD_TESTING or COMPLETED.

        logger.info(f"Successfully modified function '{function_name}' (replaced with '{new_function_node.name}') in module '{module_path}' (file '{file_path}').")
        return f"Function '{function_name}' (replaced with '{new_function_node.name}') in module '{module_path}' updated successfully."

    except FileNotFoundError:
        err_msg = f"Error: File not found for module path '{module_path}' (expected at '{file_path}')." # pragma: no cover
        logger.error(err_msg) # pragma: no cover
        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="File not found for modification") # pragma: no cover
        return err_msg # pragma: no cover
    except SyntaxError as e_syn:
        err_msg = f"SyntaxError during AST parsing. File: '{e_syn.filename}', Line: {e_syn.lineno}, Offset: {e_syn.offset}, Message: {e_syn.msg}" # pragma: no cover
        logger.error(err_msg, exc_info=True) # pragma: no cover
        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason=err_msg, step_desc="AST parsing error of original file") # pragma: no cover
        return f"SyntaxError: {err_msg}" # pragma: no cover
    except Exception as e:
        err_msg = f"An unexpected error occurred in edit_function_source_code: {type(e).__name__}: {e}" # pragma: no cover
        logger.error(err_msg, exc_info=True) # pragma: no cover
        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=err_msg, step_desc="Unexpected error during edit") # pragma: no cover
        return err_msg # pragma: no cover

def get_backup_function_source_code(module_path: str, function_name: str) -> Optional[str]:
    """
    Retrieves the source code of a specified function from its backup (.bak) file.

    Args:
        module_path: The Python module path (e.g., "ai_assistant.custom_tools.my_extra_tools").
        function_name: The name of the function to retrieve from the backup.

    Returns:
        The source code of the function as a string if found in the backup, otherwise None.
    """
    file_path_py = os.path.join(*module_path.split('.')) + ".py"
    backup_file_path = file_path_py + ".bak"

    if not os.path.exists(backup_file_path):
        print(f"Warning: Backup file '{backup_file_path}' not found for module '{module_path}'.")
        return None

    try:
        with open(backup_file_path, 'r', encoding='utf-8') as f:
            backup_source = f.read()

        backup_ast = ast.parse(backup_source, filename=backup_file_path)

        for node in backup_ast.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                try:
                    return ast.unparse(node)
                except AttributeError: # pragma: no cover
                    print("Error: ast.unparse not available. Python 3.9+ required.")
                    return None

        print(f"Warning: Function '{function_name}' not found in backup file '{backup_file_path}'.")
        return None

    except FileNotFoundError: # pragma: no cover
        print(f"Error: Backup file '{backup_file_path}' disappeared unexpectedly.")
        return None
    except SyntaxError as e_syn: # pragma: no cover
        print(f"SyntaxError parsing backup file '{backup_file_path}': {e_syn}")
        return None
    except Exception as e: # pragma: no cover
        print(f"Unexpected error retrieving function from backup '{backup_file_path}': {e}")
        return None

if __name__ == '__main__': # pragma: no cover
    # --- Setup for Tests ---
    TEST_DIR = "test_ai_assistant_ws_self_modification" # Unique name for this test suite
    CORE_DIR_SM = os.path.join(TEST_DIR, "ai_assistant", "core")
    CUSTOM_TOOLS_DIR_SM = os.path.join(TEST_DIR, "ai_assistant", "custom_tools")

    # Clean up old test directory if it exists
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    
    os.makedirs(CORE_DIR_SM, exist_ok=True)
    os.makedirs(CUSTOM_TOOLS_DIR_SM, exist_ok=True)

    # Create dummy __init__.py files to make them packages
    with open(os.path.join(TEST_DIR, "ai_assistant", "__init__.py"), "w") as f: f.write("")
    with open(os.path.join(CORE_DIR_SM, "__init__.py"), "w") as f: f.write("")
    with open(os.path.join(CUSTOM_TOOLS_DIR_SM, "__init__.py"), "w") as f: f.write("")


    core_test_module_filename = "test_module_core.py"
    core_test_module_path_str = os.path.join(CORE_DIR_SM, core_test_module_filename)
    original_core_function_one_code = (
        "def core_function_one():\n"
        "    print('This is core_function_one original')\n"
        "    return 1"
    )
    with open(core_test_module_path_str, "w", encoding="utf-8") as f:
        f.write("import os\n\n")
        f.write(original_core_function_one_code + "\n\n")
        f.write("def core_function_two(x, y):\n")
        f.write("    print('This is core_function_two original')\n")
        f.write("    return x + y\n")

    custom_test_module_filename = "test_module_custom.py"
    custom_test_module_path_str = os.path.join(CUSTOM_TOOLS_DIR_SM, custom_test_module_filename)
    with open(custom_test_module_path_str, "w", encoding="utf-8") as f:
        f.write("def custom_tool_alpha(message: str):\n")
        f.write("    '''This is the original custom_tool_alpha docstring.'''\n")
        f.write("    print(f'Original custom_tool_alpha: {message}')\n")
        f.write("    return f'Received: {message}'\n")

    # Add TEST_DIR to sys.path so that modules within it can be imported
    original_sys_path = list(sys.path)
    sys.path.insert(0, os.path.abspath(TEST_DIR)) # Add test_dir to path

    # --- Test Cases for edit_function_source_code ---
    print("\n--- Testing edit_function_source_code ---")
    module_path_core = f"ai_assistant.core.{core_test_module_filename[:-3]}"

    print("\nTest E1: Successful edit of 'core_function_one'")
    new_code_core_one = (
        "def core_function_one():\n"
        "    print('This is core_function_one MODIFIED by main test')\n" # Ensure it's different from other tests
        "    # Added a comment for main test\n"
        "    return 200"
    )

    # Mock the critical review process for the __main__ tests
    mock_reviews_main_test = [{"status": "approved", "comments": "Mock auto-approved for __main__ test"}] * 2

    # Patch target needs to be where CriticalReviewCoordinator is *looked up*
    # which is in the self_modification module's namespace.
    with patch('ai_assistant.core.self_modification.CriticalReviewCoordinator.request_critical_review',
               new_callable=AsyncMock,
               return_value=(True, mock_reviews_main_test)) as mock_review_call_main:
        result_e1 = edit_function_source_code(
            module_path=module_path_core,
            function_name="core_function_one",
            new_code_string=new_code_core_one,
            project_root_path=os.path.abspath(TEST_DIR),
            change_description="Main test E1: Modifying core_function_one",
            task_manager=None, # No TaskManager for this simple __main__ test
            parent_task_id=None
        )
        print(f"Test E1 Result: {result_e1}")
        assert "success" in result_e1.lower()
        # mock_review_call_main.assert_called_once() # Verify review was called

    with open(core_test_module_path_str, "r", encoding="utf-8") as f:
        content = f.read()
        assert "MODIFIED" in content and "core_function_two" in content

    # --- Test Cases for get_backup_function_source_code ---
    print("\n--- Testing get_backup_function_source_code ---")
    
    print("\nTest GBC.1: Retrieve existing function from backup (core_function_one)")
    retrieved_backup_code = get_backup_function_source_code(module_path_core, "core_function_one")

    if retrieved_backup_code:
        print(f"Retrieved backup code for core_function_one:\n{retrieved_backup_code}")
        # ast.unparse normalizes code, so direct comparison with original_core_function_one_code might be fragile.
        # We'll check for key components.
        parsed_retrieved = ast.parse(retrieved_backup_code)
        parsed_original = ast.parse(original_core_function_one_code)

        # Basic check: compare number of lines or specific content
        assert "print('This is core_function_one original')" in retrieved_backup_code
        assert "return 1" in retrieved_backup_code
        assert "MODIFIED" not in retrieved_backup_code # Make sure it's not the modified version
        print("Test GBC.1: Backup code content verified.")
    else:
        print("Failed to retrieve backup code for core_function_one.")
        assert False, "get_backup_function_source_code failed when it should have succeeded."

    print("\nTest GBC.2: Retrieve non-existent function from backup")
    retrieved_non_existent_code = get_backup_function_source_code(module_path_core, "non_existent_function_in_backup")
    assert retrieved_non_existent_code is None
    if retrieved_non_existent_code is None:
        print("Correctly failed to retrieve non-existent function from backup.")
    else: # pragma: no cover
        print("Incorrectly retrieved code for a non-existent function from backup.")
        assert False

    print("\nTest GBC.3: Attempt retrieve from module with no backup")
    module_without_backup_name = "module_no_backup_yet"
    module_path_no_backup = f"ai_assistant.core.{module_without_backup_name}"
    no_backup_py_file = os.path.join(CORE_DIR_SM, f"{module_without_backup_name}.py")
    with open(no_backup_py_file, "w") as f:
        f.write("def some_func_no_backup(): pass\n")
    
    retrieved_no_backup = get_backup_function_source_code(module_path_no_backup, "some_func_no_backup")
    assert retrieved_no_backup is None
    if retrieved_no_backup is None:
        print("Correctly failed to retrieve from module with no backup file.")
    else: # pragma: no cover
        print("Incorrectly retrieved code when no backup file should exist.")
        assert False

    # Restore original_core_function_one_code to the .py file (not from backup, but from variable)
    # This ensures that if other tests run using this file, they get the original.
    # The .bak file for core_function_one still holds the original_core_function_one_code.
    print("\nRestoring core_function_one in .py file to its known original state (from backup content) for subsequent tests...")

    if retrieved_backup_code:
        # Patch review again for this internal call to edit_function_source_code
        with patch('ai_assistant.core.self_modification.CriticalReviewCoordinator.request_critical_review',
                   new_callable=AsyncMock,
                   return_value=(True, mock_reviews_main_test)) as mock_restore_review_call:
            restore_result = edit_function_source_code(
                module_path_core,
                "core_function_one",
                retrieved_backup_code,
                project_root_path=os.path.abspath(TEST_DIR),
                change_description="Main test: Restoring core_function_one from backup content"
            )
            print(f"Restoration of core_function_one in .py: {restore_result}")
            assert "success" in restore_result.lower()
            # mock_restore_review_call.assert_called_once()
    else: # pragma: no cover
        print("Could not restore core_function_one as backup code was not retrieved.")


    print("\n--- Finished get_backup_function_source_code Testing ---")

    # Cleanup: Remove the TEST_DIR
    # print(f"\nNOTE: Test directory '{TEST_DIR}' and its contents will be removed if tests pass.")
    # shutil.rmtree(TEST_DIR) # Comment out for inspection
    # print(f"Cleaned up test directory: {TEST_DIR}")
    print(f"\nNOTE: Test directory '{TEST_DIR}' was NOT automatically cleaned up. Please remove it manually if desired.")
    sys.path = original_sys_path # Restore original sys.path

    print("\n--- End of self_modification.py Testing ---")
