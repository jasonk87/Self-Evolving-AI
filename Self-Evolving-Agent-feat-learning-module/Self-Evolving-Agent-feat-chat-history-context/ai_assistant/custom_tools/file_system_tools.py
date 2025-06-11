# ai_assistant/custom_tools/file_system_tools.py
import os
import re
from typing import Union, Optional, Dict, Any, List # Added Optional, Dict, Any, List

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

def list_project_files(project_identifier: str, sub_directory: Optional[str] = None) -> Dict[str, Any]:
    """
    Lists files and directories within a specified project's root path or a subdirectory thereof.

    Args:
        project_identifier: The ID or name of the project.
        sub_directory: Optional. A subdirectory within the project to list.
                       If None, lists contents of the project's root_path.

    Returns:
        A dictionary with "status": "success", "path_listed": "absolute_path",
        "files": list_of_files, "directories": list_of_directories.
        Or {"status": "error", "message": "error description"}.
    """
    from ai_assistant.core.project_manager import find_project # Delayed import to avoid circularity if models are in same dir

    project = find_project(project_identifier)
    if not project:
        return {"status": "error", "message": f"Project '{project_identifier}' not found."}

    root_path = project.get("root_path")
    if not root_path:
        return {"status": "error", "message": f"Project '{project_identifier}' (ID: {project.get('project_id')}) does not have a root_path defined."}

    if not os.path.isdir(root_path): # Ensure stored root_path is actually a directory
            return {"status": "error", "message": f"Project root path '{root_path}' for '{project_identifier}' is not a valid directory."}

    path_to_list = os.path.abspath(root_path) # Start with absolute root path

    if sub_directory:
        prospective_path = os.path.abspath(os.path.normpath(os.path.join(path_to_list, sub_directory)))
        if os.path.commonpath([path_to_list, prospective_path]) != path_to_list:
            return {"status": "error", "message": f"Subdirectory '{sub_directory}' attempts to traverse outside project root '{path_to_list}'."}
        path_to_list = prospective_path

    if not os.path.isdir(path_to_list):
            return {"status": "error", "message": f"Target path '{path_to_list}' is not a valid directory."}

    try:
        entries = os.listdir(path_to_list)
        files = [entry for entry in entries if os.path.isfile(os.path.join(path_to_list, entry))]
        directories = [entry for entry in entries if os.path.isdir(os.path.join(path_to_list, entry))]

        return {
            "status": "success",
            "path_listed": path_to_list,
            "files": sorted(files),
            "directories": sorted(directories)
        }
    except FileNotFoundError: # pragma: no cover
        return {"status": "error", "message": f"Path not found: {path_to_list}"}
    except PermissionError: # pragma: no cover
        return {"status": "error", "message": f"Permission denied to list directory: {path_to_list}"}
    except Exception as e: # pragma: no cover
        return {"status": "error", "message": f"Failed to list project files for '{project_identifier}' at '{path_to_list}': {str(e)}"}

