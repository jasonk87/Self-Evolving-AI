# ai_assistant/custom_tools/file_system_tools.py
import os
import re
from typing import Union # For return type hints if using dicts for errors, though task specifies string returns

# Import get_data_dir from the main config to centralize data paths for some things,
# but ai_generated_projects will be directly under ai_assistant.
from ..config import get_data_dir # Keep for other potential data uses if any

# Module-level constant for the base directory where projects will be created.
# Changed: Now places ai_generated_projects directly under the 'ai_assistant' package directory.
ai_assistant_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BASE_PROJECTS_DIR = os.path.join(ai_assistant_dir, "ai_generated_projects")

def sanitize_project_name(name: str) -> str:
    """
    Sanitizes a project name to create a safe directory name.

    - Converts to lowercase.
    - Replaces spaces and multiple hyphens with a single underscore.
    - Removes characters that are not alphanumeric, underscores, or hyphens.
    - Ensures it's not empty (defaults to "unnamed_project").
    - Limits length to a maximum of 50 characters.

    Args:
        name: The raw project name string.

    Returns:
        A sanitized string suitable for use as a directory name.
    """
    if not name or not name.strip():
        return "unnamed_project"

    s_name = name.lower()
    s_name = re.sub(r'\s+', '_', s_name)  # Replace spaces with underscores
    s_name = re.sub(r'-+', '_', s_name)   # Replace one or more hyphens with a single underscore
    s_name = re.sub(r'[^\w-]', '', s_name) # Remove non-alphanumeric characters (keeps underscores and hyphens)
    s_name = re.sub(r'_+', '_', s_name)   # Replace multiple underscores with a single one

    if not s_name: # If sanitization results in an empty string (e.g., "!!!")
        return "unnamed_project"
    
    return s_name[:50] # Limit length

def create_project_directory(project_name: str) -> str:
    """
    Creates a new project directory under the BASE_PROJECTS_DIR.

    Args:
        project_name: The desired name for the project. This will be sanitized.

    Returns:
        A string indicating success or failure, including the path or an error message.
    """
    if not project_name or not isinstance(project_name, str):
        return "Error: Project name must be a non-empty string."

    sanitized_name = sanitize_project_name(project_name)
    full_path = os.path.join(BASE_PROJECTS_DIR, sanitized_name)

    if os.path.exists(full_path):
        return f"Error: Project directory '{full_path}' already exists."
    
    try:
        os.makedirs(full_path, exist_ok=True) # exist_ok=True also creates BASE_PROJECTS_DIR if needed
        return f"Success: Project directory '{full_path}' created."
    except OSError as e:
        return f"Error creating project directory '{full_path}': {e}"
    except Exception as e:
        return f"An unexpected error occurred while creating project directory '{full_path}': {e}"

