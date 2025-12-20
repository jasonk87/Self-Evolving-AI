import sys
import os
import asyncio

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ai_assistant.tools.tool_system import ToolSystem

async def check_tools():
    try:
        ts = ToolSystem()
        print(f"ToolSystem loaded. Total tools: {len(ts._tool_registry)}")
        
        has_approvals = False
        has_converse = False
        
        print("\n--- Loaded Tools ---")
        for tool_name, data in ts._tool_registry.items():
            desc = data.get('description', 'N/A')
            print(f"{tool_name}: {desc[:100]}")
            
            if "approval" in tool_name or "suggestion" in tool_name:
                has_approvals = True
            if "converse" in tool_name:
                has_converse = True

        print("\n--- Check Results ---")
        print(f"Has approval tools: {has_approvals}")
        print(f"Has converse tool: {has_converse}")

    except Exception as e:
        print(f"Error initializing ToolSystem: {e}")

if __name__ == "__main__":
    asyncio.run(check_tools())
