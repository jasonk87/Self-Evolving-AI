
import asyncio
import os
import sys

# Setup Path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ai_assistant.custom_tools.memory_tools import trigger_learning_scan
from ai_assistant.core.notification_manager import NotificationManager

async def main():
    print("Executing trigger_learning_scan...")
    nm = NotificationManager()
    
    # We pass the notification manager to ensures notifications happen
    result = await trigger_learning_scan(notification_manager=nm)
    
    print("\n--- Result ---")
    print(result)
    print("--------------")

if __name__ == "__main__":
    asyncio.run(main())
