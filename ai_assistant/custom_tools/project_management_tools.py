# ai_assistant/custom_tools/project_management_tools.py
import json
import subprocess
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
import asyncio # Added for __main__
import shutil # Added for __main__
from unittest.mock import patch, MagicMock, AsyncMock # Added for __main__
import tempfile # Added for __main__

# Updated imports for ProjectManifest and related dataclasses
from ai_assistant.project_management.manifest_schema import ProjectManifest, DevelopmentTask, BuildConfig, TestConfig, Dependency
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async 
from ai_assistant.config import get_model_for_task
from ai_assistant.custom_tools.file_system_tools import (
    create_project_directory, 
    write_text_to_file, 
    sanitize_project_name, 
    BASE_PROJECTS_DIR,
    read_text_from_file
)
import logging

logger = logging.getLogger(__name__)

PROJECT_PLANNING_PROMPT_TEMPLATE = """
You are an AI assistant helping to plan a new software project.
Project Description:
{project_description}

Based on this description, break the project down into a list of essential code files.
For each file, provide:
- "filename" (e.g., "main.py", "utils/helper.js")
- "description" (a concise, one-sentence explanation of the file's purpose)
- "key_components" (a list of strings, each describing a key function, class, or module within that file)
- "dependencies" (a list of strings, each naming other planned filenames this file will depend on, or an empty list if none)

Respond with a JSON object containing a single key "project_plan", which is a list of these dictionaries.
Example:
{{
  "project_plan": [
    {{
      "filename": "app.py",
      "description": "Main application script that orchestrates the web server and routes.",
      "key_components": ["Flask app initialization", "Main route for /", "API endpoint /data"],
      "dependencies": ["data_handler.py", "utils.py"]
    }},
    {{
      "filename": "data_handler.py",
      "description": "Module for loading and processing data.",
      "key_components": ["load_data_from_source", "process_data_for_api"],
      "dependencies": []
    }},
    {{
      "filename": "utils.py",
      "description": "Utility functions for general use across the project.",
      "key_components": ["format_response", "error_handling_wrapper"],
      "dependencies": []
    }}
  ]
}}
If the project description is too vague or simple for a multi-file breakdown (e.g., "a script to print hello world"), plan for a single file, typically named after the project or 'main.py', with empty lists for key_components and dependencies.
Ensure filenames are conventional (e.g., use .py for Python, .js for JavaScript if language is implied or stated). If no language is specified, assume Python.
Do not include any other explanatory text or markdown formatting like ```json ... ``` around the JSON object.
"""

