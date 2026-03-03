import os
import sys

# Ensure the project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__))))

from ai_assistant.custom_tools.awareness_tools import get_self_awareness_info_and_converse
from ai_assistant.memory.persistent_memory import load_learned_facts
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager

def debug_check():
    print("--- Debugging Awareness Tool & Facts ---")
    
    # 1. Check raw facts loading
    facts = load_learned_facts()
    print(f"Raw Loaded Facts (Type: {type(facts)}):")
    print(facts)
    print("-" * 20)
    
    # 2. Check full tool output
    # We pass None for managers because we want to see the facts part primarily, 
    # and the tool should handle None managers gracefully (prints "not available").
    output = get_self_awareness_info_and_converse(
        context="Debug Check",
        task_manager=TaskManager(), 
        notification_manager=NotificationManager()
    )
    
    print("Full Tool Output:")
    print(output)
    print("-" * 20)

    if "Learned Facts" in output:
        print("SUCCESS: 'Learned Facts' section found in output.")
        if "jason kinslwo" in output or "Jason Kinslow" in output:
             print("SUCCESS: User name found in facts.")
        else:
             print("FAILURE: User name NOT found in facts section.")
    else:
        print("FAILURE: 'Learned Facts' section NOT found in output.")

if __name__ == "__main__":
    debug_check()
