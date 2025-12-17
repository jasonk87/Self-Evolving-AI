# Code for the AI assistant's self-modification capabilities.
import importlib
import inspect
from typing import Optional
import ast
import os
import shutil
import logging
import sys
import subprocess
import tempfile
from .diff_utils import generate_diff
from .critical_reviewer import CriticalReviewCoordinator
from .reviewer import ReviewerAgent # Needed to instantiate default reviewers
from .refinement import RefinementAgent # Added import for refinement
import asyncio # For running the async review process
from unittest.mock import patch, AsyncMock # For __main__ block mocking
from typing import Optional, Dict, Any # Ensure Optional, Dict, Any are imported for type hints
from .task_manager import TaskManager, ActiveTaskStatus, ActiveTaskType


# Configure logger for this module
logger = logging.getLogger(__name__)
if not logger.handlers: # Avoid adding multiple handlers if script is reloaded/run multiple times
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


def _run_pylint_check(code_str: str) -> Optional[str]:
    """Runs pylint on the code string and returns error message if 'undefined variable' is found."""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
            tmp.write(code_str)
            tmp_path = tmp.name
    except Exception as e:
        logger.warning(f"Failed to create temp file for Pylint: {e}")
        return None

    try:
        # Check for E0602 (undefined variable) and E0401 (import error)
        # We assume pylint is installed and in path.
        # Modified to use sys.executable for robustness per user feedback
        result = subprocess.run(
            [sys.executable, '-m', 'pylint', '--disable=all', '--enable=E0602,E0401', '--score=n', '--output-format=text', tmp_path],
            capture_output=True, text=True, check=False
        )
        # Pylint returns non-zero on issues.
        if result.returncode != 0:
            lines = result.stdout.splitlines()
            # Filter for specific errors we care about
            errors = [line for line in lines if "E0602" in line or "E0401" in line]
            if errors:
                return "Static Analysis Failed (Pylint):\n" + "\n".join(errors)
        return None
    except FileNotFoundError:
        logger.warning("Pylint not found. Skipping static analysis.")
        return None
    except Exception as e:
        logger.warning(f"Pylint check failed to run: {e}")
        return None
    finally:
        try:
            if 'tmp_path' in locals():
                os.remove(tmp_path)
        except:
            pass




def get_function_source_code(module_path: str, function_name: str) -> Optional[str]:
    """
    Retrieves the source code of a specified function within a given module.

    Args:
        module_path: The Python module path (e.g., "ai_assistant.communication.cli").
        function_name: The name of the function.

    Returns:
        The source code of the function as a string, or None if an error occurs.
    """
    # Dynamic retrieval attempt
    try:
        module = importlib.import_module(module_path)
        function_obj = getattr(module, function_name)
        source_code = inspect.getsource(function_obj)
        return source_code
    except (ModuleNotFoundError, AttributeError, TypeError, OSError, Exception) as e:
        logger.warning(f"Dynamic retrieval failed for '{module_path}.{function_name}': {e}. Attempting static retrieval.")
        pass # Proceed to static fallback

    # Static fallback
    file_path = _resolve_file_path_robust(module_path, function_name)
    if not file_path:
        logger.error(f"Could not resolve file path for '{module_path}.{function_name}' statically.")
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                # Prioritize get_source_segment to preserve formatting/comments
                if hasattr(ast, 'get_source_segment'):
                    segment = ast.get_source_segment(source, node)
                    if segment:
                        return segment
                # Fallback to unparse (reformats code)
                if hasattr(ast, 'unparse'):
                    return ast.unparse(node)
                
                logger.error(f"AST found function '{function_name}' but could not extract source (no get_source_segment/unparse).")
                return None
                
        logger.error(f"Function '{function_name}' not found in parsed file '{file_path}'.")
        return None
    except Exception as e:
        logger.error(f"Static retrieval failed for '{module_path}.{function_name}' in '{file_path}': {e}")
        return None

def resolve_function_file_path(module_path: str, function_name: str) -> Optional[str]:
    """
    Resolves the absolute file path where a function is defined using introspection.
    """
    try:
        module = importlib.import_module(module_path)
        function_obj = getattr(module, function_name)
        file_path = inspect.getfile(function_obj)
        return os.path.abspath(file_path)
    except Exception as e:
        # Don't log error yet, allow caller to handle or fallback
        return None