async def initiate_ai_project(project_name: str, project_description: str) -> str:
    """
    Initializes a new AI-managed software project. 
    
    This involves:
    1. Sanitizing the project name and creating a project directory.
    2. Calling an LLM to generate a basic project plan (list of files and descriptions).
    3. Saving a manifest file (_ai_project_manifest.json) in the project directory.

    Args:
        project_name: The desired name for the project.
        project_description: A brief description of what the project is about.

    Returns:
        A string confirming successful initialization and summarizing the plan, 
        or an error message if any step fails.
    """
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."
    if not project_description or not isinstance(project_description, str) or not project_description.strip():
        return "Error: Project description must be a non-empty string."

    sanitized_name = sanitize_project_name(project_name)
    
    dir_creation_result = create_project_directory(project_name)

    if dir_creation_result.startswith("Error:"):
        return dir_creation_result

    try:
        project_dir_path = os.path.join(BASE_PROJECTS_DIR, sanitized_name)
        if not os.path.exists(project_dir_path):
             return f"Error: Project directory creation reported success, but directory '{project_dir_path}' not found."
    except Exception as e:
        return f"Error: Could not determine project directory path after creation. Detail: {e}"

    try:
        os.makedirs(os.path.join(project_dir_path, "src"), exist_ok=True)
        os.makedirs(os.path.join(project_dir_path, "tests"), exist_ok=True)
        readme_content = f"# {project_name}\n\n{project_description}"
        readme_path = os.path.join(project_dir_path, "README.md")
        readme_write_result = write_text_to_file(readme_path, readme_content)
        if readme_write_result.startswith("Error:"):
            print(f"Warning: Failed to write README.md for project '{project_name}'. {readme_write_result}")
    except Exception as e_dir:
        return f"Error: Failed to create basic directory structure (src, tests, README.md) in '{project_dir_path}'. Detail: {e_dir}"

    prompt = PROJECT_PLANNING_PROMPT_TEMPLATE.format(project_description=project_description)
    llm_model = get_model_for_task("planning")

    plan_response_str = await invoke_ollama_model_async(prompt, model_name=llm_model)
    development_tasks_list: List[DevelopmentTask] = []

    if not plan_response_str or not plan_response_str.strip():
        return "Error: Failed to get project plan from LLM."

    try:
        if plan_response_str.startswith("```json"):
            plan_response_str = plan_response_str.lstrip("```json").rstrip("```").strip()
        elif plan_response_str.startswith("```"):
            plan_response_str = plan_response_str.lstrip("```").rstrip("```").strip()
        
        parsed_json = json.loads(plan_response_str)

        if not isinstance(parsed_json, dict) or "project_plan" not in parsed_json or not isinstance(parsed_json.get("project_plan"), list):
            return "Error: LLM returned an invalid plan structure (missing 'project_plan' list)."

        raw_plan_list = parsed_json.get("project_plan", [])
        
        if not raw_plan_list:
            print(f"Warning: LLM returned an empty project plan for '{project_name}'. Proceeding with empty plan.")

        for i, file_dict in enumerate(raw_plan_list):
            if not isinstance(file_dict, dict):
                print(f"Warning: Invalid item in LLM project plan (not a dict): {file_dict}. Skipping this item.")
                continue

            filename = file_dict.get("filename")
            description = file_dict.get("description")
            key_components = file_dict.get("key_components")
            dependencies = file_dict.get("dependencies")

            if not (filename and isinstance(filename, str) and 
                    description and isinstance(description, str)):
                print(f"Warning: Invalid item in LLM project plan (missing/invalid filename or description): {file_dict}. Skipping this item.")
                continue

            if not isinstance(key_components, list):
                print(f"Warning: 'key_components' for file '{filename}' is not a list. Defaulting to empty list. Original: {key_components}")
                key_components = []
            else:
                key_components = [str(kc) for kc in key_components]

            if not isinstance(dependencies, list):
                print(f"Warning: 'dependencies' for file '{filename}' is not a list. Defaulting to empty list. Original: {dependencies}")
                dependencies = []
            else:
                dependencies = [str(dep) for dep in dependencies]
            
            task_id = f"TASK{i+1:03d}"
            dev_task = DevelopmentTask(
                task_id=task_id,
                task_type="CREATE_FILE",
                description=f"Define structure and generate code for {filename}",
                details={
                    "filename": filename,
                    "original_description": description,
                    "key_components": key_components,
                    "file_dependencies": dependencies 
                },
                status="planned"
            )
            development_tasks_list.append(dev_task)
            
    except json.JSONDecodeError:
        return f"Error: LLM returned invalid JSON for project plan. Response: {plan_response_str}"

    current_timestamp_iso = datetime.now(timezone.utc).isoformat()
    manifest_instance = ProjectManifest(
        project_name=project_name,
        sanitized_project_name=sanitized_name,
        project_directory=project_dir_path,
        project_description=project_description,
        creation_timestamp=current_timestamp_iso,
        last_modified_timestamp=current_timestamp_iso,
        manifest_version="1.1.0",
        version="0.1.0",
        project_type="python",
        development_tasks=development_tasks_list,
        entry_points={},
        dependencies=[],
        project_goals=[],
        build_config=BuildConfig(),
        test_config=TestConfig()
    )
    
    manifest_data_dict = manifest_instance.to_json_dict()

    manifest_filepath = os.path.join(project_dir_path, "_ai_project_manifest.json")
    write_result = write_text_to_file(manifest_filepath, json.dumps(manifest_data_dict, indent=4))

    if write_result.startswith("Error:"):
        return f"Error: Project directory and structure created at '{project_dir_path}', but failed to write manifest file. {write_result}"

    task_count = len(development_tasks_list)
    plan_summary_str = "an empty plan (no development tasks specified or all entries malformed)"
    if task_count > 0:
        plan_summary_str = f"{task_count} development task(s) (primarily file creation tasks)"
        
    return (f"Success: Project '{project_name}' initialized in '{project_dir_path}' with src, tests dirs and README.md. "
            f"Plan includes {plan_summary_str}. You can now ask to generate code for these tasks.")


CODE_GENERATION_PROMPT_TEMPLATE = """
You are an AI assistant helping to write code for a software project.
Overall Project Description:
{project_description}

Current File to Generate: {filename}
Purpose of this file (from project plan): {file_description}

Key Components for this file:
{key_components_str}

Dependencies for this file (other files in this project):
{dependencies_str}

Based on the project description, the file's purpose, its key components, and its dependencies, generate the complete code for the file '{filename}'.
- Ensure the code is functional and adheres to common best practices for the inferred language (assume Python if not specified).
- Only output the raw code for the file. Do not include any explanations, comments that are not part of the code itself, or markdown formatting like ```python ... ```.
- If the file description implies it needs to interact with other planned files, write the code assuming those other files will exist and provide the described functionality.
"""

