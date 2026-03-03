import sys
import os

# Add project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_assistant.tools.tool_system import tool_system_instance

def list_all_tools():
    print("Loading tools...")
    # Refresh to be sure
    # tool_system_instance.refresh_custom_tools() 
    # Don't refresh if not needed, just list what's loaded by default
    
    tools = tool_system_instance.list_tools()
    print(f"\nFound {len(tools)} tools:")
    for name, desc in tools.items():
        print(f" - {name}")

if __name__ == "__main__":
    list_all_tools()
