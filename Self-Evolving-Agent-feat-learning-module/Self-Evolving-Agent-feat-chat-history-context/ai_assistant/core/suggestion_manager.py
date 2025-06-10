import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from ai_assistant.config import get_data_dir
from ai_assistant.utils.display_utils import CLIColors, color_text

SUGGESTIONS_FILE_NAME = "suggestions.json"

def get_suggestions_file_path() -> str:
    return os.path.join(get_data_dir(), SUGGESTIONS_FILE_NAME)

def _load_suggestions() -> List[Dict[str, Any]]:
    """Loads suggestions from the JSON file."""
    filepath = get_suggestions_file_path()
    if not os.path.exists(filepath):
        # Create a dummy suggestions file if it doesn't exist for demo purposes
        print(color_text(f"Suggestions file not found at {filepath}. Creating a dummy file.", CLIColors.SYSTEM_MESSAGE))
        dummy_suggestions = [
            {
                "suggestion_id": str(uuid.uuid4()),
                "type": "tool_improvement",
                "description": "Consider adding a 'search_web_archive' tool for historical data.",
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "reason_for_status": ""
            },
            {
                "suggestion_id": str(uuid.uuid4()),
                "type": "fact_learning",
                "description": "The agent could learn common shell commands and their uses.",
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "reason_for_status": ""
            }
        ]
        _save_suggestions(dummy_suggestions)
        return dummy_suggestions
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content:
                return []
            return json.loads(content)
    except (IOError, json.JSONDecodeError) as e:
        print(color_text(f"Error loading suggestions: {e}", CLIColors.ERROR_MESSAGE))
        return []

def _save_suggestions(suggestions: List[Dict[str, Any]]) -> bool:
    """Saves suggestions to the JSON file."""
    filepath = get_suggestions_file_path()
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(suggestions, f, indent=4)
        return True
    except IOError as e:
        print(color_text(f"Error saving suggestions: {e}", CLIColors.ERROR_MESSAGE))
        return False

def list_suggestions() -> List[Dict[str, Any]]:
    """Returns a list of all suggestions."""
    return _load_suggestions()

def find_suggestion(suggestion_id: str) -> Optional[Dict[str, Any]]:
    """Finds a suggestion by its ID."""
    suggestions = _load_suggestions()
    for suggestion in suggestions:
        if suggestion['suggestion_id'] == suggestion_id:
            return suggestion
    return None

def _update_suggestion_status(suggestion_id: str, new_status: str, reason: Optional[str] = None) -> bool:
    """Internal helper to update suggestion status."""
    suggestions = _load_suggestions()
    suggestion_found = False
    for suggestion in suggestions:
        if suggestion['suggestion_id'] == suggestion_id:
            suggestion['status'] = new_status
            suggestion['reason_for_status'] = reason or suggestion.get('reason_for_status', '')
            suggestion['updated_at'] = datetime.now(timezone.utc).isoformat()
            suggestion_found = True
            break
    
    if not suggestion_found:
        print(color_text(f"Suggestion with ID '{suggestion_id}' not found.", CLIColors.ERROR_MESSAGE))
        return False

    if _save_suggestions(suggestions):
        print(color_text(f"Status of suggestion '{suggestion_id}' updated to '{new_status}'.", CLIColors.SUCCESS))
        return True
    return False

def approve_suggestion(suggestion_id: str, reason: Optional[str] = None) -> bool:
    """Approves a suggestion."""
    return _update_suggestion_status(suggestion_id, "approved", reason)

def deny_suggestion(suggestion_id: str, reason: Optional[str] = None) -> bool:
    """Denies a suggestion."""
    return _update_suggestion_status(suggestion_id, "denied", reason)

def get_suggestions_summary_status() -> str:
    """Returns a summary string of all suggestion statuses."""
    suggestions = _load_suggestions()
    if not suggestions:
        return "No suggestions found."
    
    status_counts: Dict[str, int] = {}
    for suggestion in suggestions:
        status = suggestion.get('status', 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1
    
    summary_lines = [f"Total Suggestions: {len(suggestions)}"]
    for status, count in status_counts.items():
        summary_lines.append(f"  - {status.capitalize()}: {count}")
    return "\n".join(summary_lines)

# Example of how a new suggestion might be added internally by the system
def add_new_suggestion(type: str, description: str) -> Optional[Dict[str, Any]]:
    """Adds a new suggestion to the system (typically called by AI components)."""
    suggestions = _load_suggestions()
    
    new_suggestion = {
        "suggestion_id": str(uuid.uuid4()),
        "type": type,
        "description": description,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reason_for_status": ""
    }
    suggestions.append(new_suggestion)
    if _save_suggestions(suggestions):
        # This print might be too verbose if called frequently by AI, consider logging
        # print(color_text(f"New suggestion '{new_suggestion['suggestion_id']}' added.", CLIColors.SYSTEM_MESSAGE))
        return new_suggestion
    return None
