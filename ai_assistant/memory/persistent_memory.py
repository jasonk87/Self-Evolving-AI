# Code for persistent memory management.
import json
import os
from typing import Dict, Any, List
import datetime # Added for __main__ tests for ActionableInsights

from ai_assistant.config import get_data_dir # Import the centralized function

def save_goals_to_file(filepath: str, goals_db: Dict[str, Any]) -> bool:
    """
    Serializes the goals_db to JSON and writes it to the specified file.

    Args:
        filepath: The path to the JSON file (e.g., "data/goals.json").
        goals_db: The dictionary of goals to save.

    Returns:
        True on success, False on error.
    """
    try:
        # Ensure the directory exists
        dir_path = os.path.dirname(filepath)
        if dir_path: # Only create if there is a directory part
            os.makedirs(dir_path, exist_ok=True)
            
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(goals_db, f, indent=4, ensure_ascii=False)
        # print(f"Successfully saved goals to {filepath}") # CLI will provide user feedback
        return True
    except IOError as e:
        print(f"IOError saving goals to {filepath}: {e}")
        return False
    except TypeError as e: # For issues with non-serializable content in goals_db
        print(f"TypeError during JSON serialization for {filepath}: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error saving goals to {filepath}: {e}")
        return False

def load_goals_from_file(filepath: str) -> Dict[str, Any]:
    """
    Reads JSON data from the file and deserializes it into a dictionary.

    Args:
        filepath: The path to the JSON file.

    Returns:
        The loaded dictionary of goals. Returns an empty dictionary if the file
        doesn't exist, is invalid JSON, or another error occurs.
    """
    if not os.path.exists(filepath):
        # print(f"Info: Goals file '{filepath}' not found. Starting with an empty goals database.") # Handled by caller
        return {}
        
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            goals_db = json.load(f)
        # print(f"Successfully loaded goals from {filepath}") # CLI will provide user feedback
        return goals_db
    except FileNotFoundError: 
        # print(f"Info: Goals file '{filepath}' not found. Starting with an empty goals database.") # Handled by caller
        return {}
    except json.JSONDecodeError as e:
        print(f"JSONDecodeError loading goals from {filepath}: {e}. Returning empty goals database.")
        return {}
    except IOError as e:
        print(f"IOError loading goals from {filepath}: {e}. Returning empty goals database.")
        return {}
    except Exception as e:
        print(f"Unexpected error loading goals from {filepath}: {e}. Returning empty goals database.")
        return {}

# --- Learned Facts Persistence Functions ---

LEARNED_FACTS_FILENAME = "learned_facts.json"
LEARNED_FACTS_FILEPATH = os.path.join(get_data_dir(), LEARNED_FACTS_FILENAME)

ACTIONABLE_INSIGHTS_FILENAME = "actionable_insights.json"
ACTIONABLE_INSIGHTS_FILEPATH = os.path.join(get_data_dir(), ACTIONABLE_INSIGHTS_FILENAME)


def save_learned_facts(facts: List[Dict[str, Any]], filepath: str = LEARNED_FACTS_FILEPATH) -> bool:
    """
    Serializes the list of learned fact dictionaries to JSON and writes it to file.

    Args:
        facts: The list of learned fact dictionaries.
        filepath: The path to the JSON file. Defaults to LEARNED_FACTS_FILEPATH.

    Returns:
        True on success, False on error.
    """
    try:
        dir_path = os.path.dirname(filepath)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
            
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(facts, f, indent=4, ensure_ascii=False)
        return True
    except IOError as e: # pragma: no cover
        print(f"IOError saving learned facts to {filepath}: {e}")
        return False
    except TypeError as e: # pragma: no cover
        print(f"TypeError during JSON serialization for learned facts at {filepath}: {e}")
        return False
    except Exception as e: # pragma: no cover
        print(f"Unexpected error saving learned facts to {filepath}: {e}")
        return False

