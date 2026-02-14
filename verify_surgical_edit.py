
import os
import ast
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# Mock dependencies before importing the module to avoid side effects
with patch.dict('sys.modules', {
    'ai_assistant.core.reviewer': MagicMock(),
    'ai_assistant.core.critical_reviewer': MagicMock(),
    'ai_assistant.core.task_manager': MagicMock(),
}):
    # Import the function to test
    # We need to import the module, but we want to bypass the imports inside it that might fail or trigger things.
    # Actually, we can just import the module if the environment is set up reasonably well.
    # But `ai_assistant.core.self_modification` imports `diff_utils`.
    pass

import sys
sys.path.append(os.getcwd())

from ai_assistant.core.self_modification import edit_function_source_code

# Mock CriticalReviewCoordinator to always return Approved
async def mock_review(*args, **kwargs):
    return True, [{"status": "approved", "comments": "LGTM"}]

@patch('ai_assistant.core.self_modification.CriticalReviewCoordinator')
@patch('ai_assistant.core.self_modification.ReviewerAgent')
@patch('ai_assistant.core.self_modification.RefinementAgent')
def run_test(MockRefinement, MockReviewer, MockCoordinator):

    # Setup Mock Coordinator instance
    mock_coordinator_instance = MockCoordinator.return_value
    mock_coordinator_instance.request_critical_review = AsyncMock(side_effect=mock_review)

    # Create a dummy file
    filename = "temp_test_surgical.py"
    original_content = """import os

# A comment that should remain
def my_function(x):
    # This comment is inside the function
    print("Old Logic")
    return x * 2

# Another comment that should remain
def other_function():
    pass
"""
    with open(filename, "w") as f:
        f.write(original_content)

    new_code = """
import sys # New import

def my_function(x):
    print("New Logic")
    return x * 3
"""

    # Run the edit
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print("Running edit_function_source_code...")

    # We pass module_path as a file path equivalent for the robust resolver to work locally
    # The robust resolver tries to resolve module paths.
    # If we pass "temp_test_surgical", it might look for temp_test_surgical.py

    try:
        result = loop.run_until_complete(edit_function_source_code(
            module_path="temp_test_surgical",
            function_name="my_function",
            new_code_string=new_code,
            project_root_path=os.getcwd(),
            change_description="Testing surgical edit"
        ))
    except Exception as e:
        print(f"Execution failed: {e}")
        import traceback
        traceback.print_exc()
        return

    print(f"Result: {result}")

    # Verify content
    with open(filename, "r") as f:
        final_content = f.read()

    print("\n--- Final Content ---")
    print(final_content)
    print("---------------------")

    # Assertions
    if "# A comment that should remain" not in final_content:
        print("FAIL: Pre-function comment lost.")
    elif "# Another comment that should remain" not in final_content:
        print("FAIL: Post-function comment lost.")
    elif 'print("New Logic")' not in final_content:
        print("FAIL: Code not updated.")
    elif 'import sys' not in final_content:
        print("FAIL: New import not added.")
    else:
        print("SUCCESS: Comments preserved and code updated.")

    # Cleanup
    if os.path.exists(filename):
        os.remove(filename)
    if os.path.exists(filename + ".bak"):
        os.remove(filename + ".bak")

if __name__ == "__main__":
    run_test()
