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

