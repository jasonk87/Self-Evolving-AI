import os
import sys

# Ensure the project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__))))

from ai_assistant.custom_tools.awareness_tools import get_self_awareness_info_and_converse
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager

def test_awareness_tool_arguments():
    print("Testing get_self_awareness_info_and_converse with arguments...")
    
    # Mocking managers (or using real ones initialized with temp files)
    # We just want to check if the function call works without TypeError
    
    # Note: In the real tool system, these are injected. 
    # Here we simulate the injection manually to verify the kwargs logic.
    
    # Case 1: Call with context string (simulating planner) and injected managers
    try:
        result = get_self_awareness_info_and_converse(
            "I am checking my status", 
            task_manager=TaskManager(), 
            notification_manager=NotificationManager()
        )
        print("Case 1 Success!")
        print(f"Result snippet: {result[:100]}")
    except TypeError as e:
        print(f"Case 1 FAILED with TypeError: {e}")
    except Exception as e:
        print(f"Case 1 FAILED with Exception: {e}")

    # Case 2: Call without context string
    try:
        result = get_self_awareness_info_and_converse(
            task_manager=TaskManager(), 
            notification_manager=NotificationManager()
        )
        print("Case 2 Success!")
    except TypeError as e:
        print(f"Case 2 FAILED with TypeError: {e}")

if __name__ == "__main__":
    test_awareness_tool_arguments()
