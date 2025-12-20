import sys
import os

# Add the project root to sys.path
sys.path.append(os.getcwd())

from ai_assistant.custom_tools.awareness_tools import get_self_awareness_info_and_converse
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager

# Mock managers if needed, or instantiate real ones if possible (lightweight)
# Since the tool uses them, we should try to pass them if required OR reliance on global instances?
# The tool signature is: get_self_awareness_info_and_converse(task_manager=None, notification_manager=None, ...)
# It handles None gracefully usually, or uses dependency injection.

try:
    print("Running tool...")
    tm = TaskManager()
    nm = NotificationManager()
    result = get_self_awareness_info_and_converse(task_manager=tm, notification_manager=nm)
    
    with open("debug_awareness_output.txt", "w", encoding="utf-8") as f:
        f.write(result)
        
    print(f"Tool executed. Output length: {len(result)}")
    print("First 500 chars:")
    print(result[:500])
except Exception as e:
    print(f"Error running tool: {e}")
