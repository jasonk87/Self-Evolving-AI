# ai_assistant/custom_tools/project_management_tools.py
import json
import subprocess
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Updated imports for ProjectManifest and related dataclasses
from ai_assistant.project_management.manifest_schema import ProjectManifest, DevelopmentTask, BuildConfig, TestConfig, Dependency
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async 
from ai_assistant.config import get_model_for_task
from ai_assistant.custom_tools.file_system_tools import (
    create_project_directory, 
    write_text_to_file, 
    sanitize_project_name, 
    BASE_PROJECTS_DIR,
    read_text_from_file # Added missing import
)
import logging # Added for consistency

logger = logging.getLogger(__name__) # Added logger

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

    # 1. Sanitize project name (create_project_directory will also do this, but we need it for paths)
    sanitized_name = sanitize_project_name(project_name)
    
    # 2. Create project directory
    # create_project_directory returns a message like "Success: Project directory '{full_path}' created."
    # or "Error: ..."
    dir_creation_result = create_project_directory(project_name) # Uses the original name, sanitizes internally

    if dir_creation_result.startswith("Error:"):
        return dir_creation_result

    # Extract the actual path from the success message if possible, or reconstruct it.
    # Assuming success message format: "Success: Project directory '{path}' created."
    try:
        # More robust way to get the path: construct it as sanitize_project_name and create_project_directory would.
        project_dir_path = os.path.join(BASE_PROJECTS_DIR, sanitized_name)
        if not os.path.exists(project_dir_path): # Should exist if dir_creation_result was not an error
             return f"Error: Project directory creation reported success, but directory '{project_dir_path}' not found."
    except Exception as e: # Catch any parsing error
        return f"Error: Could not determine project directory path after creation. Detail: {e}"

    # 3. Create Basic Directory Structure (src, tests, README.md)
    try:
        os.makedirs(os.path.join(project_dir_path, "src"), exist_ok=True)
        os.makedirs(os.path.join(project_dir_path, "tests"), exist_ok=True)
        readme_content = f"# {project_name}\n\n{project_description}"
        readme_path = os.path.join(project_dir_path, "README.md")
        readme_write_result = write_text_to_file(readme_path, readme_content)
        if readme_write_result.startswith("Error:"):
            # Log warning but proceed, as manifest is more critical for now
            print(f"Warning: Failed to write README.md for project '{project_name}'. {readme_write_result}")
    except Exception as e_dir:
        return f"Error: Failed to create basic directory structure (src, tests, README.md) in '{project_dir_path}'. Detail: {e_dir}"

    # 4. LLM Call for Planning
    prompt = PROJECT_PLANNING_PROMPT_TEMPLATE.format(project_description=project_description)
    llm_model = get_model_for_task("planning")

    plan_response_str = await invoke_ollama_model_async(prompt, model_name=llm_model)
    development_tasks_list: List[DevelopmentTask] = [] # Changed to store DevelopmentTask objects

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

        for i, file_dict in enumerate(raw_plan_list): # Use raw_plan_list
            if not isinstance(file_dict, dict):
                print(f"Warning: Invalid item in LLM project plan (not a dict): {file_dict}. Skipping this item.")
                continue

            filename = file_dict.get("filename")
            description = file_dict.get("description") # This is file description
            key_components = file_dict.get("key_components")
            dependencies = file_dict.get("dependencies") # These are file-level dependencies

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
                description=f"Define structure and generate code for {filename}", # Task description
                details={ # Original file plan details from LLM
                    "filename": filename,
                    "original_description": description, # File's own description
                    "key_components": key_components,
                    "file_dependencies": dependencies 
                },
                status="planned" # Default status for new tasks
            )
            development_tasks_list.append(dev_task)
            
    except json.JSONDecodeError:
        return f"Error: LLM returned invalid JSON for project plan. Response: {plan_response_str}"

    # 5. Create ProjectManifest Instance
    current_timestamp_iso = datetime.now(timezone.utc).isoformat()
    manifest_instance = ProjectManifest(
        project_name=project_name,
        sanitized_project_name=sanitized_name,
        project_directory=project_dir_path, # Relative path from BASE_PROJECTS_DIR
        project_description=project_description,
        creation_timestamp=current_timestamp_iso,
        last_modified_timestamp=current_timestamp_iso,
        manifest_version="1.1.0",
        version="0.1.0",
        project_type="python", # Default for now
        development_tasks=development_tasks_list,
        entry_points={},
        dependencies=[], # Project-level dependencies
        project_goals=[],
        build_config=BuildConfig(), # Default instance
        test_config=TestConfig()    # Default instance
    )
    
    manifest_data_dict = manifest_instance.to_json_dict()

    # 6. Save Manifest
    manifest_filepath = os.path.join(project_dir_path, "_ai_project_manifest.json")
    write_result = write_text_to_file(manifest_filepath, json.dumps(manifest_data_dict, indent=4))

    if write_result.startswith("Error:"):
        return f"Error: Project directory and structure created at '{project_dir_path}', but failed to write manifest file. {write_result}"

    # 7. Return Success Message
    task_count = len(development_tasks_list)
    if task_count == 0:
        plan_summary = "empty plan (no development tasks specified or all entries malformed)."
    else:
        plan_summary = f"{task_count} development task(s) (primarily file creation tasks)."
        
    return (f"Success: Project '{project_name}' initialized in '{project_dir_path}' with src, tests dirs and README.md. "
            f"Plan includes {plan_summary} You can now ask to generate code for these tasks.")

