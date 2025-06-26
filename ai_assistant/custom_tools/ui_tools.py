# ai_assistant/custom_tools/ui_tools.py
from typing import Dict, Any

def display_html_content_in_project_area(html_content: str) -> Dict[str, Any]:
    """
    Signals that the provided HTML content should be displayed in the dedicated
    project display area of the UI, rather than as a standard chat message.

    Args:
        html_content: A string containing the HTML to be displayed.

    Returns:
        A dictionary indicating the action type and the HTML content.
    """
    if not isinstance(html_content, str):
        return {
            "type": "html_display_error",
            "error": "Invalid content type. html_content must be a string."
        }

    # The orchestrator will look for this specific structure to handle it differently.
    return {
        "type": "html_display_content", # Special type for orchestrator to recognize
        "content": html_content
    }

DISPLAY_HTML_CONTENT_IN_PROJECT_AREA_SCHEMA = { # Renamed to match expected pattern
    "name": "display_html_content_in_project_area",
    "description": "Use this tool to render HTML content directly within the main Project Display Area of the user interface. This is for displaying rich content, web pages, or interactive elements, not for chat messages.",
    "parameters": [
        {"name": "html_content", "type": "str", "description": "The raw HTML string to be rendered in the project display area."}
    ],
    "returns": {
        "type": "dict",
        "description": "A dictionary confirming the action, containing the HTML content. The UI will handle rendering."
    }
}

def modify_displayed_html_content(search_pattern: str, replacement_code: str, occurrence_index: int = 0) -> Dict[str, Any]:
    """
    Signals a request to modify the currently displayed HTML content in the project area.
    This tool does not perform the modification itself but returns a structured request
    for the orchestrator to process. The orchestrator will apply the change to its stored
    version of the displayed code and then trigger a UI update.

    Args:
        search_pattern: The string or regex pattern to search for in the current HTML.
                        For simple cases, this can be a literal substring of the code to be replaced.
        replacement_code: The new code snippet to replace the found pattern.
        occurrence_index: Specifies which occurrence of the search_pattern to replace.
                          Default is 0, meaning the first occurrence.
                          (Note: Current orchestrator logic might simplify this to 'first' or 'all'
                           based on Python's string replace or re.sub capabilities).

    Returns:
        A dictionary structured to represent the modification request.
    """
    if not isinstance(search_pattern, str) or not search_pattern:
        return {
            "type": "html_modification_error",
            "error": "Invalid search_pattern. Must be a non-empty string."
        }
    if not isinstance(replacement_code, str): # Allow empty string for replacement (deletion)
        return {
            "type": "html_modification_error",
            "error": "Invalid replacement_code. Must be a string."
        }
    if not isinstance(occurrence_index, int) or occurrence_index < 0:
        # For now, only supporting 0 (first) effectively.
        # Could be expanded: occurrence_index < -1 for "all" (if using re.sub without count=1)
        # or specific positive indices for Nth occurrence (more complex).
        # Let's keep it simple for now and primarily support first occurrence.
        # The orchestrator will likely use str.replace(search, replace, 1) for occurrence_index=0.
        if occurrence_index != 0:
             return {
                "type": "html_modification_error",
                "error": "Invalid occurrence_index. Currently, only 0 (for the first occurrence) is robustly supported."
            }

    return {
        "type": "html_modification_request",
        "search_pattern": search_pattern,
        "replacement_code": replacement_code,
        "occurrence_index": occurrence_index # Pass it along; orchestrator will interpret
    }

MODIFY_DISPLAYED_HTML_CONTENT_SCHEMA = {
    "name": "modify_displayed_html_content",
    "description": (
        "Use this tool to request a targeted modification (search and replace) of the HTML, CSS, or JavaScript content "
        "currently shown in the Project Display Area. This is for small, specific changes. "
        "Provide a pattern to search for and the code to replace it with. "
        "The AI should refer to the 'Currently Displayed Project Code' context to formulate the search_pattern accurately."
    ),
    "parameters": [
        {"name": "search_pattern", "type": "str", "description": "The exact string or a unique snippet of the code in the current display that needs to be found and replaced."},
        {"name": "replacement_code", "type": "str", "description": "The new code snippet that will replace the text matched by search_pattern."},
        {"name": "occurrence_index", "type": "int", "description": "Optional. Specifies which occurrence to replace if the search_pattern matches multiple times. Default is 0 (the first one).", "default": 0}
    ],
    "returns": {
        "type": "dict",
        "description": "A dictionary representing the modification request for the orchestrator to process."
    }
}


# It's good practice to have a way to list tools from this module for registration
def get_tools_in_module():
    return [
        ("display_html_content_in_project_area", display_html_content_in_project_area, DISPLAY_HTML_CONTENT_IN_PROJECT_AREA_SCHEMA), # Corrected schema name
        ("modify_displayed_html_content", modify_displayed_html_content, MODIFY_DISPLAYED_HTML_CONTENT_SCHEMA),
    ]