def load_learned_facts(filepath: str = LEARNED_FACTS_FILEPATH) -> List[Dict[str, Any]]:
    """
    Reads JSON data from the file and deserializes it into a list of learned fact dictionaries.
    Includes migration logic for old string-based fact lists.

    Args:
        filepath: The path to the JSON file. Defaults to LEARNED_FACTS_FILEPATH.

    Returns:
        The loaded list of fact dictionaries. Returns an empty list if the file
        doesn't exist, is invalid JSON, or another error occurs.
    """
    if not os.path.exists(filepath):
        return []
        
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if not isinstance(data, list):
            print(f"Warning: Data in '{filepath}' is not a list. Returning empty list.")
            return []

        if not data: # Empty list
            return []

        # Check if it's the old format (list of strings) or new format (list of dicts)
        if isinstance(data[0], str):
            print(f"Info: Migrating old format learned_facts.json (list of strings) at '{filepath}' to new structured format.")
            migrated_facts: List[Dict[str, Any]] = []
            for old_fact_text in data:
                if not isinstance(old_fact_text, str):
                    print(f"Warning: Non-string item found during string list migration: {old_fact_text}. Skipping.")
                    continue
                migrated_facts.append({
                    "fact_id": f"fact_{uuid.uuid4().hex[:8]}",
                    "text": old_fact_text,
                    "category": "uncategorized_migrated", # Specific category for these
                    "user_id": None, # Add user_id as None
                    "source": "migrated_from_string_list",
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                })
            if save_learned_facts(migrated_facts, filepath):
                 print(f"Info: Successfully migrated string-list facts and saved in new format to '{filepath}'.")
            else:
                 print(f"Error: Failed to save migrated string-list facts to '{filepath}'.")
            return migrated_facts

        elif isinstance(data[0], dict):
            # Already a list of dicts, ensure new fields (user_id, category) are present with defaults if missing.
            processed_facts: List[Dict[str, Any]] = []
            needs_resave = False
            for fact_dict in data:
                if not isinstance(fact_dict, dict):
                    print(f"Warning: Non-dictionary item found in list of facts: {fact_dict}. Skipping.")
                    needs_resave = True # If we skip one, we might want to resave the cleaned list
                    continue

                # Ensure essential keys like fact_id and text are there
                if "fact_id" not in fact_dict or "text" not in fact_dict:
                    print(f"Warning: Fact dictionary missing 'fact_id' or 'text': {fact_dict}. Skipping.")
                    needs_resave = True
                    continue

                if "user_id" not in fact_dict:
                    fact_dict["user_id"] = None # Add user_id with default None
                    needs_resave = True
                if "category" not in fact_dict:
                    fact_dict["category"] = "uncategorized" # Add category with default
                    needs_resave = True

                # Ensure timestamps are present, add if missing (less critical than user_id/category for new logic)
                if "created_at" not in fact_dict:
                    fact_dict["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    needs_resave = True
                if "updated_at" not in fact_dict:
                     fact_dict["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                     needs_resave = True
                if "source" not in fact_dict:
                    fact_dict["source"] = "unknown_pre_structured_era" # Or some other default
                    needs_resave = True


                processed_facts.append(fact_dict)

            if needs_resave:
                print(f"Info: Updating schema for some facts in '{filepath}' (adding user_id/category defaults).")
                if save_learned_facts(processed_facts, filepath):
                    print(f"Info: Successfully updated and re-saved facts with new schema defaults to '{filepath}'.")
                else:
                    print(f"Error: Failed to re-save facts with new schema defaults to '{filepath}'.")
            return processed_facts
        else:
            print(f"Warning: Data in '{filepath}' is in an unrecognized list format. Returning empty list.")
            return []

    except FileNotFoundError: # pragma: no cover
        return []
    except json.JSONDecodeError as e: # pragma: no cover
        print(f"JSONDecodeError loading learned facts from {filepath}: {e}. Returning empty list.")
        return []
    except IOError as e: # pragma: no cover
        print(f"IOError loading learned facts from {filepath}: {e}. Returning empty list.")
        return []
    except Exception as e: # pragma: no cover
        print(f"Unexpected error loading learned facts from {filepath}: {e}. Returning empty list.")
        return []

# --- Actionable Insights Persistence Functions --- Added section
def save_actionable_insights(insights: List[Dict[str, Any]], filepath: str = ACTIONABLE_INSIGHTS_FILEPATH) -> bool:
    """
    Serializes a list of ActionableInsight objects (as dictionaries) to JSON and writes to file.

    Args:
        insights: A list of dictionaries, where each dict is a serializable ActionableInsight.
        filepath: The path to the JSON file. Defaults to ACTIONABLE_INSIGHTS_FILEPATH.

    Returns:
        True on success, False on error.
    """
    try:
        dir_path = os.path.dirname(filepath)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(insights, f, indent=4, ensure_ascii=False)
        return True
    except IOError as e: # pragma: no cover
        print(f"IOError saving actionable insights to {filepath}: {e}")
        return False
    except TypeError as e: # pragma: no cover
        print(f"TypeError during JSON serialization for actionable insights at {filepath}: {e}")
        return False
    except Exception as e: # pragma: no cover
        print(f"Unexpected error saving actionable insights to {filepath}: {e}")
        return False

def load_actionable_insights(filepath: str = ACTIONABLE_INSIGHTS_FILEPATH) -> List[Dict[str, Any]]:
    """
    Reads a list of ActionableInsight objects (as dictionaries) from a JSON file.

    Args:
        filepath: The path to the JSON file. Defaults to ACTIONABLE_INSIGHTS_FILEPATH.

    Returns:
        The loaded list of dictionaries. Returns an empty list if the file
        doesn't exist, is invalid JSON, or another error occurs.
    """
    if not os.path.exists(filepath):
        return []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip(): # Handle empty file
                return []
            loaded_insights = json.loads(content)
        if not isinstance(loaded_insights, list): # pragma: no cover
            print(f"Warning: Data in actionable insights file '{filepath}' is not a list. Returning empty list.")
            return []
        return loaded_insights
    except FileNotFoundError: # pragma: no cover
        return []
    except json.JSONDecodeError as e: # pragma: no cover
        print(f"JSONDecodeError loading actionable insights from {filepath}: {e}. Returning empty list.")
        return []
    except IOError as e: # pragma: no cover
        print(f"IOError loading actionable insights from {filepath}: {e}. Returning empty list.")
        return []
    except Exception as e: # pragma: no cover
        print(f"Unexpected error loading actionable insights from {filepath}: {e}. Returning empty list.")
        return []

if __name__ == '__main__':
    print("--- Testing Persistent Memory for Goals ---")
    TEST_FILE_DIR = "test_data_pm" 
    TEST_GOALS_FILE = os.path.join(TEST_FILE_DIR, "test_goals.json") # Renamed for clarity

    # Ensure the test directory is clean before starting
    # Simplified cleanup for tests - more robust cleanup might be needed for complex scenarios
    if os.path.exists(TEST_GOALS_FILE):
        os.remove(TEST_GOALS_FILE)
    # Clean up other specific test files if they exist
    _test_invalid_goals_file = os.path.join(TEST_FILE_DIR, "invalid_goals.json")
    if os.path.exists(_test_invalid_goals_file):
        os.remove(_test_invalid_goals_file)
    # Attempt to remove the directory if empty
    if os.path.exists(TEST_FILE_DIR) and not os.listdir(TEST_FILE_DIR):
        try:
            os.rmdir(TEST_FILE_DIR)
        except OSError:
            print(f"Warning: Could not remove test directory {TEST_FILE_DIR} as it might not be empty or in use.")
    elif not os.path.exists(TEST_FILE_DIR):
        os.makedirs(TEST_FILE_DIR, exist_ok=True)


    sample_goals = {
        "goal1": {"id": "goal1", "description": "Test saving goals", "status": "pending", "priority": 1},
        "goal2": {"id": "goal2", "description": "Test loading goals", "status": "in_progress", "priority": 2},
    }

    # Test saving
    print(f"\nAttempting to save sample goals to {TEST_GOALS_FILE}...")
    save_success_goals = save_goals_to_file(TEST_GOALS_FILE, sample_goals)
    assert save_success_goals, "save_goals_to_file failed during test."
    print(f"Save operation result for goals: {save_success_goals}")
    assert os.path.exists(TEST_GOALS_FILE), f"File {TEST_GOALS_FILE} was not created after save."

    # Test loading
    print(f"\nAttempting to load goals from {TEST_GOALS_FILE}...")
    loaded_goals = load_goals_from_file(TEST_GOALS_FILE)
    assert loaded_goals == sample_goals, "Loaded goals do not match saved goals."
    print(f"Loaded goals: {loaded_goals}")

    # Test loading non-existent file
    print("\nAttempting to load goals from a non-existent file...")
    NON_EXISTENT_GOALS_FILE = os.path.join(TEST_FILE_DIR, "non_existent_goals.json")
    loaded_empty_goals = load_goals_from_file(NON_EXISTENT_GOALS_FILE)
    assert loaded_empty_goals == {}, "Loading non-existent goals file did not return an empty dict."
    print(f"Result from loading non-existent goals file: {loaded_empty_goals} (should be empty)")

    # Test loading invalid JSON
    print("\nAttempting to load goals from an invalid JSON file...")
    INVALID_JSON_GOALS_FILE = os.path.join(TEST_FILE_DIR, "invalid_goals.json")
    os.makedirs(os.path.dirname(INVALID_JSON_GOALS_FILE), exist_ok=True) 
    with open(INVALID_JSON_GOALS_FILE, 'w') as f:
        f.write("This is not valid JSON {")
    loaded_invalid_goals = load_goals_from_file(INVALID_JSON_GOALS_FILE)
    assert loaded_invalid_goals == {}, "Loading invalid JSON for goals did not return an empty dict."
    print(f"Result from loading invalid JSON goals file: {loaded_invalid_goals} (should be empty)")


    # Cleanup for goals tests
    print("\nCleaning up goals test files...")
    if os.path.exists(TEST_GOALS_FILE):
        os.remove(TEST_GOALS_FILE)
    if os.path.exists(INVALID_JSON_GOALS_FILE):
        os.remove(INVALID_JSON_GOALS_FILE)
    # Attempt to remove the directory if empty - this might be shared with other tests below
    # So, cleanup of TEST_FILE_DIR itself is deferred until the very end of __main__

    print("\n--- Goals Persistent Memory Tests Finished ---")

    print("\n--- Testing Persistent Memory for Learned Facts ---")
    TEST_FACTS_FILE = os.path.join(TEST_FILE_DIR, "test_learned_facts.json")
    _test_invalid_facts_file = os.path.join(TEST_FILE_DIR, "invalid_facts.json")

    sample_facts_old_format = ["fact1: the sky is blue", "fact2: elephants are large"]
    sample_facts_new_format = [
        {"fact_id": "fact_abc123", "text": "Python is a programming language", "category": "programming",
         "source": "manual_test", "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
         "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    ]

    # Test saving facts (new format)
    print(f"\nAttempting to save sample new format facts to {TEST_FACTS_FILE}...")
    save_success_facts = save_learned_facts(sample_facts_new_format, TEST_FACTS_FILE)
    assert save_success_facts, "save_learned_facts (new format) failed during test."
    print(f"Save operation result for new format facts: {save_success_facts}")
    assert os.path.exists(TEST_FACTS_FILE), f"File {TEST_FACTS_FILE} was not created after save."

    # Test loading facts (new format)
    print(f"\nAttempting to load new format facts from {TEST_FACTS_FILE}...")
    loaded_facts_new = load_learned_facts(TEST_FACTS_FILE)
    assert loaded_facts_new == sample_facts_new_format, "Loaded new format facts do not match saved facts."
    print(f"Loaded new format facts: {loaded_facts_new}")
    os.remove(TEST_FACTS_FILE) # Clean up for next test

    # Test loading facts (old format and migration)
    print(f"\nAttempting to load old format facts (expecting migration) from {TEST_FACTS_FILE}...")
    # Save old format directly to simulate existing file
    with open(TEST_FACTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(sample_facts_old_format, f, indent=4)

    migrated_facts = load_learned_facts(TEST_FACTS_FILE)
    assert len(migrated_facts) == len(sample_facts_old_format), "Migration did not convert all old facts."
    for i, fact_dict in enumerate(migrated_facts):
        assert isinstance(fact_dict, dict), "Migrated fact is not a dictionary."
        assert fact_dict["text"] == sample_facts_old_format[i], "Migrated fact text does not match original."
        assert fact_dict["category"] == "uncategorized", "Migrated fact category is not 'uncategorized'."
        assert fact_dict["source"] == "migrated_from_old_format", "Migrated fact source is incorrect."
        assert "fact_id" in fact_dict and "created_at" in fact_dict and "updated_at" in fact_dict
    print(f"Successfully loaded and migrated old format facts. First migrated fact: {migrated_facts[0] if migrated_facts else 'N/A'}")

    # Verify that the file was saved in the new format after migration
    if os.path.exists(TEST_FACTS_FILE):
        with open(TEST_FACTS_FILE, 'r', encoding='utf-8') as f:
            data_after_migration_save = json.load(f)
            assert isinstance(data_after_migration_save, list) and isinstance(data_after_migration_save[0], dict), \
                "File was not saved in new dictionary format after migration."
        print("Verified that migrated facts were saved back in new format.")


    # Test loading facts from a non-existent file
    print("\nAttempting to load facts from a non-existent file...")
    NON_EXISTENT_FACTS_FILE = os.path.join(TEST_FILE_DIR, "non_existent_facts.json")
    loaded_empty_facts = load_learned_facts(NON_EXISTENT_FACTS_FILE)
    assert loaded_empty_facts == [], "Loading non-existent facts file did not return an empty list."
    print(f"Result from loading non-existent facts file: {loaded_empty_facts} (should be empty list)")

    # Test loading facts from an invalid JSON file
    print("\nAttempting to load facts from an invalid JSON file...")
    INVALID_JSON_FACTS_FILE = os.path.join(TEST_FILE_DIR, "invalid_facts.json")
    os.makedirs(os.path.dirname(INVALID_JSON_FACTS_FILE), exist_ok=True) 
    with open(INVALID_JSON_FACTS_FILE, 'w') as f:
        f.write("This is not a valid JSON list")
    loaded_invalid_facts = load_learned_facts(INVALID_JSON_FACTS_FILE)
    assert loaded_invalid_facts == [], "Loading invalid JSON for facts did not return an empty list."
    print(f"Result from loading invalid JSON facts file: {loaded_invalid_facts} (should be empty list)")

    # Cleanup for facts tests
    print("\nCleaning up facts test files...")
    if os.path.exists(TEST_FACTS_FILE):
        os.remove(TEST_FACTS_FILE)
    if os.path.exists(INVALID_JSON_FACTS_FILE):
        os.remove(INVALID_JSON_FACTS_FILE)
    # INVALID_TYPE_FACTS_FILE test was implicitly covered by new format check, can remove if not needed
    # if os.path.exists(INVALID_TYPE_FACTS_FILE):
    #     os.remove(INVALID_TYPE_FACTS_FILE)
    print("--- Learned Facts Persistent Memory Tests Finished ---")

    # --- Testing Persistent Memory for Actionable Insights ---
    print("\n--- Testing Persistent Memory for Actionable Insights ---")
    TEST_INSIGHTS_FILE = os.path.join(TEST_FILE_DIR, "test_actionable_insights.json")
    INVALID_JSON_INSIGHTS_FILE = os.path.join(TEST_FILE_DIR, "invalid_insights.json")
    NON_EXISTENT_INSIGHTS_FILE = os.path.join(TEST_FILE_DIR, "non_existent_insights.json")

    if os.path.exists(TEST_INSIGHTS_FILE): os.remove(TEST_INSIGHTS_FILE) # pragma: no cover
    if os.path.exists(INVALID_JSON_INSIGHTS_FILE): os.remove(INVALID_JSON_INSIGHTS_FILE) # pragma: no cover

    sample_insights_data = [
        {
            "insight_id": "TOOL_BUG_SUSPECTED_test1", "type": "TOOL_BUG_SUSPECTED",
            "description": "Tool X failed with ValueError", "source_reflection_entry_ids": ["entry1"],
            "related_tool_name": "ToolX", "priority": 3, "status": "NEW",
            "creation_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), "metadata": {}
        },
        {
            "insight_id": "KNOWLEDGE_GAP_IDENTIFIED_test2", "type": "KNOWLEDGE_GAP_IDENTIFIED",
            "description": "Agent needs to learn about topic Y", "source_reflection_entry_ids": ["entry2"],
            "knowledge_to_learn": "Topic Y is important.", "priority": 5, "status": "NEW",
            "creation_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), "metadata": {}
        }
    ]

    print(f"\nAttempting to save {len(sample_insights_data)} sample insights to {TEST_INSIGHTS_FILE}...")
    assert save_actionable_insights(sample_insights_data, TEST_INSIGHTS_FILE), "save_actionable_insights failed."
    print(f"Save operation for insights successful.")
    assert os.path.exists(TEST_INSIGHTS_FILE), f"{TEST_INSIGHTS_FILE} was not created."

    print(f"\nAttempting to load insights from {TEST_INSIGHTS_FILE}...")
    loaded_insights = load_actionable_insights(TEST_INSIGHTS_FILE)
    assert loaded_insights == sample_insights_data, "Loaded insights do not match saved insights."
    print(f"Loaded {len(loaded_insights)} insights successfully.")

    print("\nAttempting to load insights from a non-existent file...")
    loaded_empty_insights = load_actionable_insights(NON_EXISTENT_INSIGHTS_FILE)
    assert loaded_empty_insights == [], "Loading non-existent insights file did not return an empty list."
    print(f"Loading non-existent insights file test successful: {loaded_empty_insights}")

    print("\nAttempting to load insights from an invalid JSON file...")
    with open(INVALID_JSON_INSIGHTS_FILE, 'w') as f: f.write("This is not valid JSON [")
    loaded_invalid_insights = load_actionable_insights(INVALID_JSON_INSIGHTS_FILE)
    assert loaded_invalid_insights == [], "Loading invalid JSON for insights did not return an empty list."
    print(f"Loading invalid JSON insights file test successful: {loaded_invalid_insights}")

    if os.path.exists(TEST_INSIGHTS_FILE): os.remove(TEST_INSIGHTS_FILE) # pragma: no cover
    if os.path.exists(INVALID_JSON_INSIGHTS_FILE): os.remove(INVALID_JSON_INSIGHTS_FILE) # pragma: no cover
    print("--- Actionable Insights Persistent Memory Tests Finished ---")
    
    # General cleanup for the test directory at the very end
    print("\nFinal cleanup of test directory...")
    # Clean up any other specific test files that might have been missed if tests failed early
    if os.path.exists(TEST_GOALS_FILE): os.remove(TEST_GOALS_FILE) # pragma: no cover
    if os.path.exists(_test_invalid_goals_file): os.remove(_test_invalid_goals_file) # pragma: no cover
    if os.path.exists(TEST_FACTS_FILE): os.remove(TEST_FACTS_FILE) # Cleaned up already or after specific tests
    if os.path.exists(INVALID_JSON_FACTS_FILE): os.remove(INVALID_JSON_FACTS_FILE) # Cleaned up already or after specific tests
    # if os.path.exists(INVALID_TYPE_FACTS_FILE): os.remove(INVALID_TYPE_FACTS_FILE) # This test is less relevant with new structure

    if os.path.exists(TEST_INSIGHTS_FILE): os.remove(TEST_INSIGHTS_FILE) # pragma: no cover
    if os.path.exists(INVALID_JSON_INSIGHTS_FILE): os.remove(INVALID_JSON_INSIGHTS_FILE) # pragma: no cover

    if os.path.exists(TEST_FILE_DIR) and not os.listdir(TEST_FILE_DIR): # pragma: no cover
        try:
            os.rmdir(TEST_FILE_DIR)
            print(f"Test directory {TEST_FILE_DIR} removed.")
        except OSError: # pragma: no cover
            print(f"Warning: Could not remove test directory {TEST_FILE_DIR}. It might not be empty or is in use.")
    elif os.path.exists(TEST_FILE_DIR): # pragma: no cover
        remaining_files = os.listdir(TEST_FILE_DIR)
        print(f"Warning: Test directory {TEST_FILE_DIR} still contains files: {remaining_files}. Manual cleanup may be needed.")
    
    print("\n--- All Persistent Memory Tests Finished ---")
    
    

# --- Tool Persistence Functions ---

def save_tools_to_file(filepath: str, tool_registry_data: Dict[str, Dict[str, Any]]) -> bool:
    """
    Serializes the tool_registry_data (metadata only) to JSON and writes it to file.

    Args:
        filepath: The path to the JSON file (e.g., "data/tools.json").
        tool_registry_data: The dictionary of tool metadata to save.
                            Assumes 'callable_cache' has been removed.

    Returns:
        True on success, False on error.
    """
    try:
        dir_path = os.path.dirname(filepath)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
            
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(tool_registry_data, f, indent=4, ensure_ascii=False)
        # print(f"Successfully saved tools to {filepath}") # Feedback handled by caller
        return True
    except IOError as e:
        print(f"IOError saving tools to {filepath}: {e}")
        return False
    except TypeError as e:
        print(f"TypeError during JSON serialization for tools at {filepath}: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error saving tools to {filepath}: {e}")
        return False

def load_tools_from_file(filepath: str) -> Dict[str, Dict[str, Any]]:
    """
    Reads tool metadata JSON from the file and deserializes it.

    Args:
        filepath: The path to the JSON file.

    Returns:
        The loaded dictionary of tool metadata. Returns an empty dictionary
        if the file doesn't exist, is invalid JSON, or another error occurs.
    """
    if not os.path.exists(filepath):
        # print(f"Info: Tools file '{filepath}' not found. Starting with no persisted tools.") # Feedback by caller
        return {}
        
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tool_data = json.load(f)
        # print(f"Successfully loaded tools from {filepath}") # Feedback by caller
        return tool_data
    except FileNotFoundError:
        # print(f"Info: Tools file '{filepath}' not found.") # Feedback by caller
        return {}
    except json.JSONDecodeError as e:
        print(f"JSONDecodeError loading tools from {filepath}: {e}. Returning empty toolset.")
        return {}
    except IOError as e:
        print(f"IOError loading tools from {filepath}: {e}. Returning empty toolset.")
        return {}
    except Exception as e:
        print(f"Unexpected error loading tools from {filepath}: {e}. Returning empty toolset.")
        return {}

REFLECTION_LOG_FILENAME = "reflection_log.json"
REFLECTION_LOG_FILEPATH = os.path.join(get_data_dir(), REFLECTION_LOG_FILENAME)

def save_reflection_log_entries(filepath: str, entries_as_dicts: List[Dict[str, Any]]) -> bool:
    """
    Serializes a list of reflection log entries (as dictionaries) to JSON and writes to file.

    Args:
        filepath: The path to the JSON file.
        entries_as_dicts: A list of dictionaries, where each dict is a serializable reflection log entry.

    Returns:
        True on success, False on error.
    """
    try:
        dir_path = os.path.dirname(filepath)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        temp_filepath = filepath + ".tmp"
        with open(temp_filepath, 'w', encoding='utf-8') as f:
            json.dump(entries_as_dicts, f, indent=4, ensure_ascii=False)

        # Atomically replace the old file with the new one
        try:
            os.replace(temp_filepath, filepath)
        except OSError:  # Fallback for systems where os.replace might not be atomic (e.g., some network filesystems)
            os.remove(filepath) # pragma: no cover
            os.rename(temp_filepath, filepath) # pragma: no cover

        return True

    except IOError as e:
        print(f"IOError saving reflection log to {filepath}: {e}")
        return False
    except TypeError as e:
        print(f"TypeError during JSON serialization for reflection log at {filepath}: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error saving reflection log to {filepath}: {e}")
        return False

def load_reflection_log_entries(filepath: str) -> List[Dict[str, Any]]:
    """
    Reads a list of reflection log entries (as dictionaries) from a JSON file.

    Args:
        filepath: The path to the JSON file.

    Returns:
        The loaded list of dictionaries. Returns an empty list if the file
        doesn't exist, is invalid JSON, or another error occurs.
    """
    if not os.path.exists(filepath):
        return []
        
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip(): # Handle empty file
                return []
            loaded_entries = json.loads(content) # Use json.loads on the read content
            if not isinstance(loaded_entries, list):
                print(f"Warning: Data in reflection log '{filepath}' is not a list. Returning empty log.")
                return []
            # Further validation could ensure each item is a dict, but ReflectionLog.from_dict will handle that.
        # print(f"Successfully loaded reflection log from {filepath}") # Optional: for debugging
        return loaded_entries
    except FileNotFoundError: 
        return []
    except json.JSONDecodeError as e:
        print(f"JSONDecodeError loading reflection log from {filepath}: {e}. Returning empty log.")
        return []
    except IOError as e:
        print(f"IOError loading reflection log from {filepath}: {e}. Returning empty log.")
        return []
    except Exception as e:
        print(f"Unexpected error loading reflection log from {filepath}: {e}. Returning empty log.")
        return []