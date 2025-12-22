
import os
import shutil
import json

DATA_DIR = r"c:\Users\Jason\Desktop\Self Evolving AI\ai_assistant\core\data"

FILES_TO_RESET = {
    "learned_facts.json": [],
    "event_log.json": [],
    "reflection_log.json": [],
    "actionable_insights.json": [],
    "notifications.json": [],
    "suggestions.json": [],
    "architect_state.json": {},
    "librarian_state.json": {},
    "projects.json": {}
}

DIRS_TO_DELETE = [
    "chroma_db",
    "chroma_db_backup_panic",
    "rag_vector_store.json.backup_panic", # File, but treated as delete target
    "temp_agents" 
]

# generated_tools - decide if we want to wipe this. User said "fresh start". 
# If tools are wiped, tool_registry.json needs to be wiped too.
# Let's be aggressive but safe: Wipe tool registry, keep generated_tools folder exist but empty.

FILES_TO_RESET["tool_registry.json"] = {}
DIRS_TO_DELETE.append("generated_tools")

def wipe_memory():
    print("WARNING: Starting Memory Wipe...")
    
    # 1. Reset JSON files
    for filename, empty_state in FILES_TO_RESET.items():
        filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            try:
                # Create backup first
                shutil.copy2(filepath, filepath + ".pre_wipe.bak")
                
                with open(filepath, 'w') as f:
                    json.dump(empty_state, f, indent=2)
                print(f"Reset {filename} to empty state.")
            except Exception as e:
                print(f"Error resetting {filename}: {e}")
        else:
            print(f"Skipping {filename} (not found).")

    # 2. Delete Directories/Files
    for item in DIRS_TO_DELETE:
        path = os.path.join(DATA_DIR, item)
        if os.path.exists(path):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                print(f"Deleted {item}.")
            except Exception as e:
                print(f"Error deleting {item}: {e}")

    # Re-create empty generated_tools directory
    gen_tools_path = os.path.join(DATA_DIR, "generated_tools")
    if not os.path.exists(gen_tools_path):
        os.makedirs(gen_tools_path)
        # Add __init__.py
        with open(os.path.join(gen_tools_path, "__init__.py"), 'w') as f:
            f.write("")
        print("Re-created empty generated_tools directory.")

    print("\nMemory Wipe Complete. Please restart the backend services.")

if __name__ == "__main__":
    wipe_memory()