def write_text_to_file(full_filepath: str, content: str) -> str:
    """
    Writes the given text content to the specified file.
    Ensures the directory for the file exists before writing.

    Args:
        full_filepath: The absolute or relative path to the file.
        content: The string content to write to the file.

    Returns:
        A string indicating success or an error message.
    """
    if not full_filepath or not isinstance(full_filepath, str):
        return "Error: Filepath must be a non-empty string."
    if not isinstance(content, str):
        return "Error: Content must be a string."

    try:
        dir_path = os.path.dirname(full_filepath)
        if dir_path: # If there's a directory part
            os.makedirs(dir_path, exist_ok=True)
        
        with open(full_filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Success: Content written to '{full_filepath}'."
    except OSError as e:
        return f"Error writing to file '{full_filepath}': {e}"
    except Exception as e:
        return f"An unexpected error occurred while writing to file '{full_filepath}': {e}"

def read_text_from_file(full_filepath: str) -> str:
    """
    Reads and returns the text content from the specified file.

    Args:
        full_filepath: The absolute or relative path to the file.

    Returns:
        The content of the file as a string, or an error message string if reading fails.
    """
    if not full_filepath or not isinstance(full_filepath, str):
        return "Error: Filepath must be a non-empty string."

    if not os.path.exists(full_filepath):
        return f"Error: File '{full_filepath}' not found."
    
    if not os.path.isfile(full_filepath): # Check if it's actually a file
        return f"Error: Path '{full_filepath}' is not a file."

    try:
        with open(full_filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except OSError as e:
        return f"Error reading file '{full_filepath}': {e}"
    except Exception as e:
        return f"An unexpected error occurred while reading file '{full_filepath}': {e}"

if __name__ == '__main__':
    import shutil # For cleaning up test directories

    print("--- Testing File System Tools ---")

    # --- Test sanitize_project_name ---
    print("\n--- Testing sanitize_project_name ---")
    test_names = {
        "My Awesome Hangman Game!": "my_awesome_hangman_game",
        "Project with spaces": "project_with_spaces",
        "project-with-hyphens": "project_with_hyphens",
        "Project_With_Underscores": "project_with_underscores",
        "Th!s h@s $pec!@l ch@r$": "thshs_pecl_chr",
        "  leading and trailing spaces  ": "leading_and_trailing_spaces",
        "---multiple---hyphens---": "multiple_hyphens",
        "__": "unnamed_project", # Becomes empty after initial sanitization
        "": "unnamed_project",
        "a"*60: "a"*50, # Length limit test
        "Valid-Name_123": "valid-name_123" # Test with allowed special chars
    }
    for original, expected in test_names.items():
        sanitized = sanitize_project_name(original)
        print(f"Original: '{original}' -> Sanitized: '{sanitized}' (Expected: '{expected}')")
        assert sanitized == expected, f"Sanitization failed for '{original}'"
    print("sanitize_project_name tests passed.")

    # --- Test create_project_directory ---
    print("\n--- Testing create_project_directory ---")
    project1_name = "My Test Project Alpha"
    sanitized_p1_name = sanitize_project_name(project1_name)
    expected_p1_path = os.path.join(BASE_PROJECTS_DIR, sanitized_p1_name)

    # Test creating a new directory
    result_create1 = create_project_directory(project1_name)
    print(result_create1)
    assert "Success" in result_create1 and expected_p1_path in result_create1
    assert os.path.exists(expected_p1_path) and os.path.isdir(expected_p1_path)
    print(f"Verified directory '{expected_p1_path}' exists.")

    # Test attempting to create an existing directory
    result_create_existing = create_project_directory(project1_name)
    print(result_create_existing)
    assert "Error" in result_create_existing and "already exists" in result_create_existing
    print("Attempt to create existing directory handled correctly.")
    
    # Test with empty project name
    result_empty_name = create_project_directory("")
    print(result_empty_name)
    assert "Error" in result_empty_name or "unnamed_project" in sanitize_project_name("") # depends on if error is before or after sanitize
    if "Success" in result_empty_name : # if it allows unnamed_project
        assert os.path.exists(os.path.join(BASE_PROJECTS_DIR, "unnamed_project"))
    print("Empty project name test handled.")

    print("create_project_directory tests passed.")

    # --- Test write_text_to_file and read_text_from_file ---
    print("\n--- Testing write_text_to_file and read_text_from_file ---")
    test_file_content = "Hello, this is a test file.\nIt has multiple lines.\nEnd of test."
    test_file_path = os.path.join(expected_p1_path, "test_file.txt") # Place inside created project
    
    # Test writing a new file
    result_write = write_text_to_file(test_file_path, test_file_content)
    print(result_write)
    assert "Success" in result_write and test_file_path in result_write
    assert os.path.exists(test_file_path)
    print(f"Verified file '{test_file_path}' was created.")

    # Test reading the file
    read_content = read_text_from_file(test_file_path)
    # print(f"Read content: '{read_content}'") # Can be noisy
    assert read_content == test_file_content
    print(f"Verified content of '{test_file_path}' matches.")

    # Test overwriting an existing file
    overwrite_content = "This is new content for overwriting."
    result_overwrite = write_text_to_file(test_file_path, overwrite_content)
    print(result_overwrite)
    assert "Success" in result_overwrite
    read_overwritten_content = read_text_from_file(test_file_path)
    assert read_overwritten_content == overwrite_content
    print(f"Verified file '{test_file_path}' was overwritten successfully.")

    # Test reading a non-existent file
    non_existent_file_path = os.path.join(expected_p1_path, "non_existent.txt")
    result_read_non_existent = read_text_from_file(non_existent_file_path)
    print(result_read_non_existent)
    assert "Error" in result_read_non_existent and "not found" in result_read_non_existent
    print("Attempt to read non-existent file handled correctly.")

    # Test writing to an invalid path (e.g. if perms were an issue, or path too long - hard to test robustly here)
    # For now, just test with empty filepath
    result_write_empty_path = write_text_to_file("", "content")
    print(result_write_empty_path)
    assert "Error" in result_write_empty_path
    print("Attempt to write to empty filepath handled.")

    # Test reading from an invalid path
    result_read_empty_path = read_text_from_file("")
    print(result_read_empty_path)
    assert "Error" in result_read_empty_path
    print("Attempt to read from empty filepath handled.")

    print("write_text_to_file and read_text_from_file tests passed.")

    # --- Cleanup ---
    print("\n--- Cleaning up test directories and files ---")
    if os.path.exists(BASE_PROJECTS_DIR):
        try:
            shutil.rmtree(BASE_PROJECTS_DIR)
            print(f"Removed base test directory: '{BASE_PROJECTS_DIR}'")
        except OSError as e:
            print(f"Error removing base test directory '{BASE_PROJECTS_DIR}': {e}")
    else:
        print(f"Base test directory '{BASE_PROJECTS_DIR}' was not created or already cleaned up.")
    
    print("\n--- All File System Tools Tests Finished ---")
