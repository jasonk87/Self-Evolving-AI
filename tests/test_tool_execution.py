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

tool_system_instance = None
notification_manager_instance = None
task_manager_instance = None

print("\n--- Initializing Components ---")
try:
    from ai_assistant.tools.tool_system import tool_system_instance
    print("ToolSystem initialized successfully.")

    from ai_assistant.core.notification_manager import NotificationManager
    notification_manager_instance = NotificationManager()
    print("NotificationManager initialized successfully.")

    from ai_assistant.core.task_manager import TaskManager
    task_manager_instance = TaskManager(notification_manager=notification_manager_instance)
    print("TaskManager initialized successfully.")

except Exception as e:
    print(f"Error during component initialization: {type(e).__name__} - {e}")
    print("Aborting script due to initialization failure.")
    sys.exit(1)

tool_to_test = ""
tool_args_to_use = tuple()
tool_name_for_print = "" # For cleaner print messages

if tool_system_instance:
    available_tools = tool_system_instance.list_tools()
    if not available_tools:
        print("No tools available in ToolSystem. Cannot proceed.")
        tool_to_test = None
    elif "greet_user" in available_tools:
        tool_to_test = "greet_user"
        tool_args_to_use = ("Jules",) # Args should be a tuple
        tool_name_for_print = "greet_user"
        print(f"Selected tool for testing: {tool_name_for_print}")
    elif "add_numbers" in available_tools:
        tool_to_test = "add_numbers"
        tool_args_to_use = (5, 7) # Args should be a tuple
        tool_name_for_print = "add_numbers"
        print(f"Selected tool for testing: {tool_name_for_print} (since greet_user was not found)")
    else:
        # Try to pick the first available tool if specific ones are not found
        # This makes the test more resilient if tool names change slightly
        first_tool_name = list(available_tools.keys())[0]
        tool_to_test = first_tool_name
        tool_name_for_print = first_tool_name
        # We don't know the args for this first tool, so we'll try executing it without args
        # or with common default args if that makes sense. For now, empty tuple for args.
        tool_args_to_use = ()
        print(f"Warning: Neither 'greet_user' nor 'add_numbers' found. Selecting first available tool: '{tool_name_for_print}'. Attempting execution without specific args.")
        # If this tool requires args, it will likely fail, but the test will still run.

if tool_system_instance and tool_to_test:
    print(f"\n--- Attempting Tool Execution for '{tool_name_for_print}' ---")
    try:
        result = asyncio.run(tool_system_instance.execute_tool(
            name=tool_to_test,
            args=tool_args_to_use,
            kwargs={},
            task_manager=task_manager_instance,
            notification_manager=notification_manager_instance
        ))
        print(f"Execution Result of '{tool_name_for_print}': {result!r}")
    except Exception as e:
        print(f"An error occurred during tool execution of '{tool_name_for_print}': {type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()
elif not tool_system_instance:
    # This case should have been caught by sys.exit(1) earlier
    print("ToolSystem could not be initialized. Skipping tool execution.")
else:
    print("No suitable tool selected or available for execution test.")

print("\n--- Script Finished ---")
