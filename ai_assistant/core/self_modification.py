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
from typing import Optional, Dict, Any # Ensure Optional, Dict, Any are imported for type hints
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

async def edit_function_source_code(module_path: str, function_name: str, new_code_string: str, project_root_path: str, change_description: str, task_manager: Optional[TaskManager] = None, parent_task_id: Optional[str] = None) -> str:
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
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Code identical, no changes applied.", step_desc="Diff generation found no changes")
            return f"No changes detected for function '{function_name}' in module '{module_path}'. Code is identical."

        # --- Critical Review Step ---
        critic1 = ReviewerAgent()
        critic2 = ReviewerAgent()
        coordinator = CriticalReviewCoordinator(critic1, critic2)

        logger.info(f"Requesting critical review for changes to '{function_name}' in '{module_path}'...")
        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc=f"Performing critical review for {function_name}")
        try:
            unanimous_approval, reviews = await coordinator.request_critical_review(
                original_code=original_function_code_for_diff,
                new_code_string=new_code_string,
                code_diff=code_diff,
                original_requirements=change_description,
                related_tests=None
            )
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
        
        if not new_function_ast_module.body:
            err_msg = "Error: new_code_string is empty or contains no parsable Python statements (e.g., only comments)."
            logger.error(err_msg)
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="New code is empty or invalid")
            return err_msg

        if not isinstance(new_function_ast_module.body[0], ast.FunctionDef):
            err_msg = "Error: new_code_string does not seem to be a valid single function definition (first statement is not FunctionDef)."
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
        
        success_step_desc = f"Code for '{function_name}' in '{module_path}' successfully written to disk."
        _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc=success_step_desc)

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

