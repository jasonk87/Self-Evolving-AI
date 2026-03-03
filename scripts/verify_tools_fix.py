import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

print(f"Added {project_root} to sys.path")

try:
    print("Attempting to import refresh_custom_tools from ai_assistant.tools.tool_system...")
    from ai_assistant.tools.tool_system import refresh_custom_tools
    print("Import successful.")
    
    print("Running refresh_custom_tools()...")
    result = refresh_custom_tools()
    
    if result:
        print("Tool registry refreshed successfully.")
    else:
        print("refresh_custom_tools returned False.")
        
except ImportError as e:
    print(f"CRITICAL ERROR: ImportError - {e}")
    print("Detailed info:")
    import traceback
    traceback.print_exc()
except Exception as e:
    print(f"CRITICAL ERROR: {e}")
    import traceback
    traceback.print_exc()
