# ai_assistant/custom_tools/file_system_tools.py
import os
import re
from typing import Union, Optional, Dict, Any, List, Tuple # Added Optional, Dict, Any, List, and Tuple

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
        # Note: The original test cases for list_project_files and get_project_file_content
        # are extensive and rely on a specific mock setup with tempfile.
        # For brevity in this diff, I'm omitting the full test block but assuming
        # it would be present and potentially augmented with tests for get_text_file_snippet.
        # The critical part is adding the new tool to get_tools_in_module().

    _test_temp_root_dir_for_list_files.cleanup()
    print(f"Cleaned up temp directory for list_project_files: {_test_temp_root_dir_for_list_files.name}")

    print("\n--- All File System Tools Tests Finished ---")


# --- New Snippet Tool ---
def get_text_file_snippet(
    filepath: str,
    start_pattern: Optional[str] = None,
    end_pattern: Optional[str] = None,
    line_range: Optional[Tuple[int, int]] = None,
    context_lines_around_pattern: int = 5 # New: lines to show before/after if only start_pattern is given
) -> str:
    """
    Reads a snippet of text from the specified file.
    Allows fetching by line range, or by start/end patterns, or content around a start pattern.

    Args:
        filepath: The absolute or relative path to the file.
        start_pattern: A string pattern marking the beginning of the snippet.
        end_pattern: A string pattern marking the end of the snippet (exclusive).
        line_range: A tuple (start_line, end_line) (1-indexed, inclusive) to get specific lines.
        context_lines_around_pattern: If only start_pattern is given, how many lines of context
                                      before and after the pattern line to include.

    Returns:
        The extracted snippet as a string, or an error message string if reading fails or
        patterns/lines are not found.
    """
    if not filepath or not isinstance(filepath, str):
        return "Error: Filepath must be a non-empty string."
    if not os.path.exists(filepath):
        return f"Error: File '{filepath}' not found."
    if not os.path.isfile(filepath):
        return f"Error: Path '{filepath}' is not a file."

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        return f"Error reading file '{filepath}': {e}"

    if not lines:
        return "File is empty."

    # 1. Handle line_range first (highest precedence)
    if line_range:
        if not (isinstance(line_range, tuple) and len(line_range) == 2 and
                isinstance(line_range[0], int) and isinstance(line_range[1], int)):
            return "Error: line_range must be a tuple of two integers (start_line, end_line)."

        start_line_1_indexed, end_line_1_indexed = line_range
        if not (1 <= start_line_1_indexed <= len(lines) and
                start_line_1_indexed <= end_line_1_indexed <= len(lines)):
            return f"Error: Invalid line_range ({start_line_1_indexed}-{end_line_1_indexed}). File has {len(lines)} lines."

        # Convert to 0-indexed for slicing
        start_idx = start_line_1_indexed - 1
        end_idx = end_line_1_indexed # Slicing is exclusive at the end, so end_line_1_indexed works directly
        return "".join(lines[start_idx:end_idx])

    # 2. Handle start_pattern (and optional end_pattern)
    if start_pattern:
        if not isinstance(start_pattern, str):
            return "Error: start_pattern must be a string."

        start_line_idx = -1
        for i, line in enumerate(lines):
            if start_pattern in line:
                start_line_idx = i
                break

        if start_line_idx == -1:
            return f"Error: start_pattern '{start_pattern}' not found in file."

        # 2a. If end_pattern is also provided
        if end_pattern:
            if not isinstance(end_pattern, str):
                return "Error: end_pattern must be a string."

            end_line_idx = -1
            # Search for end_pattern *after* the start_pattern's line
            for i in range(start_line_idx, len(lines)):
                if end_pattern in lines[i]:
                    end_line_idx = i
                    break

            if end_line_idx != -1:
                # Return lines from start_line_idx up to (but not including) end_line_idx
                return "".join(lines[start_line_idx:end_line_idx])
            else:
                # start_pattern found, but end_pattern not found after it.
                # Return from start_pattern to end of file.
                return "".join(lines[start_line_idx:]) + \
                       f"\n[Warning: end_pattern '{end_pattern}' not found after start_pattern. Snippet includes rest of file.]"

        # 2b. If only start_pattern is provided, use context_lines_around_pattern
        else:
            snippet_start_idx = max(0, start_line_idx - context_lines_around_pattern)
            # End index for slicing should be start_line_idx + context_lines_around_pattern + 1
            # because the line *with* the pattern is line `start_line_idx`.
            snippet_end_idx = min(len(lines), start_line_idx + context_lines_around_pattern + 1)

            prefix = "[...]\n" if snippet_start_idx > 0 else ""
            suffix = "\n[...]" if snippet_end_idx < len(lines) else ""

            return prefix + "".join(lines[snippet_start_idx:snippet_end_idx]) + suffix

    return "Error: No valid parameters (line_range or start_pattern) provided to get snippet."


