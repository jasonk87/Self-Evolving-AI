import asyncio
import os
import sys
import traceback
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.getcwd())

async def verify_fixes():
    print("--- Verifying System Fixes ---")
    
    # 1. Verify ToolSystem.get_tool_info
    print("\n1. Testing ToolSystem.get_tool_info...")
    try:
        from ai_assistant.tools import tool_system
        import importlib
        importlib.reload(tool_system)
        
        # Test get_tool_info
        tool_info = tool_system.tool_system_instance.get_tool_info("greet_user")
        if tool_info and "description" in tool_info:
            print("SUCCESS: ToolSystem.get_tool_info returned valid data.")
        else:
            print(f"FAILURE: ToolSystem.get_tool_info returned unexpected data: {tool_info}")
    except Exception:
        print("FAILURE: verify_tool_system crashed:")
        traceback.print_exc()

    # 2. Verify Weather Tool Output
    print("\n2. Testing Weather Tool Output...")
    try:
        from ai_assistant.custom_tools.generated import weather_tool
        importlib.reload(weather_tool)
        
        # Mock requests.get
        with patch('requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                'cod': 200,
                'name': 'Test City',
                'sys': {'country': 'TC'},
                'weather': [{'description': 'sunny'}],
                'main': {'temp': 72.5, 'humidity': 40},
                'wind': {'speed': 5.0}
            }
            mock_get.return_value = mock_response
            
            result = weather_tool.get_weather("Test City", api_key="test_key")
            
            if 'result_text' in result and "sunny" in result['result_text'] and "72.5" in result['result_text']:
                 print(f"SUCCESS: Weather tool result contains 'result_text': {result['result_text']}")
            else:
                 print(f"FAILURE: Weather tool result missing 'result_text' or incorrect format: {result}")

    except Exception:
         print("FAILURE: verify_weather_tool logic crashed:")
         traceback.print_exc()

    # 3. Verify Reminder Tool Relative Time
    print("\n3. Testing Reminder Tool Relative Time...")
    try:
        from ai_assistant.custom_tools.generated import set_reminder
        importlib.reload(set_reminder)
        
        with patch('asyncio.sleep', new_callable=MagicMock) as mock_sleep:
             # Make it an async mock
             f = asyncio.Future()
             f.set_result(None)
             mock_sleep.return_value = f
             
             # Test Relative Time
             print("   Testing 'in 1 minute'...")
             result = await set_reminder.set_reminder("in 1 minute", "test relative")
             
             if "Reminder: It's time to test relative!" in result:
                 print("SUCCESS: Relative time 'in 1 minute' parsed and scheduled.")
             else:
                 print(f"FAILURE: Relative time test returned: {result}")

             # Test Error Case
             print("   Testing invalid format...")
             result_error = await set_reminder.set_reminder("invalid time", "fail")
             if "Error" in result_error:
                  print("SUCCESS: Invalid format correctly handled.")

    except Exception:
        print("FAILURE: verify_reminder_tool crashed:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(verify_fixes())
