import os
from ai_assistant.core.safety.judge import judge
from ai_assistant.custom_tools.file_system_tools import write_text_to_file as custom_write_text

class SecurityViolationError(Exception):
    """Raised when an action is blocked by the Constitutional Judge."""
    pass

def write_file(filepath: str, content: str) -> str:
    """
    Writes content to a file, protected by the Constitutional Judge.

    Args:
        filepath: Path to the file.
        content: Content to write.

    Returns:
        Status string.

    Raises:
        SecurityViolationError: If the action is blocked.
    """
    action_description = f"Overwrite or create file at '{filepath}'."
    code_preview = f"File: {filepath}\nContent Preview (first 100 chars): {content[:100]}..."

    verdict = judge.evaluate_action(action_description, code_preview)

    if verdict.status == "BLOCKED":
        raise SecurityViolationError(f"Action BLOCKED by Constitution: {verdict.reason}")

    # If approved, proceed.
    # We can reuse the existing tool logic or implement minimal version.
    # Since custom_tools/file_system_tools.py handles creation of dirs, etc. let's reuse it or mimic it.
    # But custom_write_text has its own signature and return type.
    # I'll use simple os implementation to avoid circular deps or complex imports if not needed,
    # but reusing is better for consistency.

    return custom_write_text(filepath, content)

def delete_file(filepath: str) -> str:
    """
    Deletes a file, protected by the Constitutional Judge.

    Args:
        filepath: Path to the file.

    Returns:
        Status string.

    Raises:
        SecurityViolationError: If the action is blocked.
    """
    action_description = f"Delete file at '{filepath}'."

    verdict = judge.evaluate_action(action_description, filepath)

    if verdict.status == "BLOCKED":
        raise SecurityViolationError(f"Action BLOCKED by Constitution: {verdict.reason}")

    if not os.path.exists(filepath):
        return f"Error: File '{filepath}' does not exist."

    try:
        os.remove(filepath)
        return f"Success: File '{filepath}' deleted."
    except Exception as e:
        return f"Error deleting file '{filepath}': {e}"
