import os
import shutil
import glob

def fresh_start():
    print("WARNING: This will wipe all AI memory. Tools and Core Logic will be preserved.")
    print("Initiating Fresh Start Protocol...")

    # 1. Define the Memory Paths (The stuff to wipe)
    paths_to_wipe = [
        "ai_assistant/core/data/chroma_db",             # Vector DB
        "ai_assistant/core/data/learned_facts.json",    # Facts
        "ai_assistant/core/data/episodic_memories.json",# Memories
        "ai_assistant/core/data/actionable_insights.json", # Old Insights
        "ai_assistant/core/data/reflection_log.json",   # Reflections
        "ai_assistant/core/data/notifications.json",    # Old Alerts
        "ai_assistant/core/data/tool_registry.json",    # Tool Cache (Force Re-scan)
        "ai_assistant/core/data/suggestions.json",      # Old Suggestions
        "src/main.py",                                  # The Hallucinated File
        "ai_assistant/main.py"                          # Added redundant file
    ]
    
    # 2. Define Pattern for Chat Logs
    chat_logs_pattern = "_memory_/chat_sessions/*.json"

    # 3. Execution
    for path in paths_to_wipe:
        if os.path.exists(path):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                    print(f"✅ Deleted Directory: {path}")
                else:
                    os.remove(path)
                    print(f"✅ Deleted File: {path}")
            except Exception as e:
                print(f"❌ Error deleting {path}: {e}")
        else:
            print(f"⚪ Already clean: {path}")

    # 4. Wipe Chat Logs
    files = glob.glob(chat_logs_pattern)
    for f in files:
        try:
            os.remove(f)
            print(f"✅ Deleted Chat Log: {f}")
        except Exception as e:
            print(f"❌ Error deleting {f}: {e}")

    print("\n✨ Fresh Start Complete. The AI is now a blank slate with full capabilities.")
    print("PLEASE RUN: python web_app.py to restart.")

if __name__ == "__main__":
    # Auto-confirm if run via automation tool, but stick to user request structure
    # We will simulate input 'WIPE' when running this script.
    print("Type 'WIPE' to confirm total memory reset: ")
    # Using input() as user designed
    confirm = input()
    if confirm == "WIPE":
        fresh_start()
    else:
        print("Operation cancelled.")