def _resolve_file_path_robust(module_path: str, function_name: str, project_root_path: Optional[str] = None) -> Optional[str]:
    """
    Attempts to resolve the file path for a function using introspection, then falling back to
    static path construction if introspection fails.
    """
    # 1. Try introspection first
    file_path = resolve_function_file_path(module_path, function_name)
    if file_path:
        return file_path

    # 2. Fallback to naive construction
    if not project_root_path:
        # Try to guess project root from CWD or typical structure if not provided?
        # Ideally, we should have it. For now, use CWD as fallback or relative to this file.
        # But this function is imported, so let's try CWD.
        project_root_path = os.getcwd()
    
    relative_module_path = os.path.join(*module_path.split('.'))
    naive_path = os.path.join(project_root_path, relative_module_path)
    
    potential_paths = []
    
    # Check if 'naive_path' is a directory (package)
    if os.path.isdir(naive_path):
        # A. Function might be exposed in __init__.py of the package
        potential_paths.append(os.path.join(naive_path, "__init__.py"))
        
        # B. Function might be in a file inside the directory matching its name? Unlikely but possible.
        # C. Scan children py files for definition (Parsing)
        for root, _, files in os.walk(naive_path):
            for file in files:
                if file.endswith(".py"):
                    child_path = os.path.join(root, file)
                    potential_paths.append(child_path)
    else:
        # Is a file
        potential_paths.append(naive_path + ".py")
        
    # Scan potential paths for the function definition
    for p in potential_paths:
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                # Fast check using string search before parsing
                if f"def {function_name}" in content or f"async def {function_name}" in content:
                    # Verify with AST
                    try:
                        tree = ast.parse(content)
                        for node in tree.body:
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                                return os.path.abspath(p)
                    except Exception:
                        pass
            except Exception:
                continue
                
    return None

def resolve_module_file_path(module_path: str) -> Optional[str]:
    """
    Resolves the absolute file path of a module.
    """
    try:
        module = importlib.import_module(module_path)
        return os.path.abspath(inspect.getfile(module))
    except Exception as e:
        logger.error(f"Error resolving file path for module '{module_path}': {e}")
        return None

