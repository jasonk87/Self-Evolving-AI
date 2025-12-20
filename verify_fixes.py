import sys
import os

# Add the project root to sys.path
sys.path.append(os.getcwd())

def verify_system_tools():
    print("--- Verifying system_tools.list_available_tools ---")
    try:
        from ai_assistant.custom_tools.system_tools import list_available_tools
        tools_list = list_available_tools()
        if "Available Tools:" in tools_list:
            print("SUCCESS: list_available_tools returned a list.")
            # print(tools_list[:200] + "...") # Print first 200 chars
        else:
            print(f"FAILURE: list_available_tools returned unexpected output: {tools_list[:100]}...")
            return False
    except Exception as e:
        print(f"FAILURE: list_available_tools raised exception: {e}")
        return False
    return True

def verify_awareness_tools():
    print("\n--- Verifying awareness_tools import ---")
    try:
        from ai_assistant.custom_tools.awareness_tools import get_self_awareness_info_and_converse
        print("SUCCESS: Successfully imported get_self_awareness_info_and_converse")
    except ImportError as e:
        print(f"FAILURE: Could not import awareness_tools: {e}")
        return False
    except Exception as e:
        print(f"FAILURE: Unexpected error importing awareness_tools: {e}")
        return False
    return True

if __name__ == "__main__":
    tools_ok = verify_system_tools()
    awareness_ok = verify_awareness_tools()
    
    if tools_ok and awareness_ok:
        print("\nALL VERIFICATION CHECKS PASSED.")
        sys.exit(0)
    else:
        print("\nVERIFICATION FAILED.")
        sys.exit(1)
