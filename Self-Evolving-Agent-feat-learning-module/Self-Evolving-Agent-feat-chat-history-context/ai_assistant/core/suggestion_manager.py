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

def mark_suggestion_implemented(suggestion_id: str, reason: Optional[str] = "Successfully implemented.") -> bool:
    """Marks a suggestion as implemented."""
    return _update_suggestion_status(suggestion_id, "implemented", reason)

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

def _normalize_description(description: str) -> str:
    """Normalizes a suggestion description for comparison."""
    return description.lower().strip()

# Example of how a new suggestion might be added internally by the system
def add_new_suggestion(type: str, description: str, source_reflection_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Adds a new suggestion to the system (typically called by AI components).
    Performs deduplication based on normalized description.
    Links to a source reflection ID if provided.
    """
    suggestions = _load_suggestions()
    normalized_new_description = _normalize_description(description)

    for existing_suggestion in suggestions:
        normalized_existing_description = _normalize_description(existing_suggestion.get("description", ""))
        if normalized_new_description == normalized_existing_description:
            print(color_text(f"Duplicate suggestion detected. New: '{description}' matches existing ID '{existing_suggestion['suggestion_id']}' with description '{existing_suggestion['description']}'. Not adding.", CLIColors.INFO_MESSAGE))
            return existing_suggestion # Return the existing one

    new_suggestion = {
        "suggestion_id": str(uuid.uuid4()),
        "type": type,
        "description": description.strip(), # Store the stripped (but not lowercased) version
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reason_for_status": "",
        "source_reflection_id": source_reflection_id # Add this line
    }
    suggestions.append(new_suggestion)
    if _save_suggestions(suggestions):
        # This print might be too verbose if called frequently by AI, consider logging
        # print(color_text(f"New suggestion '{new_suggestion['suggestion_id']}' added.", CLIColors.SYSTEM_MESSAGE))
        return new_suggestion
    return None # Should not happen if _save_suggestions is successful

if __name__ == "__main__": # pragma: no cover
    print("--- Testing Suggestion Manager ---")

    # Clean up suggestions.json before running tests if it exists
    suggestions_file = get_suggestions_file_path()
    if os.path.exists(suggestions_file):
        os.remove(suggestions_file)
        print(f"Removed existing '{SUGGESTIONS_FILE_NAME}' for a clean test run.")

    print("\n--- Listing Suggestions (Initial, after dummy creation) ---")
    initial_loaded_suggestions = list_suggestions() # This will trigger dummy creation if file was removed
    for s in initial_loaded_suggestions:
        print(f"- {s['suggestion_id']}: {s['description'][:50]}... ({s['status']})")

    print("\n--- Adding New Unique Suggestions ---")
    sugg1_desc = "Implement a new search algorithm based on Quantum Entanglement."
    reflection_id_example = "reflection_entry_abc123"
    sugg1 = add_new_suggestion("enhancement", sugg1_desc, source_reflection_id=reflection_id_example)
    if sugg1:
        print(f"Added Sugg1: {sugg1['suggestion_id']} - {sugg1['description']}, linked to reflection: {sugg1.get('source_reflection_id')}")
        assert sugg1.get('source_reflection_id') == reflection_id_example

    sugg2_desc = "   The user interface for tool creation could be simplified.   " # Extra whitespace
    sugg2 = add_new_suggestion("feedback", sugg2_desc) # No reflection ID for this one
    if sugg2:
        print(f"Added Sugg2: {sugg2['suggestion_id']} - {sugg2['description']}, linked to reflection: {sugg2.get('source_reflection_id')}")
        assert sugg2.get('source_reflection_id') is None


    print("\n--- Testing Deduplication ---")
    # Try adding sugg1 again (exact match)
    sugg1_dup = add_new_suggestion("enhancement", sugg1_desc)
    if sugg1_dup and sugg1_dup['suggestion_id'] == sugg1['suggestion_id']:
        print(f"Correctly identified Sugg1 exact duplicate. Returned existing ID: {sugg1_dup['suggestion_id']}")
    elif sugg1_dup:
        print(f"Error: Sugg1 exact duplicate was added as new! New ID: {sugg1_dup['suggestion_id']}")
    else:
        print(f"Error: Sugg1 exact duplicate check returned None unexpectedly.")


    # Try adding sugg2 again (case and whitespace difference)
    sugg2_dup_desc_variant = "the user interface for tool creation could be simplified." # Lowercase, no extra whitespace
    sugg2_dup = add_new_suggestion("feedback", sugg2_dup_desc_variant)
    if sugg2_dup and sugg2_dup['suggestion_id'] == sugg2['suggestion_id']:
        print(f"Correctly identified Sugg2 variant duplicate. Returned existing ID: {sugg2_dup['suggestion_id']}")
    elif sugg2_dup:
        print(f"Error: Sugg2 variant duplicate was added as new! New ID: {sugg2_dup['suggestion_id']}")
    else:
        print(f"Error: Sugg2 variant duplicate check returned None unexpectedly.")


    print("\n--- Listing Suggestions (After Adds & Deduplication Tests) ---")
    final_suggestions = list_suggestions()
    for s in final_suggestions:
        print(f"- {s['suggestion_id']}: {s['description'][:60]}... ({s['status']})")

    # Check that no new suggestions were added for duplicates
    # Initial dummy + sugg1 + sugg2 = expected count
    # The number of initial dummies can vary if _load_suggestions creates them.
    # Let's count based on descriptions added in this test run.
    unique_descs_added_in_run = {sugg1_desc.strip(), sugg2_desc.strip()}
    # Count how many of these are in the final list (should be all of them)
    count_of_our_suggestions = sum(1 for s in final_suggestions if s['description'] in unique_descs_added_in_run)

    print(f"Number of unique suggestions added by this test run found in final list: {count_of_our_suggestions}")
    # This assertion is a bit tricky because of the dummy suggestions.
    # A better check would be to count unique descriptions *before* adding any,
    # then add, then check that the count increased by exactly the number of *unique* new suggestions.
    # For now, visual inspection of the output and the specific duplicate checks above are key.


    if sugg1 and sugg2: # Only proceed if initial suggestions were added
        print("\n--- Testing Status Updates ---")
        approve_suggestion(sugg1['suggestion_id'], "This is a great idea for Q4.")
        deny_suggestion(sugg2['suggestion_id'], "UI simplification is out of scope for current sprint.")

        # Create a third suggestion to mark as implemented
        sugg3_desc = "Document the new API endpoints."
        sugg3 = add_new_suggestion("documentation", sugg3_desc)
        if sugg3:
            print(f"Added Sugg3: {sugg3['suggestion_id']} - {sugg3['description']}")
            mark_suggestion_implemented(sugg3['suggestion_id'], "Documentation has been written and merged.")

        updated_sugg1 = find_suggestion(sugg1['suggestion_id'])
        updated_sugg2 = find_suggestion(sugg2['suggestion_id'])
        updated_sugg3 = find_suggestion(sugg3['suggestion_id']) if sugg3 else None

        if updated_sugg1: print(f"Sugg1 status: {updated_sugg1['status']}, Reason: {updated_sugg1['reason_for_status']}")
        if updated_sugg2: print(f"Sugg2 status: {updated_sugg2['status']}, Reason: {updated_sugg2['reason_for_status']}")
        if updated_sugg3: print(f"Sugg3 status: {updated_sugg3['status']}, Reason: {updated_sugg3['reason_for_status']}")

    print("\n--- Final Suggestions Summary ---")
    print(get_suggestions_summary_status())
    print("\n--- Suggestion Manager Testing Finished ---")
    # print(f"Note: '{SUGGESTIONS_FILE_NAME}' was modified in '{get_data_dir()}'. You may want to delete it.")