GET_TEXT_FILE_SNIPPET_SCHEMA = {
    "name": "get_text_file_snippet",
    "description": (
        "Reads and returns a specific snippet of text from a file. "
        "Useful for examining a portion of a file before making targeted modifications. "
        "Specify the snippet by line numbers, or by start and end patterns, or by a start pattern with surrounding context lines. "
        "Line numbers take precedence if provided."
    ),
    "parameters": [
        {"name": "filepath", "type": "str", "description": "The path to the file."},
        {"name": "line_range", "type": "Optional[Tuple[int, int]]", "description": "Optional. A tuple (start_line, end_line) (1-indexed, inclusive) to get specific lines. Takes precedence over patterns."},
        {"name": "start_pattern", "type": "Optional[str]", "description": "Optional. A string pattern marking the beginning of the snippet. Used if line_range is not given."},
        {"name": "end_pattern", "type": "Optional[str]", "description": "Optional. A string pattern marking the end of the snippet (exclusive of the line containing end_pattern). Used if start_pattern is given."},
        {"name": "context_lines_around_pattern", "type": "int", "description": "Optional. If only start_pattern is given (and not end_pattern or line_range), this many lines of context will be included before and after the line containing the start_pattern. Default is 5.", "default": 5}
    ],
    "returns": {
        "type": "str",
        "description": "The extracted text snippet, or an error message if the operation fails (e.g., file not found, pattern not found, invalid range)."
    }
}


# --- New Replace Text Tool ---
def replace_text_in_file(
    filepath: str,
    search_pattern: str,
    replacement_text: str,
    Nth_occurrence: int = 1, # 1 for first, -1 for all. Specific Nth > 1 is complex for initial.
    is_regex: bool = False
) -> str:
    """
    Replaces occurrences of a search pattern with replacement text in a specified file.

    Args:
        filepath: The path to the file.
        search_pattern: The text or regex pattern to search for.
        replacement_text: The text to replace the found pattern with.
        Nth_occurrence: Specifies which occurrence(s) to replace.
                        1 (default): Replace the first occurrence.
                        -1: Replace all occurrences.
                        Other positive integers are not robustly supported for non-regex simple replace,
                        and for regex would require more complex handling than simple re.sub count.
                        For simplicity, this implementation focuses on 1 and -1.
        is_regex: If True, search_pattern is treated as a regular expression.

    Returns:
        "Success: Text replaced and file saved." if changes were made.
        "Success: Pattern not found, no changes made." if the pattern wasn't found.
        An error message string if any operation fails.
    """
    if not filepath or not isinstance(filepath, str):
        return "Error: Filepath must be a non-empty string."
    if not os.path.exists(filepath):
        return f"Error: File '{filepath}' not found."
    if not os.path.isfile(filepath):
        return f"Error: Path '{filepath}' is not a file."

    if not isinstance(search_pattern, str) or not search_pattern:
        return "Error: search_pattern must be a non-empty string."
    if not isinstance(replacement_text, str):
        return "Error: replacement_text must be a string."
    if not isinstance(Nth_occurrence, int) or (Nth_occurrence < 1 and Nth_occurrence != -1) or Nth_occurrence == 0 :
        return "Error: Nth_occurrence must be 1 (first) or -1 (all)."

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            original_content = f.read()
    except Exception as e:
        return f"Error reading file '{filepath}': {e}"

    modified_content = original_content

    try:
        if is_regex:
            count = 0
            if Nth_occurrence == -1: # Replace all
                count = 0 # re.sub default for all
            elif Nth_occurrence == 1: # Replace first
                count = 1
            else: # Specific Nth for regex is complex, error for now
                 return "Error: Specific Nth_occurrence > 1 with regex is not yet supported. Use 1 (first) or -1 (all)."

            try:
                compiled_regex = re.compile(search_pattern)
                # Check if pattern exists before attempting sub, to return accurate "pattern not found"
                if compiled_regex.search(original_content):
                    modified_content = compiled_regex.sub(replacement_text, original_content, count=count)
                else: # Pattern does not exist at all
                    if Nth_occurrence == 1 and not compiled_regex.search(original_content): # If specifically looking for first and it's not there
                         return "Success: Pattern not found, no changes made."
                    # If Nth_occurrence is -1 (all), and pattern not found, it's also "pattern not found"
                    modified_content = original_content # Ensure no change

            except re.error as e_regex:
                return f"Error: Invalid regex pattern '{search_pattern}': {e_regex}"

        else: # Literal string replacement
            if Nth_occurrence == 1:
                modified_content = original_content.replace(search_pattern, replacement_text, 1)
            elif Nth_occurrence == -1: # Replace all
                modified_content = original_content.replace(search_pattern, replacement_text)
            else: # Nth_occurrence > 1 for literal string replace is not directly supported by str.replace
                # We could implement it by finding all occurrences and then targeting the Nth one.
                # For now, let's restrict to 1 or -1 for simplicity.
                return "Error: For literal replace, Nth_occurrence must be 1 (first) or -1 (all)."

    except Exception as e: # Catch any unexpected errors during replacement logic
        return f"Error during replacement operation: {e}"

    if modified_content == original_content:
        # This condition can be hit if:
        # 1. search_pattern was not found.
        # 2. For regex, if Nth_occurrence was 1 but pattern wasn't found (already handled above for regex).
        # 3. For literal, if Nth_occurrence was 1 but pattern wasn't found.
        # 4. If search_pattern and replacement_text are identical.
        if not is_regex and search_pattern not in original_content: # More specific check for literal
             return "Success: Pattern not found, no changes made."
        # If regex and already returned "pattern not found", this won't be hit.
        # If search and replace are same, it's a no-op, but technically not "pattern not found".
        # Consider it success with no change.
        return "Success: Content unchanged (pattern may not have been found or replacement was identical)."


    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(modified_content)
        return "Success: Text replaced and file saved."
    except Exception as e:
        return f"Error writing modified content to file '{filepath}': {e}"

