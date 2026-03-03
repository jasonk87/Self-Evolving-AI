import sys
import os
import asyncio

# Add project root to sys.path
# The script is expected to be in /app/, so Self-Evolving-Agent... is one level down.
project_root_parts = ["Self-Evolving-Agent-feat-learning-module", "Self-Evolving-Agent-feat-chat-history-context"]
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), *project_root_parts))

if project_root not in sys.path:
    sys.path.insert(0, project_root)

print(f"Python version: {sys.version}")
print(f"Project root: {project_root}")
print(f"sys.path: {sys.path}")


# Initialize components
notification_manager_instance = None
task_manager_instance = None
_handle_code_generation_and_registration_func = None

print("\n--- Initializing Components ---")
try:
    from ai_assistant.core.notification_manager import NotificationManager
    notification_manager_instance = NotificationManager()
    print("NotificationManager initialized.")

    from ai_assistant.core.task_manager import TaskManager
    task_manager_instance = TaskManager(notification_manager=notification_manager_instance)
    print("TaskManager initialized.")

    # Attempt to import the target function
    from ai_assistant.communication.cli import _handle_code_generation_and_registration
    _handle_code_generation_and_registration_func = _handle_code_generation_and_registration
    print("_handle_code_generation_and_registration imported successfully.")

except Exception as e:
    print(f"Error during component initialization: {type(e).__name__} - {e}")
    # Exit if essential components fail, to avoid NoneErrors later
    # Check specifically for the function we need to test
    if not _handle_code_generation_and_registration_func:
        print("Essential function _handle_code_generation_and_registration failed to import. Aborting.")
        sys.exit(1)
    if not task_manager_instance:
        print("TaskManager failed to initialize. Aborting.")
        sys.exit(1)
    if not notification_manager_instance:
        print("NotificationManager failed to initialize. Aborting.")
        sys.exit(1)


tool_desc = "a tool that takes two integers, 'x' and 'y', and returns their sum. Name it 'add_integers_tool'. The function name should be 'add_two_integers'."

print("\n--- Attempting Tool Generation ---")
if _handle_code_generation_and_registration_func:
    try:
        asyncio.run(_handle_code_generation_and_registration_func(
            tool_description_for_generation=tool_desc, # Corrected keyword argument
            task_manager=task_manager_instance,
            notification_manager=notification_manager_instance
        ))
        print("Tool generation function call completed.")
    except EOFError:
        print("Tool generation proceeded until user input was required, and EOFError occurred as expected.")
    except Exception as e:
        print(f"An error occurred during tool generation: {type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()
else:
    # This case should be caught by the sys.exit above if import fails.
    print("Tool generation function _handle_code_generation_and_registration_func not available.")

print("\n--- File Check Information ---")
print("Informational: Check manually if a tool was saved in 'Self-Evolving-Agent-feat-learning-module/Self-Evolving-Agent-feat-chat-history-context/ai_assistant/custom_tools/generated/'")
print("Informational: Check manually if a test scaffold was saved in 'Self-Evolving-Agent-feat-learning-module/Self-Evolving-Agent-feat-chat-history-context/tests/custom_tools/generated/'")

generated_tools_dir = os.path.join(project_root, "ai_assistant", "custom_tools", "generated")
generated_tests_dir = os.path.join(project_root, "tests", "custom_tools", "generated")

if os.path.exists(generated_tools_dir):
    print(f"\n--- Listing files in {generated_tools_dir} (if any) ---")
    generated_files = os.listdir(generated_tools_dir)
    found_tool_file = False
    if generated_files:
        for f_name in generated_files:
            if f_name != "__init__.py" and "__pycache__" not in f_name:
                print(f"- {f_name}")
                found_tool_file = True
    if not found_tool_file:
        print("No relevant tool files found in generated tools directory.")
else:
    print(f"\nGenerated tools directory does not exist: {generated_tools_dir}")

if os.path.exists(generated_tests_dir):
    print(f"\n--- Listing files in {generated_tests_dir} (if any) ---")
    generated_test_files = os.listdir(generated_tests_dir)
    found_test_file = False
    if generated_test_files:
        for f_name in generated_test_files:
            if f_name != "__init__.py" and "__pycache__" not in f_name:
                print(f"- {f_name}")
                found_test_file = True
    if not found_test_file:
        print("No relevant test files found in generated tests directory.")
else:
    print(f"\nGenerated tests directory does not exist: {generated_tests_dir}")

print("\n--- Script Finished ---")