def get_project_file_content(project_identifier: str, file_path_in_project: str) -> Dict[str, Any]:
    """
    Reads the content of a specified file within a project.

    Args:
        project_identifier: The ID or name of the project.
        file_path_in_project: The relative path to the file within the project's root directory.

    Returns:
        A dictionary with "status": "success", "file_path": "absolute_path", "content": "file_content".
        Or {"status": "error", "message": "error description"}.
    """
    from ai_assistant.core.project_manager import find_project # Delayed import

    project = find_project(project_identifier)
    if not project:
        return {"status": "error", "message": f"Project '{project_identifier}' not found."}

    root_path = project.get("root_path")
    if not root_path:
        return {"status": "error", "message": f"Project '{project_identifier}' (ID: {project.get('project_id')}) does not have a root_path defined."}

    if not os.path.isdir(root_path):
        return {"status": "error", "message": f"Project root path '{root_path}' for '{project_identifier}' is not a valid directory."}

    target_file_path = os.path.abspath(os.path.normpath(os.path.join(root_path, file_path_in_project)))

    if os.path.commonpath([root_path, target_file_path]) != root_path:
        return {"status": "error", "message": f"File path '{file_path_in_project}' attempts to traverse outside project root."}

    if not os.path.exists(target_file_path):
        return {"status": "error", "message": f"File not found at '{target_file_path}'."}

    if not os.path.isfile(target_file_path):
        return {"status": "error", "message": f"Path '{target_file_path}' is a directory, not a file."}

    try:
        with open(target_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {
            "status": "success",
            "file_path": target_file_path,
            "content": content
        }
    except PermissionError: # pragma: no cover
        return {"status": "error", "message": f"Permission denied to read file: {target_file_path}"}
    except IOError as e: # pragma: no cover
            return {"status": "error", "message": f"IOError reading file {target_file_path}: {str(e)}"}
    except Exception as e: # pragma: no cover
        return {"status": "error", "message": f"Failed to read project file '{file_path_in_project}' from '{project_identifier}': {str(e)}"}

if __name__ == '__main__':
    import shutil
    import tempfile
    from unittest.mock import patch

    print("--- Testing File System Tools ---")

    print("\n--- Testing sanitize_project_name ---")
    test_names = {
        "My Awesome Hangman Game!": "my_awesome_hangman_game",
        "Project with spaces": "project_with_spaces",
        "project-with-hyphens": "project_with_hyphens",
        "Project_With_Underscores": "project_with_underscores",
        "Th!s h@s $pec!@l ch@r$": "thshs_pecl_chr",
        "  leading and trailing spaces  ": "leading_and_trailing_spaces",
        "---multiple---hyphens---": "multiple_hyphens",
        "__": "unnamed_project",
        "": "unnamed_project",
        "a"*60: "a"*50,
        "Valid-Name_123": "valid-name_123"
    }
    for original, expected in test_names.items():
        sanitized = sanitize_project_name(original)
        print(f"Original: '{original}' -> Sanitized: '{sanitized}' (Expected: '{expected}')")
        assert sanitized == expected, f"Sanitization failed for '{original}'"
    print("sanitize_project_name tests passed.")

    print("\n--- Testing create_project_directory ---")
    project1_name = "My Test Project Alpha"
    sanitized_p1_name = sanitize_project_name(project1_name)
    expected_p1_path = os.path.join(BASE_PROJECTS_DIR, sanitized_p1_name)

    result_create1 = create_project_directory(project1_name)
    print(result_create1)
    assert "Success" in result_create1 and expected_p1_path in result_create1
    assert os.path.exists(expected_p1_path) and os.path.isdir(expected_p1_path)
    print(f"Verified directory '{expected_p1_path}' exists.")

    result_create_existing = create_project_directory(project1_name)
    print(result_create_existing)
    assert "Error" in result_create_existing and "already exists" in result_create_existing
    print("Attempt to create existing directory handled correctly.")
    
    result_empty_name = create_project_directory("")
    print(result_empty_name)
    assert "Error" in result_empty_name or "unnamed_project" in sanitize_project_name("")
    if "Success" in result_empty_name :
        assert os.path.exists(os.path.join(BASE_PROJECTS_DIR, "unnamed_project"))
    print("Empty project name test handled.")

    print("create_project_directory tests passed.")

    print("\n--- Testing write_text_to_file and read_text_from_file ---")
    test_file_content = "Hello, this is a test file.\nIt has multiple lines.\nEnd of test."
    test_file_path = os.path.join(expected_p1_path, "test_file.txt")
    
    result_write = write_text_to_file(test_file_path, test_file_content)
    print(result_write)
    assert "Success" in result_write and test_file_path in result_write
    assert os.path.exists(test_file_path)
    print(f"Verified file '{test_file_path}' was created.")

    read_content = read_text_from_file(test_file_path)
    assert read_content == test_file_content
    print(f"Verified content of '{test_file_path}' matches.")

    overwrite_content = "This is new content for overwriting."
    result_overwrite = write_text_to_file(test_file_path, overwrite_content)
    print(result_overwrite)
    assert "Success" in result_overwrite
    read_overwritten_content = read_text_from_file(test_file_path)
    assert read_overwritten_content == overwrite_content
    print(f"Verified file '{test_file_path}' was overwritten successfully.")

    non_existent_file_path = os.path.join(expected_p1_path, "non_existent.txt")
    result_read_non_existent = read_text_from_file(non_existent_file_path)
    print(result_read_non_existent)
    assert "Error" in result_read_non_existent and "not found" in result_read_non_existent
    print("Attempt to read non-existent file handled correctly.")

    result_write_empty_path = write_text_to_file("", "content")
    print(result_write_empty_path)
    assert "Error" in result_write_empty_path
    print("Attempt to write to empty filepath handled.")

    result_read_empty_path = read_text_from_file("")
    print(result_read_empty_path)
    assert "Error" in result_read_empty_path
    print("Attempt to read from empty filepath handled.")

    print("write_text_to_file and read_text_from_file tests passed.")

    print("\n--- Cleaning up test directories and files ---")
    if os.path.exists(BASE_PROJECTS_DIR):
        try:
            shutil.rmtree(BASE_PROJECTS_DIR)
            print(f"Removed base test directory: '{BASE_PROJECTS_DIR}'")
        except OSError as e:
            print(f"Error removing base test directory '{BASE_PROJECTS_DIR}': {e}")
    else:
        print(f"Base test directory '{BASE_PROJECTS_DIR}' was not created or already cleaned up.")

    print("\n--- Testing list_project_files ---")

    _test_temp_root_dir_for_list_files = tempfile.TemporaryDirectory()

    mock_project_data_with_path = {"project_id": "p1_list_test", "name": "ListTestProject",
                                   "root_path": os.path.join(_test_temp_root_dir_for_list_files.name, "ListTestProjectRoot")}
    mock_project_data_no_path = {"project_id": "p2_list_test", "name": "NoPathListProject", "root_path": None}
    mock_project_data_invalid_path = {"project_id": "p3_list_test", "name": "InvalidPathListProject",
                                      "root_path": os.path.join(_test_temp_root_dir_for_list_files.name, "file_instead_of_dir.txt")}

    os.makedirs(mock_project_data_with_path["root_path"], exist_ok=True)
    with open(os.path.join(mock_project_data_with_path["root_path"], "file1.txt"), "w") as f: f.write("f1")
    os.makedirs(os.path.join(mock_project_data_with_path["root_path"], "subdir1"), exist_ok=True)
    with open(os.path.join(mock_project_data_with_path["root_path"], "subdir1", "file2.txt"), "w") as f: f.write("f2")

    with open(mock_project_data_invalid_path["root_path"], "w") as f: f.write("I am a file, not a directory.")


    def mock_find_project_list_files(identifier):
        if identifier == "ListTestProject" or identifier == "p1_list_test":
            return mock_project_data_with_path
        if identifier == "NoPathListProject": return mock_project_data_no_path
        if identifier == "InvalidPathListProject": return mock_project_data_invalid_path
        return None

    with patch('ai_assistant.custom_tools.file_system_tools.find_project', side_effect=mock_find_project_list_files):
        print("\nTest 1: List project root (ListTestProject)")
        result1 = list_project_files("ListTestProject")
        print(f"Result 1: {result1}")
        assert result1["status"] == "success"
        assert "file1.txt" in result1["files"]
        assert "subdir1" in result1["directories"]
        assert os.path.isabs(result1["path_listed"])

        print("\nTest 2: List subdirectory (ListTestProject/subdir1)")
        result2 = list_project_files("ListTestProject", sub_directory="subdir1")
        print(f"Result 2: {result2}")
        assert result2["status"] == "success"
        assert "file2.txt" in result2["files"]
        assert not result2["directories"]

        print("\nTest 3: List non-existent subdirectory (ListTestProject/non_existent_subdir)")
        result3 = list_project_files("ListTestProject", sub_directory="non_existent_subdir")
        print(f"Result 3: {result3}")
        assert result3["status"] == "error"
        assert "not a valid directory" in result3["message"].lower()

        print("\nTest 4: Path traversal attempt (ListTestProject/../outside)")
        result4 = list_project_files("ListTestProject", sub_directory="../../outside_project")
        print(f"Result 4: {result4}")
        assert result4["status"] == "error"
        assert "traverse outside project root" in result4["message"].lower()

        print("\nTest 5: Project with no root_path (NoPathListProject)")
        result5 = list_project_files("NoPathListProject")
        print(f"Result 5: {result5}")
        assert result5["status"] == "error"
        assert "does not have a root_path defined" in result5["message"].lower()

        print("\nTest 6: Project not found (FakeListProject)")
        result6 = list_project_files("FakeListProject")
        print(f"Result 6: {result6}")
        assert result6["status"] == "error"
        assert "not found" in result6["message"].lower()

        print("\nTest 7: Project with invalid (non-dir) root_path (InvalidPathListProject)")
        result7 = list_project_files("InvalidPathListProject")
        print(f"Result 7: {result7}")
        assert result7["status"] == "error"
        assert "not a valid directory" in result7["message"].lower()

        print("\n--- Testing get_project_file_content ---")
        print("\nTest GFC 1: Read file in project root (ListTestProject/file1.txt)")
        content_res1 = get_project_file_content("ListTestProject", "file1.txt")
        print(f"Content Result 1: {content_res1.get('status')}")
        assert content_res1["status"] == "success"
        assert content_res1["content"] == "f1"
        assert content_res1["file_path"] == os.path.join(mock_project_data_with_path["root_path"], "file1.txt")

        print("\nTest GFC 2: Read file in subdir (ListTestProject/subdir1/file2.txt)")
        content_res2 = get_project_file_content("ListTestProject", "subdir1/file2.txt")
        print(f"Content Result 2: {content_res2.get('status')}")
        assert content_res2["status"] == "success"
        assert content_res2["content"] == "f2"

        print("\nTest GFC 3: Read non-existent file (ListTestProject/non_existent.txt)")
        content_res3 = get_project_file_content("ListTestProject", "non_existent.txt")
        print(f"Content Result 3: {content_res3}")
        assert content_res3["status"] == "error"
        assert "not found" in content_res3["message"].lower()

        print("\nTest GFC 4: Path traversal read (ListTestProject/../../outside.txt)")
        content_res4 = get_project_file_content("ListTestProject", "../../outside_project_file.txt")
        print(f"Content Result 4: {content_res4}")
        assert content_res4["status"] == "error"
        assert "traverse outside project root" in content_res4["message"].lower()

        print("\nTest GFC 5: Read a directory (ListTestProject/subdir1)")
        content_res5 = get_project_file_content("ListTestProject", "subdir1")
        print(f"Content Result 5: {content_res5}")
        assert content_res5["status"] == "error"
        assert "is a directory, not a file" in content_res5["message"].lower()

        print("\nTest GFC 6: Project with no root_path (NoPathListProject)")
        content_res6 = get_project_file_content("NoPathListProject", "anyfile.txt")
        print(f"Content Result 6: {content_res6}")
        assert content_res6["status"] == "error"
        assert "does not have a root_path defined" in content_res6["message"].lower()

        print("\nTest GFC 7: Project not found (FakeProjectToRead)")
        content_res7 = get_project_file_content("FakeProjectToRead", "anyfile.txt")
        print(f"Content Result 7: {content_res7}")
        assert content_res7["status"] == "error"
        assert "not found" in content_res7["message"].lower()

    _test_temp_root_dir_for_list_files.cleanup()
    print(f"Cleaned up temp directory for list_project_files: {_test_temp_root_dir_for_list_files.name}")
    
    print("\n--- All File System Tools Tests Finished ---")