async def generate_code_for_project_file(project_name: str, filename: str) -> str:
    """
    Generates code for a specific file within an AI-managed project,
    based on the project plan stored in the project's manifest.
    """
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."
    if not filename or not isinstance(filename, str) or not filename.strip():
        return "Error: Filename must be a non-empty string."

    sanitized_project_name = sanitize_project_name(project_name)
    project_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_project_name)
    manifest_filepath = os.path.join(project_dir, "_ai_project_manifest.json")

    manifest_json_str = read_text_from_file(manifest_filepath)
    if manifest_json_str.startswith("Error:"):
        return f"Error: Could not read project manifest for '{project_name}'. {manifest_json_str}"

    try:
        manifest_dict = json.loads(manifest_json_str)
        manifest_instance = ProjectManifest.from_dict(manifest_dict)
    except json.JSONDecodeError as e:
        return f"Error: Failed to parse project manifest for '{project_name}'. Invalid JSON. {e}"
    except Exception as e_manifest:
        return f"Error: Failed to load project manifest data into ProjectManifest object for '{project_name}'. Detail: {e_manifest}"

    file_task_entry: Optional[DevelopmentTask] = None
    
    for task in manifest_instance.development_tasks:
        if task.task_type == "CREATE_FILE" and task.details.get("filename") == filename:
            file_task_entry = task
            break
    
    if not file_task_entry:
        return f"Error: File task for '{filename}' not found in project development tasks for '{project_name}'."

    if file_task_entry.status == "generated":
        return f"Info: Code for '{filename}' in project '{project_name}' (Task ID: {file_task_entry.task_id}) has already been generated. Overwrite functionality is not yet supported."

    overall_project_desc = manifest_instance.project_description
    file_task_details = file_task_entry.details
    
    file_plan_description = file_task_details.get("original_description", "No specific file description provided in task details.")
    key_components_list = file_task_details.get("key_components", [])
    dependencies_list = file_task_details.get("file_dependencies", [])

    if not isinstance(key_components_list, list):
        print(f"Warning: 'key_components' for {filename} in task {file_task_entry.task_id} is not a list. Original: {key_components_list}. Using empty list.")
        key_components_list = []
    key_components_str = "\n".join([f"- {str(item)}" for item in key_components_list]) if key_components_list else "No specific key components listed."

    if not isinstance(dependencies_list, list):
        print(f"Warning: 'file_dependencies' for {filename} in task {file_task_entry.task_id} is not a list. Original: {dependencies_list}. Using empty list.")
        dependencies_list = []
    dependencies_str = ", ".join([str(item) for item in dependencies_list]) if dependencies_list else "None listed."

    prompt = CODE_GENERATION_PROMPT_TEMPLATE.format(
        project_description=overall_project_desc,
        filename=filename,
        file_description=file_plan_description,
        key_components_str=key_components_str,
        dependencies_str=dependencies_str
    )
    llm_model = get_model_for_task("code_generation")
    
    print(f"Info: Generating code for '{filename}' (Task ID: {file_task_entry.task_id}) in project '{project_name}' using model '{llm_model}'...")
    generated_code = await invoke_ollama_model_async(prompt, model_name=llm_model, temperature=0.5, max_tokens=4096)

    if not generated_code or not generated_code.strip():
        file_task_entry.status = "failed"
        file_task_entry.error_message = "LLM failed to generate code or returned empty code."
        file_task_entry.last_attempt_timestamp = datetime.now(timezone.utc).isoformat()
        manifest_instance.last_modified_timestamp = datetime.now(timezone.utc).isoformat()
        try:
            updated_manifest_dict_on_fail = manifest_instance.to_json_dict()
            write_text_to_file(manifest_filepath, json.dumps(updated_manifest_dict_on_fail, indent=4))
        except Exception as e_save_fail:
            print(f"Warning: Failed to update manifest after code generation failure for task {file_task_entry.task_id}. Error: {e_save_fail}")
        return f"Error: LLM failed to generate code for '{filename}'. Task '{file_task_entry.task_id}' marked as failed."
    
    if generated_code.startswith("```python"):
        generated_code = generated_code.lstrip("```python").rstrip("```").strip()
    elif generated_code.startswith("```"):
        generated_code = generated_code.lstrip("```").rstrip("```").strip()

    target_dir = project_dir
    if manifest_instance.build_config and \
       manifest_instance.build_config.source_directories and \
       isinstance(manifest_instance.build_config.source_directories, list) and \
       len(manifest_instance.build_config.source_directories) > 0:
        target_dir = os.path.join(project_dir, manifest_instance.build_config.source_directories[0])
    
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as e_dir:
        return f"Error: Could not create target directory '{target_dir}' for file '{filename}'. Detail: {e_dir}"
        
    code_filepath = os.path.join(target_dir, filename)
    write_result = write_text_to_file(code_filepath, generated_code)
    
    if write_result.startswith("Error:"):
        file_task_entry.status = "failed"
        file_task_entry.error_message = f"Failed to write generated code to file: {write_result}"
        file_task_entry.last_attempt_timestamp = datetime.now(timezone.utc).isoformat()
        manifest_instance.last_modified_timestamp = datetime.now(timezone.utc).isoformat()
        try:
            updated_manifest_dict_on_write_fail = manifest_instance.to_json_dict()
            write_text_to_file(manifest_filepath, json.dumps(updated_manifest_dict_on_write_fail, indent=4))
        except Exception as e_save_write_fail:
            print(f"Warning: Failed to update manifest after file write failure for task {file_task_entry.task_id}. Error: {e_save_write_fail}")
        return f"Error: Failed to write generated code for '{filename}' to file. {write_result}. Task '{file_task_entry.task_id}' marked as failed."

    file_task_entry.status = "generated"
    file_task_entry.last_attempt_timestamp = datetime.now(timezone.utc).isoformat()
    file_task_entry.error_message = None
    manifest_instance.last_modified_timestamp = datetime.now(timezone.utc).isoformat()
    
    try:
        updated_manifest_dict_success = manifest_instance.to_json_dict()
        manifest_write_result = write_text_to_file(manifest_filepath, json.dumps(updated_manifest_dict_success, indent=4))
        if manifest_write_result.startswith("Error:"):
            return (f"Warning: Code for '{filename}' (Task ID: {file_task_entry.task_id}) generated and saved to '{code_filepath}', "
                    f"but failed to update manifest. {manifest_write_result}")
    except Exception as e_final_save:
         return (f"Warning: Code for '{filename}' (Task ID: {file_task_entry.task_id}) generated and saved to '{code_filepath}', "
                 f"but encountered an error during final manifest serialization/save. Error: {e_final_save}")

    return (f"Success: Code for '{filename}' (Task ID: {file_task_entry.task_id}) generated and saved to '{code_filepath}' "
            f"in project '{project_name}'. Manifest updated.")


