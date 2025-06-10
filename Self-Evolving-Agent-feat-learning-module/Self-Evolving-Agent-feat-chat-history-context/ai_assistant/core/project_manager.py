import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Union

from ai_assistant.config import get_data_dir
from ai_assistant.utils.display_utils import CLIColors, color_text # For potential direct use or consistency

PROJECTS_FILE_NAME = "projects.json"

def get_projects_file_path() -> str:
    return os.path.join(get_data_dir(), PROJECTS_FILE_NAME)

def _load_projects() -> List[Dict[str, Any]]:
    """Loads projects from the JSON file."""
    filepath = get_projects_file_path()
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content:
                return []
            return json.loads(content)
    except (IOError, json.JSONDecodeError) as e:
        print(color_text(f"Error loading projects: {e}", CLIColors.ERROR_MESSAGE))
        return []

def _save_projects(projects: List[Dict[str, Any]]) -> bool:
    """Saves projects to the JSON file."""
    filepath = get_projects_file_path()
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(projects, f, indent=4)
        return True
    except IOError as e:
        print(color_text(f"Error saving projects: {e}", CLIColors.ERROR_MESSAGE))
        return False

def list_projects() -> List[Dict[str, Any]]:
    """Returns a list of all projects."""
    return _load_projects()

# Conceptual Schema for create_project tool
# CREATE_PROJECT_SCHEMA = {
#     "name": "create_new_project",
#     "description": "Creates a new project with a unique name and optional description.",
#     "parameters": [
#         {"name": "name", "type": "str", "description": "The unique name for the new project."},
#         {"name": "description", "type": "str", "description": "Optional. A brief description of the project."}
#     ]
# }
def create_project(name: str, description: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Creates a new project."""
    projects = _load_projects()
    if any(p['name'].lower() == name.lower() for p in projects):
        print(color_text(f"Project with name '{name}' already exists.", CLIColors.ERROR_MESSAGE))
        return None

    new_project = {
        "project_id": str(uuid.uuid4()),
        "name": name,
        "description": description or "",
        "status": "planning", # Default status
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tasks": []
    }
    projects.append(new_project)
    if _save_projects(projects):
        print(color_text(f"Project '{name}' created successfully with ID: {new_project['project_id']}.", CLIColors.SUCCESS))
        return new_project
    return None

def find_project(identifier: str) -> Optional[Dict[str, Any]]:
    """Finds a project by its ID or name."""
    projects = _load_projects()
    # Try by ID first
    for project in projects:
        if project['project_id'] == identifier:
            return project
    # Then try by name (case-insensitive)
    for project in projects:
        if project['name'].lower() == identifier.lower():
            return project
    return None

def remove_project(identifier: str) -> bool:
    """Removes a project by its ID or name."""
    projects = _load_projects()
    project_to_remove = find_project(identifier)

    if not project_to_remove:
        print(color_text(f"Project '{identifier}' not found.", CLIColors.ERROR_MESSAGE))
        return False

    projects = [p for p in projects if p['project_id'] != project_to_remove['project_id']]
    if _save_projects(projects):
        print(color_text(f"Project '{project_to_remove['name']}' (ID: {project_to_remove['project_id']}) removed.", CLIColors.SUCCESS))
        return True
    return False

def get_project_info(identifier: str) -> Optional[Dict[str, Any]]:
    """Gets detailed information for a specific project."""
    project = find_project(identifier)
    if not project:
        print(color_text(f"Project '{identifier}' not found.", CLIColors.ERROR_MESSAGE))
    return project

def get_project_status(identifier: str) -> Optional[str]:
    """Gets the status of a specific project."""
    project = find_project(identifier)
    return project['status'] if project else None

# Conceptual Schema for update_project tool
# UPDATE_PROJECT_SCHEMA = {
#     "name": "update_project_details",
#     "description": "Updates the name and/or description of an existing project. At least one new value must be provided.",
#     "parameters": [
#         {"name": "identifier", "type": "str", "description": "The ID or current name of the project to update."},
#         {"name": "new_name", "type": "str", "description": "Optional. The new name for the project. Must be unique if provided."},
#         {"name": "new_description", "type": "str", "description": "Optional. The new description for the project."}
#     ]
# }
def update_project(identifier: str, new_name: Optional[str] = None, new_description: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Updates a project's name and/or description.
    At least one of new_name or new_description must be provided.
    """
    if new_name is None and new_description is None:
        print(color_text("Error: No changes provided for update_project. Specify new_name and/or new_description.", CLIColors.ERROR_MESSAGE))
        return None

    projects = _load_projects()
    project_to_update = None
    updated = False

    # Find the project first to get its ID for accurate conflict checking
    current_project_obj = find_project(identifier)
    if not current_project_obj:
        print(color_text(f"Error: Project '{identifier}' not found for update.", CLIColors.ERROR_MESSAGE))
        return None

    current_project_id = current_project_obj['project_id']

    # If new_name is provided, check for name conflicts against other projects
    if new_name is not None and new_name.lower() != current_project_obj['name'].lower(): # Only check if name is actually changing
        if any(p['name'].lower() == new_name.lower() and p['project_id'] != current_project_id for p in projects):
            print(color_text(f"Error: Another project with the name '{new_name}' already exists.", CLIColors.ERROR_MESSAGE))
            return None

    for project_in_list in projects:
        # Operate on the found project using its unique ID to avoid issues if identifier was a name
        if project_in_list['project_id'] == current_project_id:
            project_to_update = project_in_list # This is the actual object in the list to modify
            if new_name is not None and project_to_update['name'] != new_name:
                project_to_update['name'] = new_name
                updated = True
            if new_description is not None and project_to_update['description'] != new_description:
                project_to_update['description'] = new_description
                updated = True

            if updated:
                project_to_update['updated_at'] = datetime.now(timezone.utc).isoformat()
            break

    # This check should technically be redundant due to find_project above, but kept for safety.
    if not project_to_update: # pragma: no cover
        print(color_text(f"Project '{identifier}' (ID: {current_project_id}) not found in list for update (internal error).", CLIColors.ERROR_MESSAGE))
        return None

    if not updated:
        print(color_text(f"No actual changes detected for project '{current_project_obj['name']}'. Name and description are the same.", CLIColors.WARNING_MESSAGE))
        return project_to_update # Return the project as is

    if _save_projects(projects):
        print(color_text(f"Project '{project_to_update['name']}' (ID: {current_project_id}) updated successfully.", CLIColors.SUCCESS))
        return project_to_update
    else: # pragma: no cover
        print(color_text(f"Failed to save updates for project '{current_project_obj['name']}'.", CLIColors.ERROR_MESSAGE))
        return None

def update_project_status(identifier: str, new_status: str) -> bool:
    """Updates the status of a project."""
    projects = _load_projects()
    project_found = False
    for project in projects:
        if project['project_id'] == identifier or project['name'].lower() == identifier.lower():
            project['status'] = new_status
            project['updated_at'] = datetime.now(timezone.utc).isoformat()
            project_found = True
            break
    
    if not project_found:
        print(color_text(f"Project '{identifier}' not found for status update.", CLIColors.ERROR_MESSAGE))
        return False

    if _save_projects(projects):
        print(color_text(f"Status of project '{identifier}' updated to '{new_status}'.", CLIColors.SUCCESS))
        return True
    return False


def get_all_projects_summary_status() -> str:
    """Returns a summary string of all project statuses."""
    projects = _load_projects()
    if not projects:
        return "No projects found."
    
    status_counts: Dict[str, int] = {}
    for project in projects:
        status = project.get('status', 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1
    
    summary_lines = [f"Total Projects: {len(projects)}"]
    for status, count in status_counts.items():
        summary_lines.append(f"  - {status.capitalize()}: {count}")
    return "\n".join(summary_lines)

# Conceptual Schema for remove_project tool
# REMOVE_PROJECT_SCHEMA = {
#     "name": "delete_project",
#     "description": "Deletes a project by its ID or name. This action is irreversible.",
#     "parameters": [
#         {"name": "identifier", "type": "str", "description": "The ID or name of the project to delete."}
#     ]
# }

# Example main for testing project_manager functionalities
if __name__ == "__main__": # pragma: no cover
    print("--- Testing Project Manager ---")

    # Clean up projects.json before running tests if it exists
    projects_file = get_projects_file_path()
    if os.path.exists(projects_file):
        os.remove(projects_file)
        print(f"Removed existing '{PROJECTS_FILE_NAME}' for a clean test run.")

    print("\n--- Listing Projects (Initial) ---")
    print(list_projects())

    print("\n--- Creating Projects ---")
    project1_name = "My Awesome Project"
    project1 = create_project(project1_name, "This is a test project for awesomeness.")
    project2_name = "Web Server For Cats"
    project2 = create_project(project2_name, "A web server dedicated to our feline friends.")
    project3_name = "Data Analysis Tool" # Will try to create this again for conflict test
    project3 = create_project(project3_name, "Tool for analyzing complex datasets.")

    # Test creating a project with a conflicting name
    print("\n--- Testing Name Conflict on Create ---")
    create_project(project1_name, "This should fail due to name conflict.")


    print("\n--- Listing Projects (After Create) ---")
    all_projects = list_projects()
    for p in all_projects:
        print(f"- ID: {p['project_id']}, Name: {p['name']}, Desc: {p['description'][:30]}...")

    if project1 and project2 and project3:
        print("\n--- Testing Find Project ---")
        found_by_id = find_project(project1['project_id'])
        print(f"Found by ID '{project1['project_id']}': {'Yes' if found_by_id else 'No'}")
        found_by_name = find_project(project2['name'])
        print(f"Found by Name '{project2['name']}': {'Yes' if found_by_name else 'No'}")
        found_non_existent = find_project("NonExistentProject")
        print(f"Found 'NonExistentProject': {'Yes' if found_non_existent else 'No'}")

        print("\n--- Testing Update Project Status ---")
        update_project_status(project1['project_id'], "active_development")
        update_project_status(project2['name'], "on_hold")
        updated_p1_status = get_project_status(project1['project_id'])
        print(f"Status of '{project1['name']}': {updated_p1_status}")

        print("\n--- Testing Update Project Details ---")
        # Test successful update
        updated_p1 = update_project(project1['project_id'], new_name="My Super Awesome Project", new_description="Enhanced awesomeness achieved.")
        if updated_p1:
            print(f"Updated P1: Name='{updated_p1['name']}', Desc='{updated_p1['description']}'")

        # Test update with no actual change
        print("\nTesting update with no actual change:")
        updated_p1_no_change = update_project(project1['project_id'], new_name=updated_p1['name'] if updated_p1 else project1_name)
        if updated_p1_no_change:
            print(f"P1 (no change): Name='{updated_p1_no_change['name']}'")

        # Test update causing name conflict
        print("\nTesting update causing name conflict:")
        # Try to rename project2 to project3's name
        update_conflict = update_project(project2['project_id'], new_name=project3['name'])
        if not update_conflict:
            print(f"Correctly failed to update '{project2['name']}' due to name conflict with '{project3['name']}'.")

        # Test updating a non-existent project
        print("\nTesting update for non-existent project:")
        update_non_existent = update_project("fake-id-123", new_name="Fake Name")
        if not update_non_existent:
            print("Correctly failed to update non-existent project.")


        print("\n--- Testing Get Project Info ---")
        info_p1 = get_project_info(project1['project_id']) # Use the ID of the potentially renamed project1
        if info_p1:
            print(f"Info for project ID '{info_p1['project_id']}': Status='{info_p1['status']}', Tasks='{len(info_p1['tasks'])}'")

        print("\n--- Current Project Status Summary ---")
        print(get_all_projects_summary_status())

        print("\n--- Testing Remove Project ---")
        projects_before_remove = list_projects()
        print(f"Projects before removal: {[p['name'] for p in projects_before_remove]}")

        # Remove project2 by its original name (assuming it wasn't renamed in a conflict test that passed)
        # To be safe, let's re-find it if its name might have changed from 'project2_name'
        project2_obj_for_removal = find_project(project2['project_id']) # Find by ID to get current name for logging
        if project2_obj_for_removal:
            print(f"Attempting to remove project: '{project2_obj_for_removal['name']}' (ID: {project2_obj_for_removal['project_id']})")
            removal_status = remove_project(project2_obj_for_removal['project_id']) # Remove by ID for safety
            print(f"Removal status for '{project2_obj_for_removal['name']}': {removal_status}")
        else:
            print(f"Could not find project with original name '{project2_name}' or its ID for removal test.")

        projects_after_remove = list_projects()
        print(f"Projects after removal: {[p['name'] for p in projects_after_remove]}")

        # Test removing a non-existent project
        print("\nTesting removal of non-existent project:")
        remove_non_existent_status = remove_project("ghost-project-id")
        print(f"Removal status for 'ghost-project-id': {remove_non_existent_status}")

    else:
        print("Initial project creation failed, skipping further tests.")

    print("\n--- Project Manager Testing Finished ---")
    # print(f"Note: '{PROJECTS_FILE_NAME}' was modified in '{get_data_dir()}'. You may want to delete it.")