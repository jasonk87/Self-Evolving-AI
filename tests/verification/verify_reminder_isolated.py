import asyncio
import os
import sys
import traceback
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())

async def test_reminder():
    print("--- Isolated Reminder Test ---")
    try:
        # generated.set_reminder is the function due to __init__
        from ai_assistant.custom_tools.generated import set_reminder
        print(f"Imported set_reminder object type: {type(set_reminder)}")
        
        # Patch asyncio.sleep in the module where set_reminder is defined
        # We assume the function's module is 'ai_assistant.custom_tools.generated.set_reminder'
        target_patch = f"{set_reminder.__module__}.asyncio.sleep"
        print(f"Patching: {target_patch}")
        
        with patch(target_patch) as mock_sleep:
            f = asyncio.Future()
            f.set_result(None)
            mock_sleep.return_value = f
            
            print("Testing 'in 1 minute'...")
            # Call the function directly
            result = await set_reminder("in 1 minute", "test relative")
            print(f"Result 1: {result}")
            
            if "Reminder: It's time to test relative!" in result:
                print("PASS: Relative time 1")
            else:
                print("FAIL: Relative time 1")
            
            # Additional test for 2nd case
            result2 = await set_reminder("in 2 hours", "test hours")
            print(f"Result 2: {result2}")
            if "Reminder: It's time to test hours!" in result2:
                 print("PASS: Relative time 2")
            else:
                 print("FAIL: Relative time 2")

    except Exception:
        print("CRASHED:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_reminder())
