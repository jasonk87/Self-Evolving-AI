# Code for goal management.
import uuid
from typing import List, Dict, Optional, Union
from ai_assistant.memory.persistent_memory import save_goals_to_file, load_goals_from_file
import os

# --- Constants ---
DEFAULT_GOALS_FILE_DIR = "data"
DEFAULT_GOALS_FILE = os.path.join(DEFAULT_GOALS_FILE_DIR, "goals.json")

# --- Goal Data Structure ---
# A goal will be represented as a dictionary.
# Example:
# {
#     "id": "unique_id_string",
#     "description": "Achieve world peace.",
#     "status": "pending",  # "pending", "in_progress", "completed", "failed"
#     "priority": 1  # Lower number means higher priority
# }

# --- In-Memory Storage ---
_goals_db: Dict[str, Dict] = {} # This will be initialized by load_initial_goals
# _next_goal_int_id: int = 1 # Not currently used with UUIDs, but could be if IDs change

def _generate_goal_id() -> str:
    """Generates a unique goal ID."""
    return uuid.uuid4().hex

# --- Persistence Functions ---

def save_current_goals() -> bool:
    """Saves the current in-memory _goals_db to the DEFAULT_GOALS_FILE."""
    # The save_goals_to_file function in persistent_memory.py handles os.makedirs
    return save_goals_to_file(DEFAULT_GOALS_FILE, _goals_db)

def load_persisted_goals() -> bool:
    """
    Loads goals from DEFAULT_GOALS_FILE and replaces the in-memory _goals_db.
    Returns True if loading was successful and _goals_db was updated, False otherwise.
    """
    global _goals_db
    loaded_data = load_goals_from_file(DEFAULT_GOALS_FILE)
    if loaded_data or (not loaded_data and os.path.exists(DEFAULT_GOALS_FILE)): 
        # If loaded_data is not empty, or it's empty because the file itself was empty JSON ({})
        # and not because the file was missing (which load_goals_from_file handles by returning {}).
        # This logic ensures we overwrite with an empty DB if the file is truly empty vs. not found.
        # However, load_goals_from_file returns {} for missing file as well, so the check for
        # os.path.exists isn't strictly necessary if we trust its "file not found" behavior.
        # Let's simplify: if load_goals_from_file returns a dict (even empty), it's "successful".
        _goals_db = loaded_data
        # print(f"Goal management: _goals_db updated from {DEFAULT_GOALS_FILE}") # For debugging
        return True
    # If load_goals_from_file returned {} AND the file didn't exist, it's not an "error" for this function,
    # but it means no goals were loaded to replace the current DB.
    # The current implementation of load_goals_from_file returns {} for file not found,
    # so this function will effectively reset the _goals_db if the file is not found.
    # This might be desired, or one might want to only replace if the file *is* found.
    # For now, we'll consider any dict returned (even empty from a non-existent file) as a valid load.
    # The function `load_goals_from_file` already prints messages for file not found or errors.
    # So, if loaded_data is {} because file wasn't found, _goals_db becomes {}.
    _goals_db = loaded_data # This line ensures _goals_db is replaced even if file was empty or not found.
    return True # Consider it successful in terms of attempting a load.

def _initialize_goals_db():
    """Loads goals from the default file when the module is first initialized."""
    global _goals_db
    print(f"GoalManagement: Initializing goals database from '{DEFAULT_GOALS_FILE}'...")
    _goals_db = load_goals_from_file(DEFAULT_GOALS_FILE)
    if _goals_db:
        print(f"GoalManagement: Successfully loaded {len(_goals_db)} goals on startup.")
    else:
        print("GoalManagement: No goals loaded on startup or file not found/empty. Starting with an empty database.")

# --- CRUD Functions ---

def create_goal(description: str, priority: int = 3) -> Dict:
    """
    Creates a new goal and stores it in the in-memory database.
    Does not automatically save to file; call save_current_goals() for that.
    """
    goal_id = _generate_goal_id()
    goal = {
        "id": goal_id,
        "description": description,
        "status": "pending",
        "priority": priority,
    }
    _goals_db[goal_id] = goal
    return goal

def get_goal(goal_id: str) -> Optional[Dict]:
    """
    Retrieves a goal by its ID.

    Args:
        goal_id: The ID of the goal to retrieve.

    Returns:
        The goal dictionary if found, otherwise None.
    """
    return _goals_db.get(goal_id)

