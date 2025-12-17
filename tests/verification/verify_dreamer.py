import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.getcwd())

from ai_assistant.dreaming.dreamer import DreamerAgent

async def test_dreamer():
    print("Initializing DreamerAgent...")
    agent = DreamerAgent()
    
    # Mock tool code (simple subtraction tool)
    tool_name = "mock_subtract"
    tool_code = """
def mock_subtract(a: int, b: int) -> int:
    return a - b
"""
    print(f"Dreaming about tool '{tool_name}'...")
    
    scenario = await agent.propose_dream_scenario(tool_name, tool_code)
    
    if scenario:
        print("\nDream Generated Successfully!")
        print(f"Scenario Name: {scenario.get('scenario_name')}")
        print(f"Description: {scenario.get('description')}")
        print("\nVerification Script Preview:")
        print(scenario.get("verification_script")[:200] + "...")
    else:
        print("\nDream Generation Failed.")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_dreamer())
