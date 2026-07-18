import os
import shutil
import json

# Paths - Assuming running from project root
DATA_DIR = os.path.abspath(os.path.join("ai_assistant", "core", "data"))
CHAT_SESSIONS_DIR = os.path.abspath(os.path.join("_memory_", "chat_sessions"))
HOME_DATA_DIR = os.path.expanduser("~/.ai_assistant_data")

# Lists to reset
LIST_FILES = [
    "reflection_log.json",
    "actionable_insights.json",
    "event_log.json",
    "learned_facts.json",
    "notifications.json",
    "suggestions.json",
    "reminders.json",
    "episodic_memories.json",
]

# Dicts to reset
DICT_FILES = [
    "goals.json",
    "architect_heatmap.json",
    "librarian_state.json",
    "projects.json",
    "tool_registry.json"
]

def clean_data_files():
    print(f"Cleaning database files in {DATA_DIR}...")
    if not os.path.exists(DATA_DIR):
        print(f"Error: Data directory not found at {DATA_DIR}")
        return

    # 1. Reset List files to []
    for filename in LIST_FILES:
        filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump([], f, indent=2)
                print(f"SUCCESS: Reset list file to []: {filename}")
            except Exception as e:
                print(f"ERROR resetting list file {filename}: {e}")
        else:
            print(f"INFO: File not found, creating clean: {filename}")
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump([], f, indent=2)
            except Exception as e:
                print(f"ERROR creating list file {filename}: {e}")

    # 2. Reset Dict files to {}
    for filename in DICT_FILES:
        filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump({}, f, indent=2)
                print(f"SUCCESS: Reset dict file to {{}}: {filename}")
            except Exception as e:
                print(f"ERROR resetting dict file {filename}: {e}")
        else:
            print(f"INFO: File not found, creating clean: {filename}")
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump({}, f, indent=2)
            except Exception as e:
                print(f"ERROR creating dict file {filename}: {e}")

    # 3. Specific state resets
    state_file = os.path.join(DATA_DIR, "architect_state.json")
    try:
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump({"last_audit_timestamp": 0.0}, f, indent=2)
        print("SUCCESS: Reset architect_state.json")
    except Exception as e:
        print(f"ERROR resetting architect_state.json: {e}")

    # 4. Delete backup/WAL files
    print("\nCleaning up backup and temporary files...")
    all_files = os.listdir(DATA_DIR)
    for f in all_files:
        if f.endswith(".bak") or f.endswith(".backup_panic") or ".json.bak" in f:
            filepath = os.path.join(DATA_DIR, f)
            try:
                os.remove(filepath)
                print(f"SUCCESS: Deleted backup file: {f}")
            except Exception as e:
                print(f"ERROR deleting backup {f}: {e}")

    # 5. Delete Directories
    dirs_to_delete = ["chroma_db", "temp_agents"]
    for dir_name in dirs_to_delete:
        dir_path = os.path.join(DATA_DIR, dir_name)
        if os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
                print(f"SUCCESS: Deleted directory: {dir_name}")
            except Exception as e:
                print(f"ERROR deleting directory {dir_name}: {e}")

    # 6. Clear chat sessions in _memory_/chat_sessions
    print(f"\nCleaning chat sessions in {CHAT_SESSIONS_DIR}...")
    if os.path.exists(CHAT_SESSIONS_DIR):
        for filename in os.listdir(CHAT_SESSIONS_DIR):
            if filename.endswith(".json"):
                file_path = os.path.join(CHAT_SESSIONS_DIR, filename)
                try:
                    os.remove(file_path)
                    print(f"SUCCESS: Deleted chat session: {filename}")
                except Exception as e:
                    print(f"ERROR deleting chat session {filename}: {e}")
    else:
        print(f"INFO: Chat sessions directory not found: {CHAT_SESSIONS_DIR}")

    # 7. Clear ~/.ai_assistant_data/
    print(f"\nCleaning home data directory in {HOME_DATA_DIR}...")
    if os.path.exists(HOME_DATA_DIR):
        for filename in os.listdir(HOME_DATA_DIR):
            file_path = os.path.join(HOME_DATA_DIR, filename)
            try:
                if os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                else:
                    os.remove(file_path)
                print(f"SUCCESS: Deleted home data file/directory: {filename}")
            except Exception as e:
                print(f"ERROR deleting home data file {filename}: {e}")
    else:
        print(f"INFO: Home data directory not found or empty: {HOME_DATA_DIR}")

if __name__ == "__main__":
    clean_data_files()
    print("\nMemory Wipe Complete. All old tasks, summaries, and memories are gone.")