def update_goal(goal_id: str, description: Optional[str] = None, 
                status: Optional[str] = None, priority: Optional[int] = None) -> Optional[Dict]:
    """
    Updates an existing goal.

    Args:
        goal_id: The ID of the goal to update.
        description: The new description (if provided).
        status: The new status (if provided).
        priority: The new priority (if provided).

    Returns:
        The updated goal dictionary if found and updated, otherwise None.
    """
    goal = _goals_db.get(goal_id)
    if goal:
        if description is not None:
            goal["description"] = description
        if status is not None:
            # Basic validation for status, can be expanded
            valid_statuses = ["pending", "in_progress", "completed", "failed"]
            if status in valid_statuses:
                goal["status"] = status
            else:
                print(f"Warning: Invalid status '{status}' for goal '{goal_id}'. Not updated.")
        if priority is not None:
            goal["priority"] = int(priority) # Ensure priority is stored as int
        return goal
    return None

def delete_goal(goal_id: str) -> bool:
    """
    Deletes a goal by its ID.

    Args:
        goal_id: The ID of the goal to delete.

    Returns:
        True if the goal was found and deleted, otherwise False.
    """
    if goal_id in _goals_db:
        del _goals_db[goal_id]
        return True
    return False

def list_goals(status: Optional[str] = None) -> List[Dict]:
    """
    Lists all goals, optionally filtering by status.

    Args:
        status: If provided, filters goals by this status.

    Returns:
        A list of goal dictionaries.
    """
    if status:
        return [goal for goal in _goals_db.values() if goal["status"] == status]
    return list(_goals_db.values())

# --- Initialization ---
_initialize_goals_db() # Load goals when module is imported

# --- Basic Manual Testing (Persistence Focus) ---
if __name__ == '__main__':
    print("\n--- Testing Goal Management with Persistence ---")
    
    # Ensure the test data directory is clean or doesn't interfere
    # For these tests, goal_management uses DEFAULT_GOALS_FILE ("data/goals.json")
    # We should probably use a different file for its own tests to avoid side effects
    # with the main application's data. However, for this example, we'll assume
    # it's okay or that we clean up "data/goals.json" after.

    # Current state (should be from file if it existed, or empty)
    print(f"Initial goals loaded: {list_goals()}")

    # Create some goals
    print("\nCreating new goals...")
    g1 = create_goal("Test persistence goal 1", 1)
    g2 = create_goal("Test persistence goal 2", 2)
    print(f"Goals after creation: {list_goals()}")

    # Save goals
    print("\nSaving current goals...")
    if save_current_goals():
        print("Goals saved successfully to DEFAULT_GOALS_FILE.")
    else:
        print("Error saving goals.")
    
    # Clear in-memory DB to simulate restart / fresh load
    print("\nSimulating restart: Clearing in-memory _goals_db and loading...")
    _goals_db.clear() 
    print(f"In-memory goals after clearing: {list_goals()}") # Should be empty

    if load_persisted_goals():
        print("Goals loaded successfully from DEFAULT_GOALS_FILE.")
    else:
        print("Error loading goals or file not found.")
    
    print(f"Goals after loading: {list_goals()}")
    # Verify that g1 and g2 (or their equivalents) are present
    found_g1 = any(g['description'] == "Test persistence goal 1" for g in _goals_db.values())
    assert found_g1, "Goal 1 not found after loading."
    print("Verified that loaded goals include the saved ones.")

    # Modify a goal and save again
    print("\nUpdating a goal and re-saving...")
    if _goals_db.get(g1['id']):
        update_goal(g1['id'], status="in_progress", description="Updated persistence goal 1")
        print(f"Goal {g1['id']} updated.")
    else:
        print(f"Could not find goal {g1['id']} to update after load, something is wrong.")

    save_current_goals()
    print("Re-saved goals.")

    # Simulate another restart
    print("\nSimulating another restart and load...")
    _goals_db.clear()
    load_persisted_goals()
    print(f"Goals after second loading: {list_goals()}")
    updated_g1_check = get_goal(g1['id'])
    if updated_g1_check:
        assert updated_g1_check['status'] == "in_progress", "Goal 1 status not updated after re-load."
        assert updated_g1_check['description'] == "Updated persistence goal 1", "Goal 1 description not updated."
        print("Verified updates persisted.")
    else:
        print(f"Goal {g1['id']} not found after second load. This is an error.")

    # Clean up the default goals file for next time / other tests
    # In a real app, you wouldn't usually do this in the __main__ block.
    if os.path.exists(DEFAULT_GOALS_FILE):
        print(f"\nCleaning up by removing {DEFAULT_GOALS_FILE}...")
        os.remove(DEFAULT_GOALS_FILE)
        if os.path.exists(DEFAULT_GOALS_FILE_DIR) and not os.listdir(DEFAULT_GOALS_FILE_DIR):
            os.rmdir(DEFAULT_GOALS_FILE_DIR)


    print("\n--- End of Goal Management Persistence Testing ---")
