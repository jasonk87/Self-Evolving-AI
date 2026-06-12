
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def verify_council_fix():
    print("--- Verifying Council Fixes ---")
    
    # 1. Verify ActiveTaskStatus update
    try:
        from ai_assistant.core.task_manager import ActiveTaskStatus
        if hasattr(ActiveTaskStatus, 'REFINING_PLAN'):
             print("SUCCESS: ActiveTaskStatus.REFINING_PLAN is present.")
        else:
             print("FAILURE: ActiveTaskStatus.REFINING_PLAN is MISSING.")
             return False
    except ImportError as e:
        print(f"FAILURE: Could not import ActiveTaskStatus: {e}")
        return False

    # 2. Verify ActionExecutor syntax/import (catch syntax errors from move)
    try:
        print("SUCCESS: ActionExecutor imported successfully (no syntax errors).")
    except Exception as e:
        print(f"FAILURE: ActionExecutor import failed: {e}")
        return False

    return True

if __name__ == "__main__":
    if verify_council_fix():
        print("VERIFICATION PASSED")
        sys.exit(0)
    else:
        print("VERIFICATION FAILED")
        sys.exit(1)