async def edit_project_file(
    absolute_file_path: str,
    new_content: str,
    change_description: str,
    task_manager: Optional[TaskManager] = None,
    parent_task_id: Optional[str] = None
) -> str:
    """
    Edits (or creates) an arbitrary project file after a critical review process.
    Handles backup of existing files before modification.

    Args:
        absolute_file_path: The absolute path to the file to be modified/created.
        new_content: The full new content for the file.
        change_description: A description of the changes being made, for review context.
        task_manager: Optional TaskManager instance for status updates.
        parent_task_id: Optional ID of the parent task to update with sub-statuses.

    Returns:
        A success or error/rejection message string.
    """
    # Helper for updating parent task status (local to this function)
    def _update_p_task(status: ActiveTaskStatus, reason: Optional[str] = None, step: Optional[str] = None):
        if task_manager and parent_task_id:
            task_manager.update_task_status(parent_task_id, status, reason=reason, step_desc=step)

    logger.info(f"Initiating edit for project file: {absolute_file_path}")
    _update_p_task(ActiveTaskStatus.PLANNING, step="Preparing for project file edit/creation")

    original_content = ""
    file_exists = os.path.exists(absolute_file_path)

    if file_exists:
        if not os.path.isfile(absolute_file_path):
            err_msg = f"Error: Path '{absolute_file_path}' exists but is not a file."
            logger.error(err_msg)
            _update_p_task(ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step="Path validation")
            return err_msg
        try:
            with open(absolute_file_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
            logger.info(f"Read original content from {absolute_file_path}")
        except Exception as e: # pragma: no cover
            err_msg = f"Error reading original file {absolute_file_path}: {e}"
            logger.error(err_msg, exc_info=True)
            _update_p_task(ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step="Reading original file")
            return err_msg
    else:
        logger.info(f"File '{absolute_file_path}' does not exist. Will be created if approved.")

    if original_content == new_content and file_exists:
        msg = f"Proposed content for '{absolute_file_path}' is identical to current. No changes made."
        logger.info(msg)
        _update_p_task(ActiveTaskStatus.COMPLETED_SUCCESSFULLY, step_desc="Content identical, no file change needed.")
        return msg

    _update_p_task(ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step="Generating diff for review")
    file_diff = generate_diff(original_content, new_content, file_name=os.path.basename(absolute_file_path))

    # --- Critical Review Step ---
    critic1 = ReviewerAgent()
    critic2 = ReviewerAgent()
    coordinator = CriticalReviewCoordinator(critic1, critic2)

    logger.info(f"Requesting critical review for changes to project file '{absolute_file_path}'...")
    _update_p_task(ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc=f"Performing critical review for file: {os.path.basename(absolute_file_path)}")
    try:
        unanimous_approval, reviews = await coordinator.request_critical_review(
            original_code=original_content,  # Use original_content here
            new_code_string=new_content,    # Use new_content here
            code_diff=file_diff,
            original_requirements=change_description,
            related_tests=None # Or determine if tests are relevant for arbitrary files
        )
    except Exception as e_review: # pragma: no cover
        err_msg = f"Error during critical review process for project file '{absolute_file_path}': {e_review}"
        logger.error(err_msg, exc_info=True)
        _update_p_task(ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="Critical review process error")
        return err_msg

    if not unanimous_approval:
        review_summaries = []
        for i, r in enumerate(reviews):
            review_summaries.append(f"Critic {i+1} ({r.get('status')}): {r.get('comments', 'No comments.')}")
        err_msg = (f"Change to project file '{absolute_file_path}' rejected by critical review. "
                   f"No modifications will be applied. Reviews: {' | '.join(review_summaries)}")
        logger.warning(err_msg)
        _update_p_task(ActiveTaskStatus.CRITIC_REVIEW_REJECTED, reason=err_msg, step_desc="Critical review rejected")
        return err_msg
    else:
        logger.info(f"Change to project file '{absolute_file_path}' approved by critical review.")
        _update_p_task(ActiveTaskStatus.CRITIC_REVIEW_APPROVED, step_desc=f"Review approved for file: {os.path.basename(absolute_file_path)}")
    # --- End Critical Review Step ---

    parent_dir = os.path.dirname(absolute_file_path)
    if parent_dir and not os.path.exists(parent_dir): # pragma: no branch
        try:
            os.makedirs(parent_dir, exist_ok=True)
            logger.info(f"Created parent directory: {parent_dir}")
        except Exception as e_mkdir: # pragma: no cover
            err_msg = f"Error creating parent directory {parent_dir} for file '{absolute_file_path}': {e_mkdir}"
            logger.error(err_msg, exc_info=True)
            _update_p_task(ActiveTaskStatus.FAILED_DURING_APPLY, reason=err_msg, step="Directory creation failed")
            return err_msg

    if file_exists:
        _update_p_task(ActiveTaskStatus.APPLYING_CHANGES, step_desc=f"Backing up existing file: {os.path.basename(absolute_file_path)}")
        backup_file_path = absolute_file_path + ".bak"
        try:
            shutil.copy2(absolute_file_path, backup_file_path)
            logger.info(f"Backup of '{absolute_file_path}' created at '{backup_file_path}'.")
        except Exception as e_backup: # pragma: no cover
            err_msg = f"Error creating backup for '{absolute_file_path}': {e_backup}"
            logger.error(err_msg, exc_info=True)
            _update_p_task(ActiveTaskStatus.FAILED_DURING_APPLY, reason=err_msg, step="Backup creation failed")
            return err_msg

    _update_p_task(ActiveTaskStatus.APPLYING_CHANGES, step_desc=f"Writing content to file: {os.path.basename(absolute_file_path)}")
    try:
        with open(absolute_file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        logger.info(f"Successfully wrote content to project file: '{absolute_file_path}'.")
        _update_p_task(ActiveTaskStatus.APPLYING_CHANGES, step_desc=f"Content written to {os.path.basename(absolute_file_path)} successfully.")
        return f"Project file '{absolute_file_path}' updated successfully after review."
    except Exception as e_write: # pragma: no cover
        err_msg = f"Error writing to project file '{absolute_file_path}': {e_write}"
        logger.error(err_msg, exc_info=True)
        _update_p_task(ActiveTaskStatus.FAILED_DURING_APPLY, reason=err_msg, step="File write operation failed")
        return err_msg

if __name__ == '__main__': # pragma: no cover
    import tempfile
    TEST_DIR = "test_ai_assistant_ws_self_modification"
    CORE_DIR_SM = os.path.join(TEST_DIR, "ai_assistant", "core")
    CUSTOM_TOOLS_DIR_SM = os.path.join(TEST_DIR, "ai_assistant", "custom_tools")

    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    
    os.makedirs(CORE_DIR_SM, exist_ok=True)
    os.makedirs(CUSTOM_TOOLS_DIR_SM, exist_ok=True)

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

    original_sys_path = list(sys.path)
    sys.path.insert(0, os.path.abspath(TEST_DIR))

    async def run_all_tests():
        print("\n--- Testing edit_function_source_code ---")
        module_path_core_main = f"ai_assistant.core.{core_test_module_filename[:-3]}"

        print("\nTest E1: Successful edit of 'core_function_one'")
        new_code_core_one_main = (
            "def core_function_one():\n"
            "    print('This is core_function_one MODIFIED by main test')\n"
            "    # Added a comment for main test\n"
            "    return 200"
        )
        mock_reviews_main_test_main = [{"status": "approved", "comments": "Mock auto-approved for __main__ test"}] * 2

        with patch('ai_assistant.core.self_modification.CriticalReviewCoordinator.request_critical_review',
                   new_callable=AsyncMock,
                   return_value=(True, mock_reviews_main_test_main)) as mock_review_call_main_again:
            result_e1_main = await edit_function_source_code(
                module_path=module_path_core_main,
                function_name="core_function_one",
                new_code_string=new_code_core_one_main,
                project_root_path=os.path.abspath(TEST_DIR),
                change_description="Main test E1: Modifying core_function_one",
                task_manager=None,
                parent_task_id=None
            )
            print(f"Test E1 Result: {result_e1_main}")
            assert "success" in result_e1_main.lower()

        with open(core_test_module_path_str, "r", encoding="utf-8") as f:
            content = f.read()
            assert "MODIFIED" in content and "core_function_two" in content

        print("\n--- Testing get_backup_function_source_code ---")
        print("\nTest GBC.1: Retrieve existing function from backup (core_function_one)")
        retrieved_backup_code = get_backup_function_source_code(module_path_core_main, "core_function_one")

        if retrieved_backup_code:
            print(f"Retrieved backup code for core_function_one:\n{retrieved_backup_code}")
            assert "print('This is core_function_one original')" in retrieved_backup_code
            assert "return 1" in retrieved_backup_code
            assert "MODIFIED" not in retrieved_backup_code
            print("Test GBC.1: Backup code content verified.")
        else: # pragma: no cover
            print("Failed to retrieve backup code for core_function_one.")
            assert False, "get_backup_function_source_code failed when it should have succeeded."

        print("\nTest GBC.2: Retrieve non-existent function from backup")
        retrieved_non_existent_code = get_backup_function_source_code(module_path_core_main, "non_existent_function_in_backup")
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

        print("\nRestoring core_function_one in .py file to its known original state (from backup content) for subsequent tests...")
        if retrieved_backup_code:
            with patch('ai_assistant.core.self_modification.CriticalReviewCoordinator.request_critical_review',
                       new_callable=AsyncMock,
                       return_value=(True, mock_reviews_main_test_main)) as mock_restore_review_call:
                restore_result = await edit_function_source_code(
                    module_path_core_main,
                    "core_function_one",
                    retrieved_backup_code,
                    project_root_path=os.path.abspath(TEST_DIR),
                    change_description="Main test: Restoring core_function_one from backup content"
                )
                print(f"Restoration of core_function_one in .py: {restore_result}")
                assert "success" in restore_result.lower()
        else: # pragma: no cover
            print("Could not restore core_function_one as backup code was not retrieved.")

        print("\n--- Finished get_backup_function_source_code Testing ---")

        print("\n--- Testing edit_project_file (async in __main__) ---")
        with tempfile.TemporaryDirectory() as temp_project_root_dir_main:
            test_proj_file_path_main = os.path.join(temp_project_root_dir_main, "my_test_project_file.txt")
            mock_reviews_project_file_approve_main = [{"status": "approved", "comments": "Project file changes look good."}] * 2

            with patch('ai_assistant.core.self_modification.CriticalReviewCoordinator.request_critical_review', new_callable=AsyncMock) as mock_review_proj_main:
                # Test 1: Create new file (approved)
                print("\nTest EPF.1: Create new file (approved)")
                mock_review_proj_main.return_value = (True, mock_reviews_project_file_approve_main)
                result_p1_main = await edit_project_file(test_proj_file_path_main, "New project content.", "Creating project file for test.", None, None)
                print(f"Test EPF.1 Result: {result_p1_main}")
                assert "success" in result_p1_main.lower()
                with open(test_proj_file_path_main, 'r') as f: assert f.read() == "New project content."
                mock_review_proj_main.assert_called_once()

                # Test 2: Edit existing file (approved)
                print("\nTest EPF.2: Edit existing file (approved)")
                mock_review_proj_main.reset_mock()
                mock_review_proj_main.return_value = (True, mock_reviews_project_file_approve_main)
                result_p2_main = await edit_project_file(test_proj_file_path_main, "Updated project content.", "Updating project file for test.", None, None)
                print(f"Test EPF.2 Result: {result_p2_main}")
                assert "success" in result_p2_main.lower()
                with open(test_proj_file_path_main, 'r') as f: assert f.read() == "Updated project content."
                assert os.path.exists(test_proj_file_path_main + ".bak")
                mock_review_proj_main.assert_called_once()

                # Test 3: Edit existing file (rejected)
                print("\nTest EPF.3: Edit existing file (rejected)")
                mock_review_proj_main.reset_mock()
                mock_reviews_project_file_reject_main = [{"status": "rejected", "comments": "Project file changes rejected by mock."}] * 2
                mock_review_proj_main.return_value = (False, mock_reviews_project_file_reject_main)
                result_p3_main = await edit_project_file(test_proj_file_path_main, "This content should be rejected.", "Trying a rejected update for test.", None, None)
                print(f"Test EPF.3 Result: {result_p3_main}")
                assert "rejected by critical review" in result_p3_main.lower()
                with open(test_proj_file_path_main, 'r') as f: assert f.read() == "Updated project content."
                mock_review_proj_main.assert_called_once()

                # Test 4: Content identical
                print("\nTest EPF.4: Content identical")
                mock_review_proj_main.reset_mock()
                result_p4_main = await edit_project_file(test_proj_file_path_main, "Updated project content.", "No real change intended for test.", None, None)
                print(f"Test EPF.4 Result: {result_p4_main}")
                assert "identical to current" in result_p4_main.lower()
                mock_review_proj_main.assert_not_called()

                # Test 5: Path is a directory
                print("\nTest EPF.5: Path is a directory")
                result_p5_main = await edit_project_file(temp_project_root_dir_main, "Content for a dir?", "Attempting to write to a dir.", None, None)
                print(f"Test EPF.5 Result: {result_p5_main}")
                assert "is not a file" in result_p5_main.lower()

        print("\n--- Finished edit_project_file Testing (async in __main__) ---")
        print(f"\nNOTE: Test directory '{TEST_DIR}' was NOT automatically cleaned up. Please remove it manually if desired.")
        sys.path = original_sys_path
        print("\n--- End of self_modification.py __main__ tests ---")

    asyncio.run(run_all_tests())
