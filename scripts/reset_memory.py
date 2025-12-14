import os
import json
import shutil

# Paths - Assuming running from project root
DATA_DIR = os.path.abspath(os.path.join("ai_assistant", "core", "data"))
MEMORY_DIR = os.path.abspath(os.path.join("ai_assistant", "memory", "data")) # Some files might be here?
# Based on ls output earlier, learned_facts was in core/data.

CHAT_SESSIONS_DIR = os.path.abspath(os.path.join("_memory_", "chat_sessions"))

FILES_TO_RESET = [
    os.path.join(DATA_DIR, "reflection_log.json"),
    os.path.join(DATA_DIR, "actionable_insights.json"),
    os.path.join(DATA_DIR, "event_log.json"),
    os.path.join(DATA_DIR, "learned_facts.json"),
    os.path.join(DATA_DIR, "notifications.json"),
    os.path.join(DATA_DIR, "rag_vector_store.json"),
    os.path.join(DATA_DIR, "suggestions.json"), # Also clear suggestions
]

def reset_file(filepath):
    if not os.path.exists(filepath):
        print(f"Skipping {filepath} (not found)")
        return
    
    try:
        # Create backup
        backup_path = filepath + ".bak"
        shutil.copy2(filepath, backup_path)
        print(f"Backed up {filepath} to {backup_path}")
        
        # Determine empty state
        if filepath.endswith("rag_vector_store.json"):
             empty_content = {} # Vector store might be a dict
        else:
             empty_content = [] # Most logs are lists
             
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(empty_content, f, indent=2)
        print(f"Reset {filepath}")
        
    except Exception as e:
        print(f"Error resetting {filepath}: {e}")

def main():
    print(f"Resetting AI Memory in {DATA_DIR}...")
    
    if not os.path.exists(DATA_DIR):
        print(f"Error: Data directory not found at {DATA_DIR}")
        return

    for filepath in FILES_TO_RESET:
        reset_file(filepath)

    # Reset Chat Sessions
    if os.path.exists(CHAT_SESSIONS_DIR):
        print(f"Resetting chat sessions in {CHAT_SESSIONS_DIR}...")
        for filename in os.listdir(CHAT_SESSIONS_DIR):
            if filename.endswith(".json"):
                file_path = os.path.join(CHAT_SESSIONS_DIR, filename)
                try:
                    os.remove(file_path)
                    print(f"Deleted chat session: {filename}")
                except Exception as e:
                    print(f"Error deleting {filename}: {e}")
    else:
        print(f"Chat sessions directory not found at {CHAT_SESSIONS_DIR}")
    
    print("\nMemory reset complete. Tool registry and generated tools were PRESERVED.")

if __name__ == "__main__":
    main()