async def add_dependency_to_project(
    project_name: str, 
    dependency_name: str, 
    dependency_version: Optional[str] = None, 
    dependency_type: Optional[str] = None
) -> str:
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."
    if not dependency_name or not isinstance(dependency_name, str) or not dependency_name.strip():
        return "Error: Dependency name must be a non-empty string."

    sanitized_proj_name = sanitize_project_name(project_name)
    project_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_proj_name)
    manifest_filepath = os.path.join(project_dir, "_ai_project_manifest.json")

    manifest_json_str = read_text_from_file(manifest_filepath)
    if manifest_json_str.startswith("Error:"):
        return f"Error: Could not read project manifest for '{project_name}'. {manifest_json_str}"

    try:
        manifest_dict = json.loads(manifest_json_str)
        manifest = ProjectManifest.from_dict(manifest_dict)
    except json.JSONDecodeError as e:
        return f"Error: Failed to parse project manifest for '{project_name}'. Invalid JSON. {e}"
    except Exception as e_manifest:
        return f"Error: Failed to load project manifest data for '{project_name}'. Detail: {e_manifest}"

    new_dependency = Dependency(
        name=dependency_name,
        version=dependency_version,
        type=dependency_type
    )

    dependency_updated = False
    dependency_added = False
    existing_dep_index = -1

    for i, existing_dependency in enumerate(manifest.dependencies):
        if existing_dependency.name == new_dependency.name:
            existing_dep_index = i
            break
    
    if existing_dep_index != -1:
        current_dep = manifest.dependencies[existing_dep_index]
        if (new_dependency.version is not None and current_dep.version != new_dependency.version) or \
           (new_dependency.type is not None and current_dep.type != new_dependency.type) or \
           (new_dependency.version is None and current_dep.version is not None) or \
           (new_dependency.type is None and current_dep.type is not None):
            manifest.dependencies[existing_dep_index] = new_dependency
            dependency_updated = True
    else:
        manifest.dependencies.append(new_dependency)
        dependency_added = True

    if not dependency_added and not dependency_updated and existing_dep_index != -1:
        return f"Info: Dependency '{dependency_name}' already exists in project '{project_name}' with the same details. No changes made."

    manifest.last_modified_timestamp = datetime.now(timezone.utc).isoformat()

    try:
        updated_manifest_dict = manifest.to_json_dict()
        save_result = write_text_to_file(manifest_filepath, json.dumps(updated_manifest_dict, indent=4))
        if save_result.startswith("Error:"):
            return f"Error: Failed to save updated manifest for project '{project_name}'. {save_result}"
    except Exception as e_save:
        return f"Error: Unexpected error saving manifest for project '{project_name}'. {e_save}"

    action_taken = "added" if dependency_added else "updated"
    return f"Success: Dependency '{dependency_name}' (version: {dependency_version or 'any'}, type: {dependency_type or 'N/A'}) {action_taken} in project '{project_name}'. Manifest updated."