def _update_parent_task(tm: Optional[TaskManager], p_task_id: Optional[str], status: ActiveTaskStatus, reason: Optional[str] = None, step: Optional[str] = None, step_desc: Optional[str] = None):
    actual_step_desc = step_desc if step_desc else step
    if tm and p_task_id:
        tm.update_task_status(p_task_id, status, reason=reason, step_desc=actual_step_desc)

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

        # Use robust introspection to find the file path
        file_path = _resolve_file_path_robust(module_path, function_name, project_root_path)
        
        if not file_path:
             logger.error(f"Could not resolve file path for '{module_path}.{function_name}'.")
             _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason="File path resolution failed", step="Path resolution")
             return "Error: Could not resolve file path."
        
        # Ensure file_path is within project_root (basic check)
        # if not file_path.startswith(project_root_path):
        #    logger.warning(f"Resolved file path '{file_path}' is outside project root '{project_root_path}'. This might be intended for venv libraries.")
 
        # Read the original file content immediately to have it available for static analysis reconstruction
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                original_source = f.read()
            original_ast = ast.parse(original_source, filename=file_path)
        except Exception as e:
            err_msg = f"Error reading or parsing original file '{file_path}': {e}"
            logger.error(err_msg)
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step="Reading original file")
            return err_msg

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

        # --- Critical Review Loop with Refinement ---
        critic1 = ReviewerAgent()
        critic2 = ReviewerAgent()
        coordinator = CriticalReviewCoordinator(critic1, critic2)
        refinement_agent = RefinementAgent()

        max_refinement_attempts = 3
        current_new_code = new_code_string
        current_code_diff = code_diff

        for attempt in range(max_refinement_attempts + 1):
            logger.info(f"Requesting critical review for '{function_name}' in '{module_path}' (Attempt {attempt+1}/{max_refinement_attempts+1})...")
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc=f"Performing critical review (Attempt {attempt+1})")

            # --- STATIC ANALYSIS CHECK ---
            pylint_error = None
            try:
                # Reconstruct the full file with the new function to check for valid imports/syntax
                temp_new_func_ast = ast.parse(current_new_code).body[0]
                # We parse existing original_source again to avoid mutating the master 'original_ast' permanently until final success
                temp_full_ast = ast.parse(original_source)
                
                # Replace function in temp AST
                new_body = []
                found_in_temp = False
                for node in temp_full_ast.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                        new_body.append(temp_new_func_ast)
                        found_in_temp = True
                    else:
                        new_body.append(node)
                
                if found_in_temp:
                    temp_full_ast.body = new_body
                    try:
                        temp_full_source = ast.unparse(temp_full_ast)
                        pylint_error = _run_pylint_check(temp_full_source)
                    except Exception as e_unparse:
                         logger.warning(f"AST unparse failed during static analysis prep: {e_unparse}")
            except Exception as e_static:
                logger.warning(f"Static analysis preparation failed: {e_static}")
                pylint_error = f"Static Analysis Preparation Failed (Syntax/AST Error): {e_static}"

            if pylint_error:
                logger.info(f"Static analysis failed: {pylint_error}")
                unanimous_approval = False
                reviews = [{
                    "status": "requires_changes",
                    "comments": f"Automatic Static Analysis Failed:\n{pylint_error}",
                    "suggestions": "Please ensure all necessary imports are added (e.g., 'from typing import Any')."
                }]
                # Skip human/LLM review, go straight to refinement
            else:
                try:
                    unanimous_approval, reviews = await coordinator.request_critical_review(
                        original_code=original_function_code_for_diff,
                        new_code_string=current_new_code,
                        code_diff=current_code_diff,
                        original_requirements=change_description,
                        related_tests=None
                    )
                except Exception as e_review:
                    err_msg = f"Error during critical review process: {e_review}"
                    logger.error(err_msg, exc_info=True)
                    _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="Critical review process error")
                    return err_msg

            if unanimous_approval:
                logger.info(f"Change to function '{function_name}' approved by critical review.")
                _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.CRITIC_REVIEW_APPROVED, step_desc="Critical review approved")
                break # Proceed to apply changes

            # If not approved, check if we can refine
            if attempt < max_refinement_attempts:
                logger.info(f"Change to '{function_name}' NOT approved. Attempting refinement ({attempt+1})...")

                # Aggregate feedback
                aggregated_comments = []
                aggregated_suggestions = []
                for i, r in enumerate(reviews):
                    status = r.get('status', 'unknown')
                    comments = r.get('comments', 'No comments')
                    suggestions = r.get('suggestions', '')
                    aggregated_comments.append(f"Critic {i+1} ({status}): {comments}")
                    if suggestions:
                        aggregated_suggestions.append(f"Critic {i+1} Suggestions: {suggestions}")

                combined_feedback = {
                    "status": "requires_changes", # Force status for refiner
                    "comments": "\n\n".join(aggregated_comments),
                    "suggestions": "\n\n".join(aggregated_suggestions)
                }

                _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.REFINING_PLAN, step_desc=f"Refining code based on feedback (Attempt {attempt+1})")

                refined_code = await refinement_agent.refine_code(
                    original_code=current_new_code,
                    requirements=change_description,
                    review_feedback=combined_feedback
                )

                if refined_code and refined_code.strip():
                     current_new_code = refined_code
                     # Regenerate diff for next review
                     current_code_diff = generate_diff(original_function_code_for_diff, current_new_code, file_name=f"{module_path}/{function_name}")
                else:
                    logger.warning("Refinement failed to produce code. Stopping retry loop.")
                    break
            else:
                 logger.warning("Max refinement attempts reached. Change rejected.")

        if not unanimous_approval:
             review_summaries = []
             for i, r in enumerate(reviews):
                review_summaries.append(f"Critic {i+1} ({r.get('status')}): {r.get('comments', 'No comments.')}")
             err_msg = (f"Change to function '{function_name}' rejected after {max_refinement_attempts+1} attempts. "
                        f"Reviews: {' | '.join(review_summaries)}")
             _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.CRITIC_REVIEW_REJECTED, reason=err_msg, step_desc="Critical review rejected final")
             return err_msg

        # --- End Critical Review Step ---

        # Proceed with applying changes using current_new_code (which might be refined)
        new_code_string = current_new_code

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
        # We already read original_source earlier, but let's refresh original_ast just to be clean
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

        # Enhanced Validation & Parsing: Allow Imports + Function
        new_function_node = None
        new_imports = []

        for node in new_function_ast_module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if new_function_node is not None:
                     err_msg = "Error: new_code_string contains multiple function definitions. Only one is allowed."
                     logger.error(err_msg)
                     _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="Multiple functions in new code")
                     return err_msg
                new_function_node = node
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                new_imports.append(node)
            else:
                # We could reject other types (ClassDef, Assign, etc) or just ignore them.
                # For safety, let's reject to prevent accidental global state changes or side effects.
                err_msg = f"Error: new_code_string contains unsupported top-level statement type: {type(node).__name__}. Only imports and a single function definition are allowed."
                logger.error(err_msg)
                _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="Invalid statement in new code")
                return err_msg

        if not new_function_node:
            err_msg = "Error: new_code_string does not contain a valid function definition."
            logger.error(err_msg)
            _update_parent_task(task_manager, parent_task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="No function found in new code")
            return err_msg
        
        if new_function_node.name != function_name:
            logger.warning(
                f"The new code defines a function named '{new_function_node.name}', "
                f"but the target function name is '{function_name}'. "
                f"The function name in the new code will be used for replacement, effectively renaming the function."
            )

        # 1. Merge Imports (Prepend to original AST)
        # Deduplicate imports: Check if new_imports already exist in original code
        if new_imports:
             existing_imports_sigs = set()
             for node in original_ast.body:
                 if isinstance(node, (ast.Import, ast.ImportFrom)):
                     try:
                         # Normalize using unparse to match exactly
                         existing_imports_sigs.add(ast.unparse(node).strip())
                     except Exception:
                         pass
             
             unique_new_imports = []
             for imp in new_imports:
                 try:
                     imp_sig = ast.unparse(imp).strip()
                     if imp_sig not in existing_imports_sigs:
                         unique_new_imports.append(imp)
                         # Add to set to prevent duplicates within new_imports list itself
                         existing_imports_sigs.add(imp_sig)
                 except Exception:
                     # If unparse fails, we default to adding it (safer vs losing it) or skip?
                     # Safer to add it to avoid MissingImport error, user can clean up rare dupes.
                     unique_new_imports.append(imp)

             if unique_new_imports:
                 original_ast.body = unique_new_imports + original_ast.body
                 logger.info(f"Added {len(unique_new_imports)} unique import statements to '{file_path}'.")
             else:
                 logger.info("All new imports were duplicates of existing imports. Skipped addition.")

        # 2. Replace Function
        function_found_and_replaced = False
        new_body = []
        for node in original_ast.body:
            # Skip the newly added imports when looking for replacement target (they are at start of list now)
            # Actually, we are iterating `original_ast.body` which we just modified. 
            # We should probably iterate a copy or be careful.
            # But simpler: We rebuild `new_body`.
            
            # Use `is` check to avoid matching the nodes we just added (though improbable to match name/type exactly identically by object identity)
            # A safer way is to iterate the *original* content's body nodes. 
            # But since we just prepended, the function replacing logic below is fine as long as we don't accidentally replace the import?
            # Imports are not FunctionDefs, so safe.
            
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                # Check if this is the ONE we want to replace (in case of overloads? Python doesn't support overloads in AST usually w/o decorators)
                
                # IMPORTANT: If we have multiple functions with same name (unlikely in valid module), this replaces all? 
                # Standard behavior: replace first or all? Let's replace all to be safe or just first? 
                # Usually modules have unique top level names.
                
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

