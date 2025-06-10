import json
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from ai_assistant.config import get_data_dir # Import the centralized function

EVENT_LOG_FILENAME = "event_log.json"
EVENT_LOG_FILE = os.path.join(get_data_dir(), EVENT_LOG_FILENAME) # Use the centralized data directory
MAX_LOG_ENTRIES_IN_MEMORY = 100 # For get_recent_events, not a hard limit on file size

# Lock for file operations if we were in a threaded environment.
# For CLI, less critical but good to be aware of potential race conditions
# if the app becomes more complex. For now, direct file read/write is fine.
# import threading
# _file_lock = threading.Lock()

def _ensure_log_dir_exists():
    """Ensures the log directory exists."""
    # get_data_dir() called when EVENT_LOG_FILE is defined already ensures the base data directory exists.
    # This function can ensure the specific directory for the log file exists if it were in a subdirectory
    # of get_data_dir(), but since it's directly in get_data_dir(), this call is mostly redundant
    # but harmless with exist_ok=True.
    os.makedirs(os.path.dirname(EVENT_LOG_FILE), exist_ok=True)

def log_event(
    event_type: str,
    description: str,
    source: str,
    metadata: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Logs a structured event to the event_log.json file.

    Args:
        event_type: Enum-like string for the type of event.
        description: Human-readable summary of the event.
        source: Module or component name where the event originated.
        metadata: Optional dictionary for event-specific data.
        correlation_id: Optional UUID string to link related events.

    Returns:
        The event dictionary that was logged.
    """
    _ensure_log_dir_exists()

    event_id = uuid.uuid4().hex
    timestamp = datetime.now(timezone.utc).isoformat()
    
    event = {
        "timestamp": timestamp,
        "event_id": event_id,
        "event_type": event_type,
        "description": description,
        "source": source,
        "metadata": metadata if metadata is not None else {},
    }
    if correlation_id:
        event["correlation_id"] = correlation_id

    # with _file_lock: # If threading becomes a concern
    try:
        if os.path.exists(EVENT_LOG_FILE):
            with open(EVENT_LOG_FILE, 'r', encoding='utf-8') as f:
                try:
                    # Handle empty or malformed file
                    content = f.read()
                    if not content:
                        events = []
                    else:
                        events = json.loads(content)
                    if not isinstance(events, list): # Ensure it's a list, not some other JSON type
                        print(f"Warning: Event log file '{EVENT_LOG_FILE}' contained non-list data. Reinitializing.")
                        events = []
                except json.JSONDecodeError:
                    print(f"Warning: Could not decode JSON from '{EVENT_LOG_FILE}'. Reinitializing event log.")
                    events = [] # If file is corrupted, start fresh to avoid losing new logs
        else:
            events = []

        events.append(event)

        with open(EVENT_LOG_FILE, 'w', encoding='utf-8') as f: # Corrected typo here
            json.dump(events, f, indent=4, ensure_ascii=False)
            
        # Optional: print a confirmation or a summary of the logged event for debugging
        # print(f"Event logged ({event_type}): {description[:50]}...")

    except IOError as e:
        print(f"IOError logging event to {EVENT_LOG_FILE}: {e}")
    except Exception as e:
        print(f"Unexpected error logging event: {e}")
        
    return event # Return the created event, even if logging failed, for potential in-memory use by caller

def get_recent_events(limit: int = MAX_LOG_ENTRIES_IN_MEMORY) -> List[Dict[str, Any]]:
    """
    Retrieves a list of the most recent events from the log file.

    Args:
        limit: The maximum number of recent events to return.

    Returns:
        A list of event dictionaries, newest first. Returns empty if log is empty or error.
    """
    # with _file_lock: # If threading
    try:
        if not os.path.exists(EVENT_LOG_FILE):
            return []
        with open(EVENT_LOG_FILE, 'r', encoding='utf-8') as f:
            try:
                events = json.load(f)
                if not isinstance(events, list):
                     return [] # Or handle error
            except json.JSONDecodeError:
                return [] # Or handle error
        
        # Return last 'limit' events, newest first (assuming append-only log)
        return events[-limit:][::-1] 
    except IOError as e:
        print(f"IOError reading event log {EVENT_LOG_FILE}: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error reading event log: {e}")
        return []

if __name__ == '__main__':
    print("--- Testing Event Logger ---")
    
    # Clean up old log file for fresh test
    if os.path.exists(EVENT_LOG_FILE):
        os.remove(EVENT_LOG_FILE)

    # Log some sample events
    event1_meta = {"tool_name": "test_tool", "version": "1.0"}
    ev1 = log_event("TOOL_REGISTERED_MANUAL", "Test tool registered by user.", "cli.py", event1_meta)
    print(f"Logged event 1: {ev1.get('event_id')}")

    event2_meta = {"goal_id": "g123", "status": "completed"}
    ev2 = log_event("GOAL_STATUS_UPDATED", "Goal status changed.", "goal_management.py", event2_meta)
    print(f"Logged event 2: {ev2.get('event_id')}")
    
    ev3 = log_event("USER_INTERACTION", "User provided input.", "cli.py", {"input_length": 20})
    print(f"Logged event 3: {ev3.get('event_id')}")

    # Retrieve recent events
    print("\n--- Recent Events ---")
    recent = get_recent_events(limit=2)
    for item in recent:
        print(f"  {item['timestamp']} - {item['event_type']}: {item['description']}")
    
    assert len(recent) == 2, f"Expected 2 recent events, got {len(recent)}"
    if recent: # Check if recent is not empty before indexing
        assert recent[0]['event_id'] == ev3['event_id'], "Events not in newest-first order or wrong event"

    # Check full log content
    if os.path.exists(EVENT_LOG_FILE):
        with open(EVENT_LOG_FILE, 'r') as f:
            all_logged_events = json.load(f)
        print(f"\nTotal events in log file: {len(all_logged_events)}")
        assert len(all_logged_events) == 3, "Expected 3 events in the log file."
        assert all_logged_events[0]['event_id'] == ev1['event_id']
        assert all_logged_events[1]['event_id'] == ev2['event_id']
        assert all_logged_events[2]['event_id'] == ev3['event_id']
    else:
        print("ERROR: Log file not found after logging events.")
        assert False, "Log file not created."

    print("\n--- Testing with empty/corrupted log file (before next logs) ---")
    # Simulate corrupted log
    with open(EVENT_LOG_FILE, 'w') as f:
        f.write("this is not json")
    
    corrupt_read = get_recent_events(1)
    assert corrupt_read == [], f"Expected empty list from corrupted log, got {corrupt_read}"
    
    # Log another event - should reinitialize due to corruption
    ev4 = log_event("SYSTEM_WARNING", "Log file was corrupted, reinitialized.", "event_logger.py")
    print(f"Logged event 4 (after corruption): {ev4.get('event_id')}")
    
    recent_after_corruption = get_recent_events(5)
    assert len(recent_after_corruption) == 1, "Expected 1 event after reinitialization"
    if recent_after_corruption:
        assert recent_after_corruption[0]['event_id'] == ev4['event_id']
    
    print(f"Log content after reinit: {recent_after_corruption}")


    print("\n--- Event Logger Tests Finished ---")
