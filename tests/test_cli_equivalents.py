import sys
import os
import importlib # For reloading modules if necessary

# Add project root to sys.path
# The script is expected to be in /app/, so Self-Evolving-Agent... is one level down.
project_root_parts = ["Self-Evolving-Agent-feat-learning-module", "Self-Evolving-Agent-feat-chat-history-context"]
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), *project_root_parts))

if project_root not in sys.path:
    sys.path.insert(0, project_root)

print(f"Python version: {sys.version}")
print(f"Project root: {project_root}")
print(f"sys.path: {sys.path}")

# Initialize components with error handling
tool_system_instance = None
project_manager_module = None # Renamed to avoid conflict if project_manager is a class
suggestion_manager_module = None
status_reporting_module = None # Renamed
awareness_tools_module = None # Renamed
notification_manager_instance = None # For awareness_tools
task_manager_instance = None # For status_reporting (though it might use globals)

print("\n--- Initializing Components ---")
try:
    from ai_assistant.tools.tool_system import tool_system_instance
    print("ToolSystem initialized successfully.")
except Exception as e:
    print(f"Error initializing ToolSystem: {type(e).__name__} - {e}")

try:
    from ai_assistant.core import project_manager as pm_module
    project_manager_module = pm_module
    # If project_manager is a module with functions like list_projects, this is fine.
    # If it's a class that needs instantiation, this would need to be pm = pm_module.ProjectManager()
    # Based on previous usage, it seems to be a module with top-level functions.
    print("ProjectManager module imported successfully.")
except Exception as e:
    print(f"Error importing ProjectManager module: {type(e).__name__} - {e}")

try:
    from ai_assistant.core import suggestion_manager as sm_module
    suggestion_manager_module = sm_module
    print("SuggestionManager module imported successfully.")
except Exception as e:
    print(f"Error importing SuggestionManager module: {type(e).__name__} - {e}")

try:
    from ai_assistant.core import status_reporting as sr_module
    status_reporting_module = sr_module
    print("StatusReporting module imported successfully.")
except Exception as e:
    print(f"Error importing StatusReporting module: {type(e).__name__} - {e}")

try:
    from ai_assistant.custom_tools import awareness_tools as at_module
    awareness_tools_module = at_module
    print("AwarenessTools module imported successfully.")
except Exception as e:
    print(f"Error importing AwarenessTools module: {type(e).__name__} - {e}")

try:
    from ai_assistant.core.notification_manager import NotificationManager
    notification_manager_instance = NotificationManager()
    print("NotificationManager instance created for AwarenessTools.")
except Exception as e:
    print(f"Error creating NotificationManager instance: {type(e).__name__} - {e}")

# Test /tools list
print("\n--- Testing '/tools list' equivalent ---")
if tool_system_instance:
    try:
        tools = tool_system_instance.list_tools()
        if tools:
            print(f"Found {len(tools)} tools.")
            # for name, desc in tools.items():
            #     print(f"{name}: {desc}") # Printing all can be too verbose
            print("Sample tool:", list(tools.items())[0] if tools else "None")
        else:
            print("No tools found.")
    except Exception as e:
        print(f"Error listing tools: {type(e).__name__} - {e}")
else:
    print("ToolSystem could not be initialized. Skipping tools list.")

# Test /projects list
print("\n--- Testing '/projects list' equivalent ---")
if project_manager_module:
    try:
        # Assuming list_projects is a function in the project_manager_module
        projects = project_manager_module.list_projects()
        if projects:
            print(f"Found {len(projects)} projects.")
            # for proj_path in projects: # list_projects returns paths
            #     print(proj_path)
            print("Sample project path:", projects[0] if projects else "None")
        else:
            print("No projects found.")
    except Exception as e:
        print(f"Error listing projects: {type(e).__name__} - {e}")
else:
    print("ProjectManager module could not be imported. Skipping projects list.")

# Test /suggestions list and status
print("\n--- Testing '/suggestions list' equivalent ---")
if suggestion_manager_module and awareness_tools_module and notification_manager_instance:
    try:
        print("--- Suggestions Status ---")
        # get_suggestions_summary_status might need NotificationManager too, or other setup
        # status = suggestion_manager_module.get_suggestions_summary_status(notification_manager_instance)
        # For now, assuming it might work without if NM is optional or implicitly handled by other means.
        # Based on function signature, it seems it does not take arguments.
        status = suggestion_manager_module.get_suggestions_summary_status()
        print(status)
    except Exception as e:
        print(f"Error getting suggestions status: {type(e).__name__} - {e}")

    try:
        print("\n--- Pending Suggestions ---")
        # Corrected: list_formatted_suggestions does not take notification_manager
        pending_suggestions = awareness_tools_module.list_formatted_suggestions(
            status_filter='pending'
        )
        # list_formatted_suggestions returns a list of dicts
        if pending_suggestions and isinstance(pending_suggestions, list) and len(pending_suggestions) > 0 :
             print(f"Found {len(pending_suggestions)} pending suggestions.")
             # for suggestion_dict in pending_suggestions:
             #   print(suggestion_dict)
             print("Sample pending suggestion:", pending_suggestions[0] if pending_suggestions else "None")
        elif isinstance(pending_suggestions, list) and len(pending_suggestions) == 0:
             print("No pending suggestions found.")
        else: # If it's not a list or some other unexpected return
             print(f"No pending suggestions found or an issue with the returned data: {pending_suggestions!r}")
    except Exception as e:
        print(f"Error listing pending suggestions: {type(e).__name__} - {e}")
        print("Note: list_formatted_suggestions relies on suggestion_manager.list_suggestions().")
else:
    print("SuggestionManager, AwarenessTools, or NotificationManager could not be initialized. Skipping suggestions tests.")

# Test /status components
print("\n--- Testing '/status' components equivalent ---")
if status_reporting_module:
    try:
        print("\nTools Status:")
        # Corrected: get_tools_status does not take arguments
        print(status_reporting_module.get_tools_status())

        print("\nProjects Status:")
        # get_projects_status might rely on project_manager_module
        # Assuming it imports project_manager itself or we need to pass it.
        # The function signature of get_projects_status is (None)
        print(status_reporting_module.get_projects_status())


        print("\nSystem Status (Note: may be inaccurate due to CLI dependencies):")
        # get_system_status in status_reporting.py seems to directly access
        # cli._task_manager_cli_instance and cli._notification_manager_cli_instance
        # This will likely fail if cli.py is not run or these are not set.
        # We will try to provide dummy/default instances if possible.
        try:
            from ai_assistant.core.task_manager import TaskManager
            task_manager_instance = TaskManager(None) # Requires Orchestrator, pass None for now
            print("TaskManager instance created for StatusReporting.")
        except Exception as e:
            print(f"Error creating TaskManager instance for StatusReporting: {type(e).__name__} - {e}")

        # We already have notification_manager_instance

        # The problem is that status_reporting.get_system_status directly accesses
        # from ai_assistant.communication import cli
        # cli._task_manager_cli_instance
        # cli._notification_manager_cli_instance
        # This is a hard dependency on the CLI state.
        # This call is expected to fail.
        system_status_output = status_reporting_module.get_system_status(
            active_tasks_count=0, # Placeholder
            # task_manager=task_manager_instance, # Not taken by function
            # notification_manager=notification_manager_instance # Not taken by function
        )
        print(system_status_output)

    except Exception as e:
        print(f"Error during status reporting: {type(e).__name__} - {e}")
        print("Note: StatusReporting functions might have hard dependencies on CLI state or other global instances.")
else:
    print("StatusReporting module could not be imported. Skipping status tests.")

print("\n--- Script Finished ---")