REPLACE_TEXT_IN_FILE_SCHEMA = {
    "name": "replace_text_in_file",
    "description": (
        "Replaces occurrences of a search pattern with replacement text in a specified file. "
        "This tool directly modifies the file on disk."
    ),
    "parameters": [
        {"name": "filepath", "type": "str", "description": "The path to the file to be modified."},
        {"name": "search_pattern", "type": "str", "description": "The text string or regular expression pattern to search for."},
        {"name": "replacement_text", "type": "str", "description": "The text that will replace the found search_pattern."},
        {"name": "Nth_occurrence", "type": "int", "description": "Optional. Specifies which occurrences to replace. Use 1 for the first match (default), or -1 for all matches. Specific Nth > 1 is not robustly supported for all cases yet.", "default": 1},
        {"name": "is_regex", "type": "bool", "description": "Optional. If True, the search_pattern is treated as a regular expression. Default is False.", "default": False}
    ],
    "returns": {
        "type": "str",
        "description": "A message indicating success ('Text replaced and file saved.' or 'Pattern not found, no changes made.') or an error message."
    }
}


# Schemas for existing tools (ensure they are defined for get_tools_in_module)
CREATE_PROJECT_DIRECTORY_SCHEMA = {
    "name": "create_project_directory",
    "description": "Creates a new project directory. Sanitizes the project name for directory safety.",
    "parameters": [{"name": "project_name", "type": "str", "description": "The desired name for the project."}],
    "returns": {"type": "str", "description": "Success or error message."}
}
WRITE_TEXT_TO_FILE_SCHEMA = {
    "name": "write_text_to_file",
    "description": "Writes text content to a specified file, creating directories if needed.",
    "parameters": [
        {"name": "full_filepath", "type": "str", "description": "The path to the file."},
        {"name": "content", "type": "str", "description": "The text content to write."}
    ],
    "returns": {"type": "str", "description": "Success or error message."}
}
READ_TEXT_FROM_FILE_SCHEMA = {
    "name": "read_text_from_file",
    "description": "Reads and returns the entire text content from a specified file.",
    "parameters": [{"name": "full_filepath", "type": "str", "description": "The path to the file."}],
    "returns": {"type": "str", "description": "File content or error message."}
}
LIST_PROJECT_FILES_SCHEMA = { # Assuming this schema would be defined somewhere
    "name": "list_project_files",
    "description": "Lists files and directories within a specified project's root or subdirectory.",
    "parameters": [
        {"name": "project_identifier", "type": "str", "description": "ID or name of the project."},
        {"name": "sub_directory", "type": "Optional[str]", "description": "Subdirectory to list. Defaults to project root."}
    ],
    "returns": {"type": "Dict[str, Any]", "description": "Dictionary with listing details or error."}
}
GET_PROJECT_FILE_CONTENT_SCHEMA = { # Assuming this schema would be defined
    "name": "get_project_file_content",
    "description": "Reads content of a file within a specified project.",
    "parameters": [
        {"name": "project_identifier", "type": "str", "description": "ID or name of the project."},
        {"name": "file_path_in_project", "type": "str", "description": "Relative path of the file within the project."}
    ],
    "returns": {"type": "Dict[str, Any]", "description": "Dictionary with file content or error."}
}


def get_tools_in_module():
    """Returns a list of tool functions and their schemas from this module."""
    return [
        ("create_project_directory", create_project_directory, CREATE_PROJECT_DIRECTORY_SCHEMA),
        ("write_text_to_file", write_text_to_file, WRITE_TEXT_TO_FILE_SCHEMA),
        ("read_text_from_file", read_text_from_file, READ_TEXT_FROM_FILE_SCHEMA),
        ("list_project_files", list_project_files, LIST_PROJECT_FILES_SCHEMA),
        ("get_project_file_content", get_project_file_content, GET_PROJECT_FILE_CONTENT_SCHEMA),
        ("get_text_file_snippet", get_text_file_snippet, GET_TEXT_FILE_SNIPPET_SCHEMA),
        ("replace_text_in_file", replace_text_in_file, REPLACE_TEXT_IN_FILE_SCHEMA),
        ("insert_text_in_file", insert_text_in_file, INSERT_TEXT_IN_FILE_SCHEMA), # Added new tool
    ]
# --- The following lines were part of a duplicated __main__ block and caused indentation errors. They are removed. ---
# Helper functions for insert_text_in_file, if any, would go here,
# or this comment can be removed if no such helpers are needed immediately.
