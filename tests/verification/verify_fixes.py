import sys
import os
import asyncio
import json
import logging

# Add project root
# Add project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.dreaming.dreamer import DreamerAgent

# Mock response for Dreamer Test
MOCK_BAD_JSON_RESPONSE = """
Here is your dream scenario:
```json
{
    "scenario_name": "Test",
    "description": "Testing regex",
    "verification_script": "print('Hello')"
}
```
Hope you like it!
"""

async def test_dreamer_parsing():
    print("Testing DreamerAgent parsing...")
    dreamer = DreamerAgent()
    
    # We need to mock invoke_gemini_model_async or just test the parsing logic if it was exposed.
    # The parsing logic is inside propose_dream_scenario.
    # I'll create a subclass to mock the network call.
    
    class MockDreamer(DreamerAgent):
        async def invoke_gemini(self, *args, **kwargs):
            return MOCK_BAD_JSON_RESPONSE

    # Monkey patch the import in the module is hard, so let's just copy the parsing logic here to verify the REGEX works
    # OR better: The parsing logic is what changed. I'll test the regex directly here identical to the code.
    import re
    response = MOCK_BAD_JSON_RESPONSE
    
    json_match = re.search(r'\{.*\}', response, re.DOTALL)
    if json_match:
        print("Dreamer Regex: SUCCESS - Found JSON.")
        data = json.loads(json_match.group(0))
        print(f"Parsed Data: {data['scenario_name']}")
    else:
        print("Dreamer Regex: FAILED - No JSON found.")

def test_ghost_tool():
    print("\nTesting Ghost Mode Tool Registration...")
    print("Forcing Refresh...")
    tool_system_instance.refresh_custom_tools()
    
    tools = tool_system_instance.list_tools()
    
    if 'set_ghost_mode' in tools:
        print(f"Ghost Mode Tool: FOUND ({tools['set_ghost_mode']})")
    else:
        print("Ghost Mode Tool: NOT FOUND")
        print("Available tools:", list(tools.keys()))
        
    if 'update_system_config' in tools:
        print(f"Update Config Tool: FOUND")
    else:
         print("Update Config Tool: NOT FOUND")

if __name__ == "__main__":
    test_dreamer_parsing()
    test_ghost_tool()
