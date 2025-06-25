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

# It's good practice to have a way to list tools from this module for registration
def get_tools_in_module():
    return [
        ("display_html_content_in_project_area", display_html_content_in_project_area, DISPLAY_HTML_CONTENT_SCHEMA),
    ]