async def run_project_tests(project_name: str) -> str:
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."

    sanitized_proj_name = sanitize_project_name(project_name)
    project_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_proj_name)
    manifest_filepath = os.path.join(project_dir, "_ai_project_manifest.json")

    manifest_json_str = read_text_from_file(manifest_filepath)
    if manifest_json_str.startswith("Error:"):
        return f"Error: Could not read project manifest for '{project_name}'. {manifest_json_str}"

    try:
        manifest_dict = json.loads(manifest_json_str)
        manifest = ProjectManifest.from_dict(manifest_dict)
    except json.JSONDecodeError as e:
        return f"Error: Failed to parse project manifest for '{project_name}'. Invalid JSON. {e}"
    except Exception as e_manifest:
        return f"Error: Failed to load project manifest data for '{project_name}'. Detail: {e_manifest}"

    if not manifest.test_config or not manifest.test_config.test_command:
        return f"Info: No test command configured in the manifest for project '{project_name}'."

    test_command_str = manifest.test_config.test_command
    test_command_parts = test_command_str.split()

    try:
        logger.info(f"Running test command '{test_command_str}' for project '{project_name}' in directory '{project_dir}'...")
        process_result = subprocess.run(
            test_command_parts,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=300,
            check=False
        )

        output_summary = [
            f"Test Results for Project: {project_name}",
            f"Command: {test_command_str}",
            f"Return Code: {process_result.returncode}",
            "--- STDOUT ---",
            process_result.stdout.strip() if process_result.stdout else "(No standard output)",
            "--- STDERR ---",
            process_result.stderr.strip() if process_result.stderr else "(No standard error)"
        ]
        return "\n".join(output_summary)

    except FileNotFoundError:
        return f"Error: Test command '{test_command_parts[0]}' not found. Ensure it's installed and in PATH."
    except subprocess.TimeoutExpired:
        return f"Error: Test command '{test_command_str}' timed out after 300 seconds."
    except Exception as e:
        return f"Error: An unexpected error occurred while running tests for '{project_name}': {e}"

async def build_project(project_name: str) -> str:
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."

    sanitized_proj_name = sanitize_project_name(project_name)
    project_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_proj_name)
    manifest_filepath = os.path.join(project_dir, "_ai_project_manifest.json")

    manifest_json_str = read_text_from_file(manifest_filepath)
    if manifest_json_str.startswith("Error:"):
        return f"Error: Could not read project manifest for '{project_name}'. {manifest_json_str}"

    try:
        manifest_dict = json.loads(manifest_json_str)
        manifest = ProjectManifest.from_dict(manifest_dict)
    except json.JSONDecodeError as e:
        return f"Error: Failed to parse project manifest for '{project_name}'. Invalid JSON. {e}"
    except Exception as e_manifest:
        return f"Error: Failed to load project manifest data for '{project_name}'. Detail: {e_manifest}"

    if not manifest.build_config or not manifest.build_config.build_command:
        return f"Info: No build command configured in the manifest for project '{project_name}'."

    build_command_str = manifest.build_config.build_command
    build_command_parts = build_command_str.split()

    try:
        logger.info(f"Running build command '{build_command_str}' for project '{project_name}' in directory '{project_dir}'...")
        process_result = subprocess.run(
            build_command_parts,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=600,
            check=False
        )

        output_summary = [
            f"Build Results for Project: {project_name}",
            f"Command: {build_command_str}",
            f"Return Code: {process_result.returncode}",
            "--- STDOUT ---",
            process_result.stdout.strip() if process_result.stdout else "(No standard output)",
            "--- STDERR ---",
            process_result.stderr.strip() if process_result.stderr else "(No standard error)"
        ]
        return "\n".join(output_summary)

    except FileNotFoundError:
        return f"Error: Build command '{build_command_parts[0]}' not found. Ensure it's installed and in PATH."
    except subprocess.TimeoutExpired:
        return f"Error: Build command '{build_command_str}' timed out after 600 seconds."
    except Exception as e:
        return f"Error: An unexpected error occurred while building project '{project_name}': {e}"

from ..core.self_modification import edit_project_file
from ..core.task_manager import TaskManager

async def propose_project_file_update(
    absolute_target_filepath: str,
    new_file_content: str,
    change_description: str,
    task_manager: Optional[TaskManager] = None,
    parent_task_id: Optional[str] = None
) -> Dict[str, Any]:
    if not absolute_target_filepath or not change_description:
        return {"status": "error", "message": "Missing required arguments: absolute_target_filepath or change_description."}

    try:
        result_message = await edit_project_file(
            absolute_file_path=absolute_target_filepath,
            new_content=new_file_content,
            change_description=change_description,
            task_manager=task_manager,
            parent_task_id=parent_task_id
        )

        if "success" in result_message.lower():
            if "identical to current" in result_message.lower():
                    return {"status": "success_no_change", "message": result_message}
            return {"status": "success", "message": result_message}
        elif "rejected by critical review" in result_message.lower():
            return {"status": "rejected_by_review", "message": result_message}
        else:
            return {"status": "error", "message": result_message}

    except Exception as e: # pragma: no cover
        logger.error(f"Unexpected error in propose_project_file_update for '{absolute_target_filepath}': {str(e)}", exc_info=True)
        return {"status": "error", "message": f"An unexpected error occurred while proposing project file update for '{absolute_target_filepath}': {str(e)}"}