# --- Code Generation Tool ---

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

    Args:
        project_name: The name of the project.
        filename: The name of the file for which to generate code (e.g., "main.py").

    Returns:
        A string confirming code generation and saving, or an error/info message.
    """
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."
    if not filename or not isinstance(filename, str) or not filename.strip():
        return "Error: Filename must be a non-empty string."

    sanitized_project_name = sanitize_project_name(project_name)
    project_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_project_name)
    manifest_filepath = os.path.join(project_dir, "_ai_project_manifest.json")

    # 1. Load Manifest using ProjectManifest dataclass
    manifest_json_str = read_text_from_file(manifest_filepath)
    if manifest_json_str.startswith("Error:"):
        return f"Error: Could not read project manifest for '{project_name}'. {manifest_json_str}"

    try:
        manifest_dict = json.loads(manifest_json_str)
        manifest_instance = ProjectManifest.from_dict(manifest_dict)
    except json.JSONDecodeError as e:
        return f"Error: Failed to parse project manifest for '{project_name}'. Invalid JSON. {e}"
    except Exception as e_manifest: # Catch errors from ProjectManifest.from_dict
        return f"Error: Failed to load project manifest data into ProjectManifest object for '{project_name}'. Detail: {e_manifest}"

    # 2. Find File Task in development_tasks
    file_task_entry: Optional[DevelopmentTask] = None
    # task_index = -1 # Not strictly needed if DevelopmentTask objects are mutable and part of the list
    
    for task in manifest_instance.development_tasks:
        if task.task_type == "CREATE_FILE" and task.details.get("filename") == filename:
            file_task_entry = task
            break
    
    if not file_task_entry:
        return f"Error: File task for '{filename}' not found in project development tasks for '{project_name}'."

    if file_task_entry.status == "generated":
        return f"Info: Code for '{filename}' in project '{project_name}' (Task ID: {file_task_entry.task_id}) has already been generated. Overwrite functionality is not yet supported."

    # Extracting prompt info from the found task and manifest
    overall_project_desc = manifest_instance.project_description
    file_task_details = file_task_entry.details
    
    file_plan_description = file_task_details.get("original_description", "No specific file description provided in task details.")
    key_components_list = file_task_details.get("key_components", [])
    dependencies_list = file_task_details.get("file_dependencies", []) # Note: key in details is "file_dependencies"

    if not isinstance(key_components_list, list):
        print(f"Warning: 'key_components' for {filename} in task {file_task_entry.task_id} is not a list. Original: {key_components_list}. Using empty list.")
        key_components_list = []
    key_components_str = "\n".join([f"- {str(item)}" for item in key_components_list]) if key_components_list else "No specific key components listed."

    if not isinstance(dependencies_list, list):
        print(f"Warning: 'file_dependencies' for {filename} in task {file_task_entry.task_id} is not a list. Original: {dependencies_list}. Using empty list.")
        dependencies_list = []
    dependencies_str = ", ".join([str(item) for item in dependencies_list]) if dependencies_list else "None listed."

    # 3. LLM Call for Code Generation (remains largely the same)
    prompt = CODE_GENERATION_PROMPT_TEMPLATE.format(
        project_description=overall_project_desc,
        filename=filename,
        file_description=file_plan_description,
        key_components_str=key_components_str,
        dependencies_str=dependencies_str
    )
    llm_model = get_model_for_task("code_generation")
    
    print(f"Info: Generating code for '{filename}' (Task ID: {file_task_entry.task_id}) in project '{project_name}' using model '{llm_model}'...")
    generated_code = await invoke_ollama_model_async(prompt, model_name=llm_model, temperature=0.5, max_tokens=4096) # Increased max_tokens

    if not generated_code or not generated_code.strip():
        file_task_entry.status = "failed" # Update status on failure
        file_task_entry.error_message = "LLM failed to generate code or returned empty code."
        file_task_entry.last_attempt_timestamp = datetime.now(timezone.utc).isoformat()
        manifest_instance.last_modified_timestamp = datetime.now(timezone.utc).isoformat()
        # Attempt to save manifest even on code gen failure to record the attempt
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

    # 4. Handle Directory Structure for Saving Code
    target_dir = project_dir # Default to project root
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
        file_task_entry.status = "failed" # Update status on failure
        file_task_entry.error_message = f"Failed to write generated code to file: {write_result}"
        file_task_entry.last_attempt_timestamp = datetime.now(timezone.utc).isoformat()
        manifest_instance.last_modified_timestamp = datetime.now(timezone.utc).isoformat()
        try:
            updated_manifest_dict_on_write_fail = manifest_instance.to_json_dict()
            write_text_to_file(manifest_filepath, json.dumps(updated_manifest_dict_on_write_fail, indent=4))
        except Exception as e_save_write_fail:
            print(f"Warning: Failed to update manifest after file write failure for task {file_task_entry.task_id}. Error: {e_save_write_fail}")
        return f"Error: Failed to write generated code for '{filename}' to file. {write_result}. Task '{file_task_entry.task_id}' marked as failed."

    # 5. Update Manifest After Successful Code Saving
    file_task_entry.status = "generated"
    file_task_entry.last_attempt_timestamp = datetime.now(timezone.utc).isoformat() # Or a new 'completion_timestamp'
    file_task_entry.error_message = None # Clear previous errors if any
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
    dependency_type: Optional[str] = None  # e.g., "pip", "npm" - could be inferred from project_type in manifest
) -> str:
    """
    Adds a dependency to the specified project's manifest.
    (Future implementation would also attempt to install it using package managers).

    Args:
        project_name: The name of the project.
        dependency_name: The name of the dependency (e.g., "requests", "react").
        dependency_version: The specific version of the dependency (e.g., "2.25.1", "^17.0.2").
        dependency_type: The type of dependency, if needed (e.g., "pip", "npm").

    Returns:
        A string confirming the action or an error message.
    """
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."
    if not dependency_name or not isinstance(dependency_name, str) or not dependency_name.strip():
        return "Error: Dependency name must be a non-empty string."

    sanitized_proj_name = sanitize_project_name(project_name)
    project_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_proj_name)
    manifest_filepath = os.path.join(project_dir, "_ai_project_manifest.json")

    # 1. Load ProjectManifest
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

    # 2. Create a new Dependency object
    new_dependency = Dependency(
        name=dependency_name,
        version=dependency_version,
        type=dependency_type
    )

    # 3. Add it to manifest.dependencies list (avoid duplicates by name, update if version/type changes)
    dependency_updated = False
    dependency_added = False
    existing_dep_index = -1

    for i, existing_dependency in enumerate(manifest.dependencies):
        if existing_dependency.name == new_dependency.name:
            existing_dep_index = i
            break
    
    if existing_dep_index != -1:
        # Dependency with the same name found, check if update is needed
        current_dep = manifest.dependencies[existing_dep_index]
        if (new_dependency.version is not None and current_dep.version != new_dependency.version) or \
           (new_dependency.type is not None and current_dep.type != new_dependency.type) or \
           (new_dependency.version is None and current_dep.version is not None) or \
           (new_dependency.type is None and current_dep.type is not None):
            # Update if version or type is different, or if new value is None and old was not (clearing a value)
            manifest.dependencies[existing_dep_index] = new_dependency
            dependency_updated = True
    else:
        # Dependency not found, add it
        manifest.dependencies.append(new_dependency)
        dependency_added = True

    if not dependency_added and not dependency_updated and existing_dep_index != -1:
        return f"Info: Dependency '{dependency_name}' already exists in project '{project_name}' with the same details. No changes made."

    # 4. Update manifest.last_modified_timestamp
    manifest.last_modified_timestamp = datetime.now(timezone.utc).isoformat()

    # 5. Save ProjectManifest
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
    """
    Runs the tests for the specified project based on 'test_config' in the manifest.

    Args:
        project_name: The name of the project.

    Returns:
        A string containing test results (e.g., stdout/stderr of test runner) or an error message.
    """
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."

    sanitized_proj_name = sanitize_project_name(project_name)
    project_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_proj_name)
    manifest_filepath = os.path.join(project_dir, "_ai_project_manifest.json")

    # 1. Load ProjectManifest
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

    # 2. Get test_config.test_command
    if not manifest.test_config or not manifest.test_config.test_command:
        return f"Info: No test command configured in the manifest for project '{project_name}'."

    test_command_str = manifest.test_config.test_command
    # Simple command splitting, assuming no complex shell features are needed in the command string itself.
    # For more complex commands, shlex.split might be better.
    test_command_parts = test_command_str.split()

    # 3. Execute it in the project directory
    try:
        logger.info(f"Running test command '{test_command_str}' for project '{project_name}' in directory '{project_dir}'...")
        process_result = subprocess.run(
            test_command_parts,
            cwd=project_dir, # Run from the project's root directory
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute timeout for tests
            check=False   # Do not raise exception for non-zero exit codes
        )

        # 4. Capture and return output
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
    """
    Builds the specified project based on 'build_config' in the manifest.

    Args:
        project_name: The name of the project.

    Returns:
        A string confirming build success or failure, including output or error messages.
    """
    if not project_name or not isinstance(project_name, str) or not project_name.strip():
        return "Error: Project name must be a non-empty string."

    sanitized_proj_name = sanitize_project_name(project_name)
    project_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_proj_name)
    manifest_filepath = os.path.join(project_dir, "_ai_project_manifest.json")

    # 1. Load ProjectManifest
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

    # 2. Get build_config.build_command
    if not manifest.build_config or not manifest.build_config.build_command:
        return f"Info: No build command configured in the manifest for project '{project_name}'."

    build_command_str = manifest.build_config.build_command
    build_command_parts = build_command_str.split()

    # 3. Execute it in the project directory
    try:
        logger.info(f"Running build command '{build_command_str}' for project '{project_name}' in directory '{project_dir}'...")
        process_result = subprocess.run(
            build_command_parts,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=600,  # 10-minute timeout for builds
            check=False
        )

        # 4. Capture and return output
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


if __name__ == '__main__':
    import asyncio
    import shutil # Ensure shutil is imported for cleanup

    # --- Mocking Dependencies (Global for test functions) ---
    # _original_read_text_from_file = None # Already defined if needed, but new mocks are more specific
    _mock_captured_code_gen_prompt = None # Global to capture prompt for code gen tests

    async def _mock_invoke_ollama_code_generator(prompt: str, model_name: str, **kwargs):
        global _mock_captured_code_gen_prompt
        _mock_captured_code_gen_prompt = prompt # Capture the prompt
        # print(f"DEBUG: _mock_invoke_ollama_code_generator called with prompt:\n{prompt}")
        if "app.py" in prompt: # Corresponds to filename in detailed plan
            return "print('Hello from app.py!')"
        elif "util.py" in prompt: # Corresponds to filename in detailed plan
            return "def helper_util_function():\n    return 'Helper util output'"
        return "# Default mock code from _mock_invoke_ollama_code_generator"

    # _mock_read_files = {} # Old mock, replaced by _written_files_for_test for MOCK_FS

    # def mock_fs_read_text_from_file(full_filepath_fs: str) -> str: # Old mock
    #     global _mock_read_files 
    #     print(f"MOCK read_text_from_file from: '{full_filepath_fs}'")
    #     if full_filepath_fs in _mock_read_files:
    #         return _mock_read_files[full_filepath_fs]
    #     if os.path.exists(full_filepath_fs):
    #          try:
    #             with open(full_filepath_fs, 'r') as f_real:
    #                 return f_real.read()
    #          except Exception as e_real:
    #             return f"Error: Mock fs read (real file) failed {e_real}"
    #     return f"Error: File '{full_filepath_fs}' not found."
    
    # --- Mocking Dependencies (Global for test functions) ---
    # _mock_invoke_ollama_responses = {} # Not used with current _mock_invoke_ollama_planner
    _original_invoke_ollama_model_async = None # Will store original invoke_ollama_model_async
    _original_create_project_directory = None
    _original_write_text_to_file = None
    _original_read_text_from_file = None # Added for consistency
    _original_sanitize_project_name = None
    _created_dirs_for_test = []
    _written_files_for_test = {}
    _printed_warnings_for_test = []

    # This will be our test-specific projects directory
    TEST_BASE_PROJECTS_DIR_MAIN = "temp_test_pm_projects"


    async def _mock_invoke_ollama_planner(prompt: str, model_name: str, **kwargs):
        # print(f"DEBUG: _mock_invoke_ollama_planner called with prompt containing: {project_description_marker}")
        # Based on project_description_marker, return a specific response
        # This is a simplified way to control mock response per test based on prompt content
        if "Detailed Plan Test" in prompt:
            return json.dumps({
                "project_plan": [
                    {"filename": "app.py", "description": "Main app.", "key_components": ["comp1"], "dependencies": ["util.py"]},
                    {"filename": "util.py", "description": "Utilities.", "key_components": ["helper"], "dependencies": []}
                ]
            })
        elif "Missing Fields Test" in prompt:
            return json.dumps({
                "project_plan": [
                    {"filename": "core.py", "description": "Core logic."} 
                    # key_components, dependencies missing
                ]
            })
        elif "Wrong Types Test" in prompt:
            return json.dumps({
                "project_plan": [
                    {"filename": "service.py", "description": "Service layer.", "key_components": "not_a_list", "dependencies": 123}
                ]
            })
        elif "Empty Plan Test" in prompt:
             return json.dumps({"project_plan": []})
        elif "Malformed JSON Test" in prompt:
            return "This is not valid JSON { "
        elif "Invalid Structure Test 1" in prompt: # Missing 'project_plan' key
            return json.dumps({"project_files": []})
        elif "Invalid Structure Test 2" in prompt: # 'project_plan' is not a list
            return json.dumps({"project_plan": {"filename": "test.py"}})
        elif "LLM No Response Test" in prompt:
            return None
        # Default for other tests like code gen that might be indirectly called
        return json.dumps({"project_plan": [{"filename": "default.py", "description": "Default."}]})


    def _mock_create_project_directory(project_name: str):
        global _created_dirs_for_test, TEST_BASE_PROJECTS_DIR_MAIN # Use the specific test base dir
        s_name = sanitize_project_name(project_name) # Use the real sanitize
        path = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, s_name)
        _created_dirs_for_test.append(path)
        os.makedirs(path, exist_ok=True)
        # print(f"MOCK_FS: Created directory {path}")
        return f"Success: Project directory '{path}' created."

    def _mock_write_text_to_file(full_filepath: str, content: str):
        global _written_files_for_test
        _written_files_for_test[full_filepath] = content
        # print(f"MOCK_FS: Wrote to file {full_filepath}")
        # Simulate actual writing for manifest checks
        try:
            os.makedirs(os.path.dirname(full_filepath), exist_ok=True)
            with open(full_filepath, 'w') as f:
                f.write(content)
            return f"Success: Content written to '{full_filepath}'."
        except Exception as e:
            return f"Error: Mock fs write failed {e}"
            
    def _mock_read_text_from_file(full_filepath: str): # Added for generate_code_for_project_file if needed
        if full_filepath in _written_files_for_test:
            return _written_files_for_test[full_filepath]
        # Simulate actual file reading for manifest checks if it was written by _mock_write_text_to_file
        if os.path.exists(full_filepath):
            try:
                with open(full_filepath, 'r') as f_real:
                    return f_real.read()
            except Exception as e_real:
                return f"Error: Mock fs read (real file) failed {e_real}"
        return f"Error: File not found '{full_filepath}'"    # Capture prints for warning checks
    _original_print = __builtins__.print
    def _captured_print(*args, **kwargs):
        global _printed_warnings_for_test
        _original_print(*args, **kwargs) # Keep original print behavior
        _printed_warnings_for_test.append(" ".join(map(str, args)))

    async def run_tests():
        global invoke_ollama_model_async, create_project_directory, write_text_to_file, read_text_from_file, sanitize_project_name, print
        global _original_invoke_ollama_model_async, _original_create_project_directory, _original_write_text_to_file, _original_read_text_from_file, _original_sanitize_project_name
        global BASE_PROJECTS_DIR, _created_dirs_for_test, _written_files_for_test, _printed_warnings_for_test
        
        # Store original functions and BASE_PROJECTS_DIR
        _original_invoke_ollama_model_async = invoke_ollama_model_async
        _original_create_project_directory = create_project_directory
        _original_write_text_to_file = write_text_to_file
        _original_read_text_from_file = read_text_from_file
        _original_sanitize_project_name = sanitize_project_name 
        
        original_base_dir_for_module = BASE_PROJECTS_DIR # Store the module's default
        
        # Apply mocks
        invoke_ollama_model_async = _mock_invoke_ollama_planner
        create_project_directory = _mock_create_project_directory
        write_text_to_file = _mock_write_text_to_file
        read_text_from_file = _mock_read_text_from_file
        # sanitize_project_name is used directly from file_system_tools, so no need to mock if it's correct there
        print_backup = print # Backup original print
        print = _captured_print # Set print to our capturing version

        BASE_PROJECTS_DIR = TEST_BASE_PROJECTS_DIR_MAIN # Override for tests

        # Ensure clean test environment
        if os.path.exists(TEST_BASE_PROJECTS_DIR_MAIN):
            shutil.rmtree(TEST_BASE_PROJECTS_DIR_MAIN)
        os.makedirs(TEST_BASE_PROJECTS_DIR_MAIN, exist_ok=True)

        # --- Test Case 1: Successful detailed plan ---
        _original_print("--- Test 1: Successful Detailed Plan ---")
        _created_dirs_for_test.clear()
        _written_files_for_test.clear()
        _printed_warnings_for_test.clear()
        project_name_1 = "DetailedPlanProject"
        result_1 = await initiate_ai_project(project_name_1, "Detailed Plan Test")
        _original_print(f"Result 1: {result_1}")
        assert "Success" in result_1
        assert "2 file(s) with detailed components and dependencies." in result_1
        manifest_path_1 = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, sanitize_project_name(project_name_1), "_ai_project_manifest.json")
        assert os.path.exists(manifest_path_1)
        with open(manifest_path_1, 'r') as f:
            manifest_content_1 = json.load(f)
        assert len(manifest_content_1["project_plan"]) == 2
        assert manifest_content_1["project_plan"][0]["filename"] == "app.py"
        assert manifest_content_1["project_plan"][0]["key_components"] == ["comp1"]
        assert manifest_content_1["project_plan"][0]["dependencies"] == ["util.py"]
        assert manifest_content_1["project_plan"][0]["status"] == "planned"
        assert manifest_content_1["project_plan"][1]["filename"] == "util.py"
        assert len(_printed_warnings_for_test) == 0

        # --- Test Case 2: Missing optional fields (key_components, dependencies) ---
        _original_print("\n--- Test 2: Missing Optional Fields ---")
        _created_dirs_for_test.clear()
        _written_files_for_test.clear()
        _printed_warnings_for_test.clear()
        project_name_2 = "MissingFieldsProject"
        result_2 = await initiate_ai_project(project_name_2, "Missing Fields Test")
        _original_print(f"Result 2: {result_2}")
        assert "Success" in result_2
        manifest_path_2 = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, sanitize_project_name(project_name_2), "_ai_project_manifest.json")
        assert os.path.exists(manifest_path_2)
        with open(manifest_path_2, 'r') as f:
            manifest_content_2 = json.load(f)
        assert len(manifest_content_2["project_plan"]) == 1
        assert manifest_content_2["project_plan"][0]["filename"] == "core.py"
        assert manifest_content_2["project_plan"][0]["key_components"] == [] # Defaulted
        assert manifest_content_2["project_plan"][0]["dependencies"] == [] # Defaulted
        assert manifest_content_2["project_plan"][0]["status"] == "planned"
        assert any("key_components' for file 'core.py' is not present or not a list. Defaulting to empty list." in warn for warn in _printed_warnings_for_test)
        assert any("dependencies' for file 'core.py' is not present or not a list. Defaulting to empty list." in warn for warn in _printed_warnings_for_test)


        # --- Test Case 3: Optional fields have wrong types ---
        _original_print("\n--- Test 3: Wrong Types for Optional Fields ---")
        _created_dirs_for_test.clear()
        _written_files_for_test.clear()
        _printed_warnings_for_test.clear()
        project_name_3 = "WrongTypesProject"
        result_3 = await initiate_ai_project(project_name_3, "Wrong Types Test")
        _original_print(f"Result 3: {result_3}")
        assert "Success" in result_3
        manifest_path_3 = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, sanitize_project_name(project_name_3), "_ai_project_manifest.json")
        assert os.path.exists(manifest_path_3)
        with open(manifest_path_3, 'r') as f:
            manifest_content_3 = json.load(f)
        assert len(manifest_content_3["project_plan"]) == 1
        assert manifest_content_3["project_plan"][0]["filename"] == "service.py"
        assert manifest_content_3["project_plan"][0]["key_components"] == [] # Defaulted
        assert manifest_content_3["project_plan"][0]["dependencies"] == [] # Defaulted
        assert any("key_components' for file 'service.py' is not a list. Defaulting to empty list." in warn for warn in _printed_warnings_for_test)
        assert any("dependencies' for file 'service.py' is not a list. Defaulting to empty list." in warn for warn in _printed_warnings_for_test)

        # --- Test Case 4: LLM returns empty project plan list ---
        _original_print("\n--- Test 4: LLM Empty Plan List ---")
        _created_dirs_for_test.clear(); _written_files_for_test.clear(); _printed_warnings_for_test.clear()
        project_name_4 = "EmptyPlanProject"
        result_4 = await initiate_ai_project(project_name_4, "Empty Plan Test")
        _original_print(f"Result 4: {result_4}")
        assert "Success" in result_4
        assert "empty plan" in result_4 # Check success message
        manifest_path_4 = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, sanitize_project_name(project_name_4), "_ai_project_manifest.json")
        assert os.path.exists(manifest_path_4)
        with open(manifest_path_4, 'r') as f:
            manifest_content_4 = json.load(f)
        assert manifest_content_4["project_plan"] == []
        assert any("LLM returned an empty project plan" in warn for warn in _printed_warnings_for_test)


        # --- Test Case 5: LLM returns malformed JSON ---
        _original_print("\n--- Test 5: LLM Malformed JSON ---")
        result_5 = await initiate_ai_project("MalformedJSONProject", "Malformed JSON Test")
        _original_print(f"Result 5: {result_5}")
        assert "Error: LLM returned invalid JSON for project plan." in result_5

        # --- Test Case 6: LLM returns invalid plan structure (missing 'project_plan' key) ---
        _original_print("\n--- Test 6: LLM Invalid Structure 1 ---")
        result_6 = await initiate_ai_project("InvalidStruct1Project", "Invalid Structure Test 1")
        _original_print(f"Result 6: {result_6}")
        assert "Error: LLM returned an invalid plan structure (missing 'project_plan' list)." in result_6

        # --- Test Case 7: LLM returns invalid plan structure ('project_plan' not a list) ---
        _original_print("\n--- Test 7: LLM Invalid Structure 2 ---")
        result_7 = await initiate_ai_project("InvalidStruct2Project", "Invalid Structure Test 2")
        _original_print(f"Result 7: {result_7}")
        assert "Error: LLM returned an invalid plan structure (missing 'project_plan' list)." in result_7
        
        # --- Test Case 8: LLM returns no response ---
        _original_print("\n--- Test 8: LLM No Response ---")
        result_8 = await initiate_ai_project("NoResponseProject", "LLM No Response Test")
        _original_print(f"Result 8: {result_8}")
        assert "Error: Failed to get project plan from LLM." in result_8

        _original_print("\n--- All initiate_ai_project tests seem to have passed (check warnings) ---")

        # Restore original functions and BASE_PROJECTS_DIR
        invoke_ollama_model_async = _original_invoke_ollama_model_async
        create_project_directory = _original_create_project_directory
        write_text_to_file = _original_write_text_to_file
        read_text_from_file = _original_read_text_from_file
        sanitize_project_name = _original_sanitize_project_name
        # Restore global print to the _original_print captured at the start of run_tests' outer scope
        # which is __builtins__.print.
        print = _original_print
        BASE_PROJECTS_DIR = original_base_dir_for_module

        # Cleanup
        if os.path.exists(TEST_BASE_PROJECTS_DIR_MAIN):
            shutil.rmtree(TEST_BASE_PROJECTS_DIR_MAIN)
            _original_print(f"Cleaned up test directory: {TEST_BASE_PROJECTS_DIR_MAIN}")

        # --- Original tests for generate_code_for_project_file and execute_project_coding_plan ---
        # These tests are from the original file and might need adjustment if they depend on
        # the old plan structure or specific mock behaviors not replicated here.
        # For this subtask, we are primarily focused on initiate_ai_project.
        # I will keep them for now but comment out the parts that might fail due to
        # the new plan structure not being accounted for in their specific mocks or setup.

        _original_print("\n--- Running Original Prerequisite Tests for Code Gen (may need adjustments) ---")
        # Ensure print is the original built-in for any direct calls in __main__ after run_tests
        print = _original_print

        # Re-setup mocks for these older tests if they use different mock LLM logic
        # For simplicity, we'll reuse the planner mock, which might not be ideal.
        invoke_ollama_model_async = _mock_invoke_ollama_planner # This will use the detailed plan mock

        # --- Original tests for generate_code_for_project_file and execute_project_coding_plan ---
        _original_print("\n--- Running Updated Tests for generate_code_for_project_file ---")
        # Ensure print is the original built-in
        print = _original_print

        
        # Setup a project specifically for these tests using the detailed planner mock
        # This ensures the manifest has 'key_components' and 'dependencies'
        invoke_ollama_model_async = _mock_invoke_ollama_planner 
        
        project_name_cg_test = "CodeGenDetailedProject"
        project_desc_cg_test = "Detailed Plan Test for CodeGen" # Triggers detailed plan from _mock_invoke_ollama_planner
        
        # Clear previous test artifacts if any for this specific project name
        sanitized_cg_proj_name = sanitize_project_name(project_name_cg_test)
        cg_project_dir_path = os.path.join(TEST_BASE_PROJECTS_DIR_MAIN, sanitized_cg_proj_name)
        if os.path.exists(cg_project_dir_path):
            shutil.rmtree(cg_project_dir_path)
        
        # Initialize the project - this will use _mock_invoke_ollama_planner
        init_cg_result = await initiate_ai_project(project_name_cg_test, project_desc_cg_test)
        assert "Success" in init_cg_result
        manifest_path_cg = os.path.join(cg_project_dir_path, "_ai_project_manifest.json")
        assert os.path.exists(manifest_path_cg)

        # Now, switch LLM mock to the code generator that captures prompts
        invoke_ollama_model_async = _mock_invoke_ollama_code_generator
        global _mock_captured_code_gen_prompt # Ensure it's accessible
        _mock_captured_code_gen_prompt = None


        # Test CG 1: Successful code generation with detailed plan info in prompt
        _original_print("\n--- Test CG 1: Successful generation & Prompt Verification ---")
        # Ensure print is the original built-in
        print = _original_print
        filename_app_py = "app.py" # This file is in the detailed plan from the mock
        
        cg_result1 = await generate_code_for_project_file(project_name_cg_test, filename_app_py)
        _original_print(f"Code Gen Result 1: {cg_result1}")
        assert f"Success: Code for '{filename_app_py}' generated" in cg_result1
        assert _mock_captured_code_gen_prompt is not None
        assert "Overall Project Description:\nDetailed Plan Test for CodeGen" in _mock_captured_code_gen_prompt
        assert f"Current File to Generate: {filename_app_py}" in _mock_captured_code_gen_prompt
        assert "Purpose of this file (from project plan): Main app." in _mock_captured_code_gen_prompt
        assert "Key Components for this file:\n- comp1" in _mock_captured_code_gen_prompt
        assert "Dependencies for this file (other files in this project):\nutil.py" in _mock_captured_code_gen_prompt
        
        # Verify code file content
        expected_code_path_app = os.path.join(cg_project_dir_path, filename_app_py)
        assert os.path.exists(expected_code_path_app)
        with open(expected_code_path_app, 'r') as f:
            assert "print('Hello from app.py!')" in f.read()
        
        # Verify manifest update for this file
        with open(manifest_path_cg, 'r') as f_m_cg_updated:
            manifest_data_after_cg1 = json.load(f_m_cg_updated)
        app_py_entry = next((item for item in manifest_data_after_cg1["project_plan"] if item["filename"] == filename_app_py), None)
        assert app_py_entry is not None
        assert app_py_entry["status"] == "generated"
        assert "last_code_generation_timestamp" in app_py_entry

        # Test CG 2: File not in plan (should still work as before)
        _original_print("\n--- Test CG 2: File not in plan ---")
        # Ensure print is the original built-in
        print = _original_print
        _mock_captured_code_gen_prompt = None
        cg_result2 = await generate_code_for_project_file(project_name_cg_test, "non_existent_file.py")
        _original_print(f"Code Gen Result 2: {cg_result2}")
        assert "Error: File 'non_existent_file.py' not found" in cg_result2
        assert _mock_captured_code_gen_prompt is None # LLM should not have been called

        # Test CG 3: File already generated (should still work as before)
        _original_print("\n--- Test CG 3: File already generated ---")
        # Ensure print is the original built-in
        print = _original_print
        _mock_captured_code_gen_prompt = None
        cg_result3 = await generate_code_for_project_file(project_name_cg_test, filename_app_py) # app.py was generated in Test CG 1
        _original_print(f"Code Gen Result 3: {cg_result3}")
        assert f"Info: Code for '{filename_app_py}' in project '{project_name_cg_test}' has already been generated" in cg_result3
        assert _mock_captured_code_gen_prompt is None

        # Test CG 4: LLM fails to generate code (e.g., returns empty)
        _original_print("\n--- Test CG 4: LLM fails to generate code ---")
        # Ensure print is the original built-in
        print = _original_print
        filename_util_py = "util.py" # This file is in the plan and should be 'planned'
        # Temporarily make the mock return empty for this specific file
        original_code_gen_mock = invoke_ollama_model_async 
        async def mock_empty_for_util(prompt: str, model_name: str, **kwargs):
            global _mock_captured_code_gen_prompt
            _mock_captured_code_gen_prompt = prompt
            if filename_util_py in prompt:
                return "" # Empty response
            return await original_code_gen_mock(prompt, model_name, **kwargs) # Call original mock for others
        invoke_ollama_model_async = mock_empty_for_util
        _mock_captured_code_gen_prompt = None

        cg_result4 = await generate_code_for_project_file(project_name_cg_test, filename_util_py)
        _original_print(f"Code Gen Result 4: {cg_result4}")
        assert f"Error: LLM failed to generate code for '{filename_util_py}'" in cg_result4
        assert _mock_captured_code_gen_prompt is not None # Prompt should have been made
        # Check that the util.py status is still "planned"
        with open(manifest_path_cg, 'r') as f_m_cg_after_fail:
            manifest_data_after_cg4 = json.load(f_m_cg_after_fail)
        util_py_entry = next((item for item in manifest_data_after_cg4["project_plan"] if item["filename"] == filename_util_py), None)
        assert util_py_entry is not None
        assert util_py_entry["status"] == "planned"

        invoke_ollama_model_async = original_code_gen_mock # Restore the main code gen mock

        _original_print("\n--- End of generate_code_for_project_file tests ---")
        # Ensure print is the original built-in
        print = _original_print

        # Restore original functions and BASE_PROJECTS_DIR fully at the end
        invoke_ollama_model_async = _original_invoke_ollama_model_async # Restore original planner/codegen mock
        create_project_directory = _original_create_project_directory
        write_text_to_file = _original_write_text_to_file
        read_text_from_file = _original_read_text_from_file
        sanitize_project_name = _original_sanitize_project_name
        print = _original_print # Ensure it's restored to the true built-in
        BASE_PROJECTS_DIR = original_base_dir_for_module

        # Final cleanup
        if os.path.exists(TEST_BASE_PROJECTS_DIR_MAIN):
            shutil.rmtree(TEST_BASE_PROJECTS_DIR_MAIN)
            _original_print(f"Cleaned up test directory: {TEST_BASE_PROJECTS_DIR_MAIN}")

    # Explicitly restore print to __builtins__.print after asyncio.run completes,
    # to ensure Pylance is satisfied for any subsequent print calls in __main__.
    asyncio.run(run_tests())
    print = __builtins__.print

    if __name__ == '__main__':
        # This __main__ block now includes tests for initiate_ai_project, 
        # generate_code_for_project_file, and execute_project_coding_plan.

        # Define mock_invoke_ollama_good_plan and other LLM mocks if they are not already defined at this scope
        # For simplicity, we assume they are defined as in the previous version of the __main__ block.
        # If not, they would need to be defined here or imported if refactored into a test utility module.
        
        # Example mock LLM responses (ensure these are defined before run_tests if not already)
        async def mock_invoke_ollama_good_plan(prompt: str, model_name: str):
            # print(f"MOCK LLM call for: {model_name} (Good Plan - Detailed)")
            return json.dumps({
                "project_plan": [
                    {
                        "filename": "main.py", 
                        "description": "Main application script.",
                        "key_components": ["app_setup", "routes", "main_logic"],
                        "dependencies": ["utils.py", "models.py"]
                    },
                    {
                        "filename": "utils.py", 
                        "description": "Utility functions.",
                        "key_components": ["helper_function_1", "data_parser"],
                        "dependencies": []
                    },
                    {
                        "filename": "models.py", 
                        "description": "Data models/schemas.",
                        "key_components": ["UserSchema", "ProductSchema"],
                        "dependencies": []
                    }
                ]
            })

        async def mock_invoke_ollama_good_plan_missing_fields(prompt: str, model_name: str):
            # Test lenient validation: key_components and dependencies missing
            return json.dumps({
                "project_plan": [
                    {
                        "filename": "core_logic.py", 
                        "description": "Core business logic without explicit components/deps listed."
                        # key_components and dependencies are missing
                    }
                ]
            })

        async def mock_invoke_ollama_good_plan_wrong_types(prompt: str, model_name: str):
            # Test lenient validation: key_components and dependencies are not lists
            return json.dumps({
                "project_plan": [
                    {
                        "filename": "service.py", 
                        "description": "Service layer.",
                        "key_components": "should_be_list", # Incorrect type
                        "dependencies": "should_also_be_list" # Incorrect type
                    }
                ]
            })
        
        async def mock_invoke_ollama_empty_plan(prompt: str, model_name: str): 
            # print(f"MOCK LLM call for: {model_name} (Empty Plan)")
            return json.dumps({"project_plan": []})
        
        async def mock_invoke_ollama_malformed_json(prompt: str, model_name: str): 
            # print(f"MOCK LLM call for: {model_name} (Malformed JSON)")
            return "This is not JSON { definitely not json"
            
        async def mock_invoke_ollama_invalid_plan_structure1(prompt: str, model_name: str):
            # print(f"MOCK LLM call for: {model_name} (Invalid Plan Structure 1)")
            return json.dumps({"project_files": []}) # Missing "project_plan" key

        async def mock_invoke_ollama_invalid_plan_structure2(prompt: str, model_name: str):
            # print(f"MOCK LLM call for: {model_name} (Invalid Plan Structure 2)")
            return json.dumps({"project_plan": {"filename": "main.py"}}) # "project_plan" is not a list
            
        async def mock_invoke_ollama_no_response(prompt: str, model_name: str): 
            # print(f"MOCK LLM call for: {model_name} (No Response)")
            return None

        TEST_BASE_PROJECTS_DIR = "temp_test_ai_projects" 

        asyncio.run(run_tests())


# --- Code Review Tool ---
# Ensure this import is at the top level (column 1)
from ai_assistant.core.reviewer import ReviewerAgent 

async def request_code_review_tool(
    code_to_review: str,
    original_requirements: str,
    related_tests: Optional[str] = None
) -> Dict[str, Any]:
    """
    Requests a review for the provided code against original requirements and related tests.

    Args:
        code_to_review: The actual code content (string) to be reviewed.
        original_requirements: The description of what the code was supposed to do.
        related_tests: (Optional) String representation of tests related to this code.

    Returns:
        A dictionary containing the review results (status, comments, suggestions).
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
