import sys
import os

# Add project root to path
sys.path.append(os.path.abspath("."))

from ai_assistant.core.project_manager import create_project, set_project_root_path, find_project
from ai_assistant.custom_tools.file_system_tools import BASE_PROJECTS_DIR

def repair_project_visibility(project_name):
    print(f"Repairing visibility for '{project_name}'...")
    
    # 1. Check if it exists in the filesystem
    sanitized_name = project_name.lower().replace(" ", "_") # Approximation of sanitize
    project_dir = os.path.join(BASE_PROJECTS_DIR, sanitized_name)
    
    # Try exact name match first in FS
    if not os.path.exists(project_dir):
        # Try finding it
        found = False
        for d in os.listdir(BASE_PROJECTS_DIR):
            if d.lower() == sanitized_name.lower() or d.lower() == project_name.lower():
                 project_dir = os.path.join(BASE_PROJECTS_DIR, d)
                 found = True
                 break
        if not found:
            print(f"Error: Could not refer to a directory for project '{project_name}' in '{BASE_PROJECTS_DIR}'")
            return

    print(f"Found project directory: {project_dir}")

    # 2. Register with Project Manager
    existing_proj = find_project(project_name)
    project_id = None
    
    if not existing_proj:
        print("Project not found in registry. Creating entry...")
        new_proj = create_project(project_name, f"Restored project {project_name}")
        if new_proj:
            project_id = new_proj['project_id']
    else:
        print(f"Project found in registry (ID: {existing_proj['project_id']}).")
        project_id = existing_proj['project_id']
        
    if project_id:
        print(f"Setting root path for ID {project_id} to {project_dir}")
        if set_project_root_path(project_id, project_dir):
            print("Success! Project should now be visible.")
        else:
            print("Failed to set root path.")
    else:
        print("Failed to get project ID.")

if __name__ == "__main__":
    repair_project_visibility("CheckersGame")