if __name__ == '__main__':
    _original_print = __builtins__.print # type: ignore
    _mock_captured_code_gen_prompt = None
    _original_invoke_ollama_model_async = None
    _original_create_project_directory = None
    _original_write_text_to_file = None
    _original_read_text_from_file = None
    _original_sanitize_project_name = None
    _created_dirs_for_test = []
    _written_files_for_test = {}
    _printed_warnings_for_test = []
    TEST_BASE_PROJECTS_DIR_MAIN = "temp_test_pm_projects"

    async def _mock_invoke_ollama_planner(prompt: str, model_name: str, **kwargs):
        if "Detailed Plan Test" in prompt:
            return json.dumps({
                "project_plan": [
                    {"filename": "app.py", "description": "Main app.", "key_components": ["comp1"], "dependencies": ["util.py"]},
                    {"filename": "util.py", "description": "Utilities.", "key_components": ["helper"], "dependencies": []}
                ]
            })
        elif "Missing Fields Test" in prompt:
            return json.dumps({"project_plan": [{"filename": "core.py", "description": "Core logic."}]})
        elif "Wrong Types Test" in prompt:
            return json.dumps({"project_plan": [{"filename": "service.py", "description": "Service layer.", "key_components": "not_a_list", "dependencies": 123}]})
        elif "Empty Plan Test" in prompt:
             return json.dumps({"project_plan": []})
        elif "Malformed JSON Test" in prompt:
            return "This is not valid JSON { "
        elif "Invalid Structure Test 1" in prompt:
            return json.dumps({"project_files": []})
        elif "Invalid Structure Test 2" in prompt:
            return json.dumps({"project_plan": {"filename": "test.py"}})
        elif "LLM No Response Test" in prompt:
            return None
        return json.dumps({"project_plan": [{"filename": "default.py", "description": "Default."}]})

    def _mock_create_project_directory(project_name: str):
        global _created_dirs_for_test, TEST_BASE_PROJECTS_DIR_MAIN
        s_name = sanitize_project_name(project_name)
        path = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, s_name)
        _created_dirs_for_test.append(path)
        os.makedirs(path, exist_ok=True)
        return f"Success: Project directory '{path}' created."

    def _mock_write_text_to_file(full_filepath: str, content: str):
        global _written_files_for_test
        _written_files_for_test[full_filepath] = content
        try:
            os.makedirs(os.path.dirname(full_filepath), exist_ok=True)
            with open(full_filepath, 'w') as f:
                f.write(content)
            return f"Success: Content written to '{full_filepath}'."
        except Exception as e:
            return f"Error: Mock fs write failed {e}"
            
    def _mock_read_text_from_file(full_filepath: str):
        if full_filepath in _written_files_for_test:
            return _written_files_for_test[full_filepath]
        if os.path.exists(full_filepath):
            try:
                with open(full_filepath, 'r') as f_real:
                    return f_real.read()
            except Exception as e_real:
                return f"Error: Mock fs read (real file) failed {e_real}"
        return f"Error: File not found '{full_filepath}'"

    _original_builtin_print = __builtins__.print # type: ignore
    def _captured_print(*args, **kwargs):
        global _printed_warnings_for_test
        _original_builtin_print(*args, **kwargs)
        _printed_warnings_for_test.append(" ".join(map(str, args)))

    async def run_tests():
        global invoke_ollama_model_async, create_project_directory, write_text_to_file, read_text_from_file, sanitize_project_name
        global _original_invoke_ollama_model_async, _original_create_project_directory, _original_write_text_to_file, _original_read_text_from_file, _original_sanitize_project_name
        global BASE_PROJECTS_DIR, _created_dirs_for_test, _written_files_for_test, _printed_warnings_for_test
        
        _original_invoke_ollama_model_async = invoke_ollama_model_async
        _original_create_project_directory = create_project_directory
        _original_write_text_to_file = write_text_to_file
        _original_read_text_from_file = read_text_from_file
        _original_sanitize_project_name = sanitize_project_name 
        
        original_base_dir_for_module = BASE_PROJECTS_DIR
        
        invoke_ollama_model_async = _mock_invoke_ollama_planner
        create_project_directory = _mock_create_project_directory
        write_text_to_file = _mock_write_text_to_file
        read_text_from_file = _mock_read_text_from_file

        __builtins__.print = _captured_print # type: ignore

        BASE_PROJECTS_DIR = TEST_BASE_PROJECTS_DIR_MAIN

        if os.path.exists(TEST_BASE_PROJECTS_DIR_MAIN):
            shutil.rmtree(TEST_BASE_PROJECTS_DIR_MAIN)
        os.makedirs(TEST_BASE_PROJECTS_DIR_MAIN, exist_ok=True)

        _original_builtin_print("--- Test 1: Successful Detailed Plan ---")
        _created_dirs_for_test.clear(); _written_files_for_test.clear(); _printed_warnings_for_test.clear()
        project_name_1 = "DetailedPlanProject"
        result_1 = await initiate_ai_project(project_name_1, "Detailed Plan Test")
        _original_builtin_print(f"Result 1: {result_1}") # Use original print for test output
        # ... (rest of assertions and tests from original file, using _original_builtin_print for test outputs) ...

        # Test Case 1: Successful detailed plan
        assert "Success" in result_1
        assert "2 development task(s)" in result_1 # Adjusted to match new summary
        manifest_path_1 = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, sanitize_project_name(project_name_1), "_ai_project_manifest.json")
        assert os.path.exists(manifest_path_1)
        with open(manifest_path_1, 'r') as f:
            manifest_content_1 = json.load(f)
        assert len(manifest_content_1["development_tasks"]) == 2
        assert manifest_content_1["development_tasks"][0]["details"]["filename"] == "app.py"
        assert manifest_content_1["development_tasks"][0]["details"]["key_components"] == ["comp1"]
        assert manifest_content_1["development_tasks"][0]["details"]["file_dependencies"] == ["util.py"]
        assert manifest_content_1["development_tasks"][0]["status"] == "planned"
        assert manifest_content_1["development_tasks"][1]["details"]["filename"] == "util.py"
        assert len(_printed_warnings_for_test) == 0

        # Test Case 2: Missing optional fields
        _original_builtin_print("\n--- Test 2: Missing Optional Fields ---")
        _created_dirs_for_test.clear(); _written_files_for_test.clear(); _printed_warnings_for_test.clear()
        project_name_2 = "MissingFieldsProject"
        result_2 = await initiate_ai_project(project_name_2, "Missing Fields Test")
        _original_builtin_print(f"Result 2: {result_2}")
        assert "Success" in result_2
        manifest_path_2 = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, sanitize_project_name(project_name_2), "_ai_project_manifest.json")
        assert os.path.exists(manifest_path_2)
        with open(manifest_path_2, 'r') as f:
            manifest_content_2 = json.load(f)
        assert len(manifest_content_2["development_tasks"]) == 1
        assert manifest_content_2["development_tasks"][0]["details"]["filename"] == "core.py"
        assert manifest_content_2["development_tasks"][0]["details"]["key_components"] == []
        assert manifest_content_2["development_tasks"][0]["details"]["file_dependencies"] == []
        assert manifest_content_2["development_tasks"][0]["status"] == "planned"
        # Warnings for missing fields are now printed by the main logic, not part of the DevelopmentTask defaulting
        assert any("key_components' for file 'core.py' is not a list" in warn for warn in _printed_warnings_for_test) # Updated check
        assert any("dependencies' for file 'core.py' is not a list" in warn for warn in _printed_warnings_for_test) # Updated check

        # Test Case 3: Optional fields have wrong types
        _original_builtin_print("\n--- Test 3: Wrong Types for Optional Fields ---")
        _created_dirs_for_test.clear(); _written_files_for_test.clear(); _printed_warnings_for_test.clear()
        project_name_3 = "WrongTypesProject"
        result_3 = await initiate_ai_project(project_name_3, "Wrong Types Test")
        _original_builtin_print(f"Result 3: {result_3}")
        assert "Success" in result_3
        manifest_path_3 = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, sanitize_project_name(project_name_3), "_ai_project_manifest.json")
        assert os.path.exists(manifest_path_3)
        with open(manifest_path_3, 'r') as f:
            manifest_content_3 = json.load(f)
        assert len(manifest_content_3["development_tasks"]) == 1
        assert manifest_content_3["development_tasks"][0]["details"]["filename"] == "service.py"
        assert manifest_content_3["development_tasks"][0]["details"]["key_components"] == []
        assert manifest_content_3["development_tasks"][0]["details"]["file_dependencies"] == []
        assert any("key_components' for file 'service.py' is not a list" in warn for warn in _printed_warnings_for_test)
        assert any("dependencies' for file 'service.py' is not a list" in warn for warn in _printed_warnings_for_test)

        # Test Case 4: LLM returns empty project plan list
        _original_builtin_print("\n--- Test 4: LLM Empty Plan List ---")
        _created_dirs_for_test.clear(); _written_files_for_test.clear(); _printed_warnings_for_test.clear()
        project_name_4 = "EmptyPlanProject"
        result_4 = await initiate_ai_project(project_name_4, "Empty Plan Test")
        _original_builtin_print(f"Result 4: {result_4}")
        assert "Success" in result_4
        assert "empty plan" in result_4.lower() # Check success message
        manifest_path_4 = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, sanitize_project_name(project_name_4), "_ai_project_manifest.json")
        assert os.path.exists(manifest_path_4)
        with open(manifest_path_4, 'r') as f:
            manifest_content_4 = json.load(f)
        assert manifest_content_4["development_tasks"] == []
        assert any("LLM returned an empty project plan" in warn for warn in _printed_warnings_for_test)

        _original_builtin_print("\n--- All initiate_ai_project tests seem to have passed (check warnings) ---")

        invoke_ollama_model_async = _original_invoke_ollama_model_async
        create_project_directory = _original_create_project_directory
        write_text_to_file = _original_write_text_to_file
        read_text_from_file = _original_read_text_from_file
        sanitize_project_name = _original_sanitize_project_name
        __builtins__.print = _original_builtin_print # type: ignore
        BASE_PROJECTS_DIR = original_base_dir_for_module

        if os.path.exists(TEST_BASE_PROJECTS_DIR_MAIN):
            shutil.rmtree(TEST_BASE_PROJECTS_DIR_MAIN)
            _original_builtin_print(f"Cleaned up test directory: {TEST_BASE_PROJECTS_DIR_MAIN}")

    async def run_pm_main_tests():
        await run_tests() # Runs initiate_ai_project tests

        # --- Tests for propose_project_file_update ---
        from ai_assistant.core.task_manager import TaskManager # Moved import to ensure it's available

        _original_builtin_print("\n--- Testing propose_project_file_update (async in __main__) ---")
        mock_tm_instance_for_propose_main = MagicMock(spec=TaskManager)

        with tempfile.TemporaryDirectory() as temp_dir_for_propose_main:
            test_file_path_for_propose_main = os.path.join(temp_dir_for_propose_main, "sample_project_file_for_propose.txt")

            with patch('ai_assistant.custom_tools.project_management_tools.edit_project_file', new_callable=AsyncMock, return_value=f"Project file '{test_file_path_for_propose_main}' updated successfully after review.") as mock_epf_success_main:
                result_prop1_main = await propose_project_file_update(test_file_path_for_propose_main, "new content for propose", "User requested update for propose", task_manager=mock_tm_instance_for_propose_main, parent_task_id="task_prop_123")
                _original_builtin_print(f"Result Prop1 (Success): {result_prop1_main}")
                mock_epf_success_main.assert_called_once_with(absolute_file_path=test_file_path_for_propose_main, new_content="new content for propose", change_description="User requested update for propose", task_manager=mock_tm_instance_for_propose_main, parent_task_id="task_prop_123")
                assert result_prop1_main["status"] == "success"

            with patch('ai_assistant.custom_tools.project_management_tools.edit_project_file', new_callable=AsyncMock, return_value=f"Change to project file '{test_file_path_for_propose_main}' rejected by critical review.") as mock_epf_rejected_main:
                result_prop2_main = await propose_project_file_update(test_file_path_for_propose_main, "other content for propose", "Another update for propose", task_manager=None)
                _original_builtin_print(f"Result Prop2 (Rejected): {result_prop2_main}")
                assert result_prop2_main["status"] == "rejected_by_review"

            with patch('ai_assistant.custom_tools.project_management_tools.edit_project_file', new_callable=AsyncMock, return_value="Error: Some internal failure in edit_project_file.") as mock_epf_error_main:
                result_prop3_main = await propose_project_file_update(test_file_path_for_propose_main, "error content for propose", "Error test for propose", task_manager=None)
                _original_builtin_print(f"Result Prop3 (Error): {result_prop3_main}")
                assert result_prop3_main["status"] == "error"
                assert "Some internal failure" in result_prop3_main["message"]
        
            with patch('ai_assistant.custom_tools.project_management_tools.edit_project_file', new_callable=AsyncMock, return_value=f"Proposed content for '{test_file_path_for_propose_main}' is identical to current. No changes made.") as mock_epf_identical_main:
                result_prop4_main = await propose_project_file_update(test_file_path_for_propose_main, "identical content for propose", "Identical test for propose", task_manager=None)
                _original_builtin_print(f"Result Prop4 (Identical): {result_prop4_main}")
                assert result_prop4_main["status"] == "success_no_change"
        
            result_prop5_main = await propose_project_file_update(test_file_path_for_propose_main, "some content", "") # Missing change_description
            _original_builtin_print(f"Result Prop5 (Missing Args): {result_prop5_main}")
            assert result_prop5_main["status"] == "error"
            assert "Missing required arguments" in result_prop5_main["message"]

        _original_builtin_print("--- Project Management Tools Tests (propose_project_file_update part) Finished ---")

    asyncio.run(run_pm_main_tests())
    __builtins__.print = _original_print # type: ignore

from ai_assistant.core.reviewer import ReviewerAgent 

async def request_code_review_tool(
    code_to_review: str,
    original_requirements: str,
    related_tests: Optional[str] = None
) -> Dict[str, Any]:
    """
    Requests a review for the provided code against original requirements and related tests.
    """
    if not code_to_review or not original_requirements:
        return {
            "status": "error",
            "comments": "Error: Code to review and original requirements must be provided.",
            "suggestions": ""
        }

    reviewer_agent = ReviewerAgent()
    review_results = await reviewer_agent.review_code(
        code_to_review=code_to_review,
        original_requirements=original_requirements,
        related_tests=related_tests
    )
    return review_results