def _find_function_in_package_dir(package_dir: str, function_name: str) -> Optional[str]:
    """
    Searches for a function definition in all .py files within a directory.
    Returns the absolute path to the file containing the function, or None if not found.
    """
    if not os.path.isdir(package_dir):
        return None

    for root, _, files in os.walk(package_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        if f"def {function_name}" in f.read():
                             # Verify with AST to be sure (avoid comments/strings)
                             try:
                                 with open(file_path, 'r', encoding='utf-8') as f_ast:
                                     source = f_ast.read()
                                     tree = ast.parse(source)
                                     for node in tree.body:
                                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                                             return os.path.abspath(file_path)
                             except Exception:
                                 continue
                except Exception:
                    continue
    return None

def get_backup_function_source_code(module_path: str, function_name: str) -> Optional[str]:
    """
    Retrieves the source code of a specified function from its backup (.bak) file.

    Args:
        module_path: The Python module path (e.g., "ai_assistant.custom_tools.my_extra_tools").
        function_name: The name of the function to retrieve from the backup.

    Returns:
        The source code of the function as a string if found in the backup, otherwise None.
    """
    # Use robust path resolution
    file_path = resolve_module_file_path(module_path)
    if not file_path:
        # Fallback to naive construction
        file_path = os.path.join(*module_path.split('.')) + ".py"
    
    # Check if we are dealing with a package/init and the function might be in a submodule
    if file_path.endswith("__init__.py"):
         package_dir = os.path.dirname(file_path)
         # Verify if function is actually in __init__.py or a submodule
         # We can reuse the logic from edit_function_source_code or simplistically search
         found_file = _find_function_in_package_dir(package_dir, function_name)
         if found_file:
             file_path = found_file
             # Re-verify if this is the backup logic we want. 
             # If edit_function_source_code modified 'weather_tool.py', it created 'weather_tool.py.bak'.
             # So we want 'weather_tool.py' + '.bak'

    backup_file_path = file_path + ".bak"

    if not os.path.exists(backup_file_path):
        print(f"Warning: Backup file '{backup_file_path}' not found for module '{module_path}'.")
        # Fallback: check if the file_path itself is the backup (some implementations swap)
        # But here we stick to .bak extension convention.
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

    # --- Critical Review Loop with Refinement ---
    critic1 = ReviewerAgent()
    critic2 = ReviewerAgent()
    coordinator = CriticalReviewCoordinator(critic1, critic2)
    refinement_agent = RefinementAgent()

    max_refinement_attempts = 3
    current_new_content = new_content
    current_file_diff = file_diff

    for attempt in range(max_refinement_attempts + 1):
        logger.info(f"Requesting critical review for project file '{absolute_file_path}' (Attempt {attempt+1})...")
        _update_p_task(ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc=f"Performing critical review for file (Attempt {attempt+1})")

        try:
            unanimous_approval, reviews = await coordinator.request_critical_review(
                original_code=original_content,  # Use original_content here
                new_code_string=current_new_content,    # Use new_content here
                code_diff=current_file_diff,
                original_requirements=change_description,
                related_tests=None # Or determine if tests are relevant for arbitrary files
            )
        except Exception as e_review: # pragma: no cover
            err_msg = f"Error during critical review process for project file '{absolute_file_path}': {e_review}"
            logger.error(err_msg, exc_info=True)
            _update_p_task(ActiveTaskStatus.FAILED_PRE_REVIEW, reason=err_msg, step_desc="Critical review process error")
            return err_msg

        if unanimous_approval:
             logger.info(f"Change to project file '{absolute_file_path}' approved by critical review.")
             _update_p_task(ActiveTaskStatus.CRITIC_REVIEW_APPROVED, step_desc=f"Review approved for file: {os.path.basename(absolute_file_path)}")
             break

        if attempt < max_refinement_attempts:
             logger.info(f"Change to '{absolute_file_path}' NOT approved. Attempting refinement ({attempt+1})...")

             aggregated_comments = []
             aggregated_suggestions = []
             for i, r in enumerate(reviews):
                status = r.get('status', 'unknown')
                comments = r.get('comments', 'No comments')
                suggestions = r.get('suggestions', '')
                aggregated_comments.append(f"Critic {i+1} ({status}): {comments}")
                if suggestions:
                    aggregated_suggestions.append(f"Critic {i+1} Suggestions: {suggestions}")

             combined_feedback = {
                "status": "requires_changes",
                "comments": "\n\n".join(aggregated_comments),
                "suggestions": "\n\n".join(aggregated_suggestions)
             }

             _update_p_task(ActiveTaskStatus.REFINING_PLAN, step_desc=f"Refining file content (Attempt {attempt+1})")

             refined_content = await refinement_agent.refine_code(
                original_code=current_new_content,
                requirements=change_description,
                review_feedback=combined_feedback
             )

             if refined_content and refined_content.strip():
                 current_new_content = refined_content
                 current_file_diff = generate_diff(original_content, current_new_content, file_name=os.path.basename(absolute_file_path))
             else:
                 logger.warning("Refinement failed to produce content. Stopping retry loop.")
                 break
        else:
             logger.warning("Max refinement attempts reached for file. Change rejected.")

    if not unanimous_approval:
        review_summaries = []
        for i, r in enumerate(reviews):
            review_summaries.append(f"Critic {i+1} ({r.get('status')}): {r.get('comments', 'No comments.')}")
        err_msg = (f"Change to project file '{absolute_file_path}' rejected after attempts. "
                   f"No modifications will be applied. Reviews: {' | '.join(review_summaries)}")
        logger.warning(err_msg)
        _update_p_task(ActiveTaskStatus.CRITIC_REVIEW_REJECTED, reason=err_msg, step_desc="Critical review rejected final")
        return err_msg

    # --- End Critical Review Step ---

    # Proceed with current_new_content
    new_content = current_new_content

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

async def edit_class_method(
    module_path: str,
    class_name: str,
    method_name: str,
    new_code: str,
    project_root_path: str,
    change_description: str,
    task_manager: Optional[TaskManager] = None,
    parent_task_id: Optional[str] = None
) -> str:
    """
    Edits the source code of a specified method within a class in a given module file using AST.
    """
    def _update_p_task(status: ActiveTaskStatus, reason: Optional[str] = None, step: Optional[str] = None, step_desc: Optional[str] = None):
        actual_step = step_desc if step_desc else step
        if task_manager and parent_task_id:
            task_manager.update_task_status(parent_task_id, status, reason=reason, step_desc=actual_step)

    _update_p_task(ActiveTaskStatus.PLANNING, step=f"Preparing to edit method {class_name}.{method_name}")

    if not os.path.isabs(project_root_path):
        project_root_path = os.path.abspath(project_root_path)

    file_path = resolve_module_file_path(module_path)
    if not file_path:
        relative_module_file_path = os.path.join(*module_path.split('.')) + ".py"
        file_path = os.path.join(project_root_path, relative_module_file_path)

    if not os.path.exists(file_path):
        return f"Error: File not found: {file_path}"

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_source = f.read()
    except Exception as e:
        return f"Error reading file: {e}"

    try:
        new_method_ast = ast.parse(new_code).body[0]
        if not isinstance(new_method_ast, (ast.FunctionDef, ast.AsyncFunctionDef)):
             return "Error: new_code must be a function/method definition."
    except Exception as e:
        return f"Error parsing new code: {e}"

    try:
        original_ast = ast.parse(original_source)
    except Exception as e:
        return f"Error parsing original file: {e}"

    class_found = False
    method_found = False

    for node in original_ast.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            class_found = True
            new_class_body = []
            for class_node in node.body:
                if isinstance(class_node, (ast.FunctionDef, ast.AsyncFunctionDef)) and class_node.name == method_name:
                    method_found = True
                    # Replace with new method AST
                    new_class_body.append(new_method_ast)
                else:
                    new_class_body.append(class_node)
            node.body = new_class_body
            break

    if not class_found:
        return f"Error: Class '{class_name}' not found in module '{module_path}'."
    if not method_found:
        return f"Error: Method '{method_name}' not found in class '{class_name}'."

    try:
        new_file_source = ast.unparse(original_ast)
    except Exception as e:
        return f"Error unparsing AST: {e}"

    file_diff = generate_diff(original_source, new_file_source, file_name=relative_module_file_path)

    critic1 = ReviewerAgent()
    critic2 = ReviewerAgent()
    coordinator = CriticalReviewCoordinator(critic1, critic2)
    refinement_agent = RefinementAgent()

    _update_p_task(ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc=f"Reviewing changes for {class_name}.{method_name}")

    max_refinement_attempts = 3
    current_new_source = new_file_source
    current_file_diff = file_diff
    approved = False

    for attempt in range(max_refinement_attempts + 1):
        try:
            approved, reviews = await coordinator.request_critical_review(
                original_code=original_source,
                new_code_string=current_new_source,
                code_diff=current_file_diff,
                original_requirements=change_description
            )
        except Exception as e:
             return f"Error during review: {e}"

        if approved:
            break

        if attempt < max_refinement_attempts:
            # Refine
            aggregated_comments = "\n".join([f"{r['status']}: {r['comments']} {r.get('suggestions','')}" for r in reviews])
            combined_feedback = {"status": "requires_changes", "comments": aggregated_comments, "suggestions": ""}

            # Note: refining the WHOLE file might be too much context for LLM if large.
            # Ideally we'd refine just the method, but here we have the whole file string.
            # RefinementAgent expects 'original_code'.
            refined_file_source = await refinement_agent.refine_code(
                original_code=current_new_source,
                requirements=change_description,
                review_feedback=combined_feedback
            )
            if refined_file_source:
                current_new_source = refined_file_source
                current_file_diff = generate_diff(original_source, current_new_source, file_name=relative_module_file_path)
            else:
                break
        else:
            break

    if not approved:
        return "Change rejected by critical review."

    shutil.copy2(file_path, file_path + ".bak")
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(current_new_source)

    _update_p_task(ActiveTaskStatus.COMPLETED_SUCCESSFULLY, step_desc="Method updated.")
    return f"Method '{class_name}.{method_name}' updated successfully."

async def upsert_import(
    module_path: str,
    import_statement: str,
    project_root_path: str,
    change_description: str,
    task_manager: Optional[TaskManager] = None,
    parent_task_id: Optional[str] = None
) -> str:
    """
    Ensures a specific import statement exists in the module.
    """
    if not os.path.isabs(project_root_path):
        project_root_path = os.path.abspath(project_root_path)

    file_path = resolve_module_file_path(module_path)
    if not file_path:
        relative_module_file_path = os.path.join(*module_path.split('.')) + ".py"
        file_path = os.path.join(project_root_path, relative_module_file_path)

    if not os.path.exists(file_path):
        return f"Error: File not found: {file_path}"

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_source = f.read()
    except Exception as e:
        return f"Error reading file: {e}"

    try:
        new_import_node = ast.parse(import_statement).body[0]
        if not isinstance(new_import_node, (ast.Import, ast.ImportFrom)):
            return "Error: import_statement must be an import statement."
    except Exception as e:
        return f"Error parsing import statement: {e}"

    try:
        original_ast = ast.parse(original_source)
    except Exception as e:
        return f"Error parsing original file: {e}"

    # Check existence
    exists = False
    for node in original_ast.body:
        if type(node) == type(new_import_node):
            if isinstance(node, ast.Import):
                if node.names[0].name == new_import_node.names[0].name: # Simplified check
                    exists = True
                    break
            elif isinstance(node, ast.ImportFrom):
                if node.module == new_import_node.module and node.names[0].name == new_import_node.names[0].name:
                    exists = True
                    break

    if exists:
        return "Import already exists."

    # Insert
    # Find insertion index
    insert_idx = 0
    for i, node in enumerate(original_ast.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            insert_idx = i + 1
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # Docstring, skip
            if insert_idx == 0: insert_idx = i + 1
        else:
            # Stop at first code
            # But wait, we might have multiple imports, we want to append to them.
            # If we encountered imports, insert_idx is after them.
            # If we hit code, we break.
            break

    original_ast.body.insert(insert_idx, new_import_node)

    try:
        new_file_source = ast.unparse(original_ast)
    except Exception as e:
        return f"Error unparsing AST: {e}"

    shutil.copy2(file_path, file_path + ".bak")
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_file_source)

    return f"Import '{import_statement}' upserted successfully."

async def insert_code_block(
    module_path: str, # Interpreted as file path if not a module path
    anchor_code: str,
    new_code: str,
    position: str = 'after',
    project_root_path: str = None,
    change_description: str = "",
    task_manager: Optional[TaskManager] = None,
    parent_task_id: Optional[str] = None
) -> str:
    """
    Inserts a code block before or after an anchor string in a file.
    """
    # Determine file path. If module_path looks like a path, use it. Else convert.
    if project_root_path:
        if not os.path.isabs(project_root_path):
             project_root_path = os.path.abspath(project_root_path)
        # Check if it has an extension or path separators
        if '.' in os.path.basename(module_path) or '/' in module_path or '\\' in module_path:
             file_path = os.path.join(project_root_path, module_path)
        else:
             resolved_path = resolve_module_file_path(module_path)
             if resolved_path:
                 file_path = resolved_path
             else:
                 file_path = os.path.join(project_root_path, os.path.join(*module_path.split('.')) + ".py")
    else:
        file_path = os.path.abspath(module_path) # Assume absolute or relative to cwd

    if not os.path.exists(file_path):
        return f"Error: File not found: {file_path}"

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return f"Error reading file: {e}"

    if anchor_code not in content:
        return f"Error: Anchor code not found in file."

    if position == 'after':
        new_content = content.replace(anchor_code, anchor_code + "\n" + new_code)
    elif position == 'before':
        new_content = content.replace(anchor_code, new_code + "\n" + anchor_code)
    else:
        return "Error: Position must be 'before' or 'after'."

    shutil.copy2(file_path, file_path + ".bak")
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    return "Code block inserted successfully."

async def surgical_edit_function(
    module_path: str,
    function_name: str,
    target_node_pattern: str,
    replacement_code: str,
    project_root_path: str,
    change_description: str,
    task_manager: Optional[TaskManager] = None,
    parent_task_id: Optional[str] = None
) -> str:
    """
    Performs a surgical edit on a function by locating a specific statement
    (matching target_node_pattern) and replacing it with replacement_code.
    """
    def _update_p_task(status: ActiveTaskStatus, reason: Optional[str] = None, step: Optional[str] = None, step_desc: Optional[str] = None):
        actual_step = step_desc if step_desc else step
        if task_manager and parent_task_id:
            task_manager.update_task_status(parent_task_id, status, reason=reason, step_desc=actual_step)

    _update_p_task(ActiveTaskStatus.PLANNING, step=f"Preparing surgical edit for {function_name}")

    if not os.path.isabs(project_root_path):
        project_root_path = os.path.abspath(project_root_path)

    relative_module_file_path = os.path.join(*module_path.split('.')) + ".py"
    file_path = os.path.join(project_root_path, relative_module_file_path)

    if not os.path.exists(file_path):
        return f"Error: File not found: {file_path}"

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            original_source = f.read()
    except Exception as e:
        return f"Error reading file: {e}"

    # Verify target pattern validity
    try:
        normalized_target_str = ast.unparse(ast.parse(target_node_pattern)).strip()
    except Exception as e:
        return f"Error parsing target_node_pattern: {e}"

    # Verify replacement code validity
    try:
        replacement_ast_body = ast.parse(replacement_code).body
    except Exception as e:
        return f"Error parsing replacement_code: {e}"

    try:
        original_ast = ast.parse(original_source)
    except Exception as e:
        return f"Error parsing original file: {e}"
    
    class SurgicalTransformer(ast.NodeTransformer):
        def __init__(self):
            self.found = False
            self.replaced_count = 0

        def visit_FunctionDef(self, node):
            if node.name == function_name:
                new_body = []
                for child in node.body:
                    try:
                        child_source = ast.unparse(child).strip()
                        if child_source == normalized_target_str:
                            self.found = True
                            self.replaced_count += 1
                            new_body.extend(replacement_ast_body)
                        else:
                            new_body.append(self.visit(child))
                    except Exception as e:
                        new_body.append(child)
                node.body = new_body
            return node
        
        def visit_AsyncFunctionDef(self, node):
            return self.visit_FunctionDef(node)

    transformer = SurgicalTransformer()
    new_tree = transformer.visit(original_ast)

    if not transformer.found:
        return f"Error: Target pattern '{target_node_pattern}' not found in function '{function_name}'."

    try:
        new_source_code = ast.unparse(new_tree)
    except Exception as e:
        return f"Error unparsing AST: {e}"

    code_diff = generate_diff(original_source, new_source_code, file_name=relative_module_file_path)

    critic1 = ReviewerAgent()
    critic2 = ReviewerAgent()
    coordinator = CriticalReviewCoordinator(critic1, critic2)
    
    _update_p_task(ActiveTaskStatus.AWAITING_CRITIC_REVIEW, step_desc="Reviewing surgical changes")
    
    approved, reviews = await coordinator.request_critical_review(
        original_code=original_source,
        new_code_string=new_source_code,
        code_diff=code_diff,
        original_requirements=change_description
    )

    if not approved:
         return "Surgical edit rejected by critical review."

    shutil.copy2(file_path, file_path + ".bak")
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_source_code)
    
    _update_p_task(ActiveTaskStatus.COMPLETED_SUCCESSFULLY, step_desc="Surgical edit applied.")
    return f"Surgical edit to '{function_name}' applied successfully."

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
                # Update expectation to include "attempts" which is part of the new reject message format
                assert "rejected after" in result_p3_main.lower() or "rejected by critical review" in result_p3_main.lower()
                with open(test_proj_file_path_main, 'r') as f: assert f.read() == "Updated project content."
                # It should be called multiple times now due to loop (max_refinement_attempts + 1)
                assert mock_review_proj_main.call_count >= 1

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
