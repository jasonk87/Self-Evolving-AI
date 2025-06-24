import difflib
from typing import List

def generate_diff(old_code: str, new_code: str, file_name: str = "code") -> str:
    """
    Generates a textual diff between old_code and new_code.

    Args:
        old_code: The original code string.
        new_code: The new code string.
        file_name: An optional file name to be used in the diff header.

    Returns:
        A multi-line string representing the unified diff.
        Returns an empty string if inputs are identical.
    """
    if old_code == new_code:
        return ""

    old_code_lines = old_code.splitlines(keepends=True)
    new_code_lines = new_code.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_code_lines,
        new_code_lines,
        fromfile=f"a/{file_name}",
        tofile=f"b/{file_name}",
        lineterm='\n' # Ensure consistent line terminators in diff output
    )
    return "".join(diff)
