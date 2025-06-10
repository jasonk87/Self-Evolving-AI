# ai_assistant/tools/tool_system.py
import importlib
import os
import sys
import json
import inspect
import asyncio
from typing import Callable, Dict, Any, Optional, Tuple, List # TYPE_CHECKING removed
from ai_assistant.config import is_debug_mode, get_data_dir # Import get_data_dir
from ai_assistant.core.self_modification import get_function_source_code
from ..core.task_manager import TaskManager # Added for type hinting
from ..core.notification_manager import NotificationManager # Made unconditional

# --- Constants ---
DEFAULT_TOOLS_FILE_DIR = get_data_dir() # Use centralized data directory from config
DEFAULT_TOOL_REGISTRY_FILE = os.path.join(DEFAULT_TOOLS_FILE_DIR, "tool_registry.json") # Standardized name


# --- Custom Exceptions ---
class ToolNotFoundError(Exception):
    """Raised when a tool is not found in the registry."""
    pass

class ToolAlreadyRegisteredError(Exception):
    """Raised when trying to register a tool that already exists."""
    pass

class ToolExecutionError(Exception):
    """Raised when a tool fails to load or execute."""
    pass

class ToolSystem:
    def __init__(self, tool_registry_file: Optional[str] = None):
        self._tool_registry: Dict[str, Dict[str, Any]] = {}
        # Import is_debug_mode here or ensure it's available if used in methods called by __init__
        self._persisted_tool_metadata_file = tool_registry_file or DEFAULT_TOOL_REGISTRY_FILE

        if is_debug_mode():
            print(f"ToolSystem: Initializing with registry file: {self._persisted_tool_metadata_file}")
        self.load_persisted_tools()
        self._register_system_tools()

        # Define a list of custom tool modules to discover
        # Each tuple is (module_import_path, friendly_filename_for_logging)
        custom_tool_modules_to_discover = [
            ("ai_assistant.custom_tools.my_extra_tools", "my_extra_tools.py"),
            ("ai_assistant.custom_tools.awareness_tools", "awareness_tools.py"),
            ("ai_assistant.custom_tools.config_management_tools", "config_management_tools.py"),
            ("ai_assistant.custom_tools.conversational_tools", "conversational_tools.py"),
            ("ai_assistant.custom_tools.project_management_tools", "project_management_tools.py"),
            ("ai_assistant.custom_tools.project_execution_tools", "project_execution_tools.py"),
            ("ai_assistant.custom_tools.code_execution_tools", "code_execution_tools.py"),
            ("ai_assistant.custom_tools.file_system_tools", "file_system_tools.py"),
            ("ai_assistant.custom_tools.git_tools", "git_tools.py"),
            ("ai_assistant.custom_tools.knowledge_tools", "knowledge_tools.py"),
            ("ai_assistant.custom_tools.meta_programming_tools", "meta_programming_tools.py"),
            ("ai_assistant.custom_tools.suggestion_management_tools", "suggestion_management_tools.py"), # Added
            ("ai_assistant.custom_tools.generated", "generated_tools_module"),
        ]

        any_new_tools_registered_overall = False
        for module_import_path, module_filename in custom_tool_modules_to_discover:
            try:
                # Attempt to import the module
                module_to_inspect = importlib.import_module(module_import_path)
                # Discover and register tools from this module
                if self._discover_and_register_custom_tools(module_to_inspect, module_import_path):
                    if is_debug_mode():
                        print(f"ToolSystem: New custom tools discovered from {module_filename}. Triggering save.")
                    any_new_tools_registered_overall = True
            except ImportError:
                if is_debug_mode(): # Less noise if a custom module is optionally missing
                    print(f"ToolSystem: Could not import {module_filename} (path: {module_import_path}), skipping custom tool discovery from it.")
            except Exception as e:
                # Keep this as a regular print or change to logger.warning, as it's an unexpected error.
                print(f"ToolSystem: Warning - Error during custom tool discovery from {module_filename} (path: {module_import_path}): {e}")

        self.register_example_tools() # Register built-in example tools

        # Save the registry only if new tools were discovered and registered during this initialization
        if any_new_tools_registered_overall:
             self.save_registered_tools()
        if is_debug_mode():
            print(f"ToolSystem: Initialization complete. {len(self._tool_registry)} tools registered.")

    def _discover_and_register_custom_tools(self, module_to_inspect, module_path_str: str) -> bool:
        """
        Discovers and registers public functions from a given module as tools.
        Skips functions starting with '_' or not defined directly in the module.
        If a tool with the same name and module_path already exists, it's skipped.
        Returns True if any new tools were registered from this module, False otherwise.
        """
        new_tools_registered_in_this_module = False
        if is_debug_mode():
            print(f"ToolSystem: Discovering custom tools from module: {module_path_str}")
        for name, func_object in inspect.getmembers(module_to_inspect, inspect.isfunction):
            if name.startswith("_"): # Skip private/internal functions
                continue
            # Ensure the function is defined in the module being inspected, not imported into it
            if func_object.__module__ != module_to_inspect.__name__:
                continue

            # Check if this specific tool (name + module_path) is already registered (e.g., from persisted data)
            if name in self._tool_registry and self._tool_registry[name].get('module_path') == module_path_str:
                # Tool already loaded, likely from tools.json. No need to re-register from discovery.
                continue

            docstring = inspect.getdoc(func_object) or "No description available."
            # Ensure description is a single line for brevity in some displays, or take first line.
            first_line_of_docstring = docstring.splitlines()[0] if docstring else "No description available."

            tool_description_for_registration = first_line_of_docstring
            discovered_schema_details = None
            schema_variable_name = f"{name.upper()}_SCHEMA"
            if hasattr(module_to_inspect, schema_variable_name):
                potential_schema = getattr(module_to_inspect, schema_variable_name)
                if isinstance(potential_schema, dict) and "name" in potential_schema and "description" in potential_schema:
                    discovered_schema_details = potential_schema
                    tool_description_for_registration = discovered_schema_details.get("description", first_line_of_docstring)
                    if is_debug_mode():
                        print(f"ToolSystem: Found schema '{schema_variable_name}' for tool '{name}'. Using schema description.")
                elif is_debug_mode():
                    print(f"ToolSystem: Found schema variable '{schema_variable_name}' for tool '{name}', but it's not a valid schema dict.")


            if is_debug_mode():
                print(f"ToolSystem: Attempting to register discovered custom tool '{name}' from '{module_path_str}'.")
            try:
                self.register_tool(
                    tool_name=name,
                    description=tool_description_for_registration,
                    module_path=module_path_str,
                    function_name_in_module=name,
                    tool_type="custom_discovered",
                    func_callable=func_object,
                    schema_details=discovered_schema_details # Pass schema
                )
                new_tools_registered_in_this_module = True
                if is_debug_mode():
                    print(f"ToolSystem: Successfully registered custom tool '{name}'.")
            except ToolAlreadyRegisteredError as e:
                # This might happen if a tool with the same name but different origin (e.g. example tool) exists.
                # The actual warning about metadata difference is handled in register_tool.
                if is_debug_mode():
                    print(f"ToolSystem: Info during discovery for custom tool '{name}': {e}")
            except Exception as e: # Catch any other error during registration
                # This is an error, so it should probably always be visible or logged as an error.
                print(f"ToolSystem: Error - Failed to register discovered custom tool '{name}': {e}")

        if new_tools_registered_in_this_module:
            if is_debug_mode():
                print(f"ToolSystem: Finished discovery for '{module_path_str}'. New tools were registered in this pass.")
        else:
            if is_debug_mode():
                print(f"ToolSystem: Finished discovery for '{module_path_str}'. No new tools were registered in this pass. ")
        return new_tools_registered_in_this_module


    def _system_update_tool_metadata_impl(self, tool_name: str, new_description: Optional[str] = None) -> bool:
        """
        Implementation logic for updating a tool's metadata.
        Modifies self._tool_registry and persists changes.
        """
        if tool_name not in self._tool_registry:
            print(f"Error (system_update_tool_metadata): Tool '{tool_name}' not found in registry.")
            return False
        tool_entry = self._tool_registry[tool_name]
        updated = False
        if new_description is not None:
            if tool_entry.get('description') != new_description:
                tool_entry['description'] = new_description                
                if is_debug_mode():
                    print(f"SystemTool: Updated description for tool '{tool_name}'.")
                updated = True
            else:
                if is_debug_mode():
                    print(f"SystemTool: New description for '{tool_name}' is same as old; no update made.")
        # Placeholder for other metadata updates
        # if new_module_path is not None: tool_entry['module_path'] = new_module_path; updated = True
        # if new_function_name is not None: tool_entry['function_name'] = new_function_name; updated = True

        if updated:
            if self.save_registered_tools():
                if is_debug_mode():
                    print(f"SystemTool: Tool registry saved successfully after updating '{tool_name}'.")
                return True
            else: # pragma: no cover
                print(f"Error (system_update_tool_metadata): Failed to save tool registry after updating '{tool_name}'.")
                return False
        else:
            # No actual change was made, but operation is considered "successful" in terms of not erroring.
            return True


    def _register_system_tools(self):
        """Registers tools that are internal to the ToolSystem or for system management."""
        system_tool_entry = {
            'tool_name': "system_update_tool_metadata", # Ensure tool_name is part of the entry dict
            'description': "Updates the metadata of a registered tool, such as its description. For system use. Args: tool_name (str), new_description (str, optional)",
            'type': 'system_internal', # Mark as an internal system tool
            'module_path': self.__class__.__module__, # Points to this module (ai_assistant.tools.tool_system)
            'function_name': '_system_update_tool_metadata_impl', # The actual method name
            'callable_cache': self._system_update_tool_metadata_impl, # Cache the bound method
            'is_method_on_instance': True # Flag indicating it's a method of this ToolSystem instance
        }
        # Only register if not already present, or if re-registration is desired (e.g. to update cache)
        if "system_update_tool_metadata" not in self._tool_registry:
             self._tool_registry["system_update_tool_metadata"] = system_tool_entry
        # If it is already there, this ensures the callable_cache is for the current instance,
        # which is important if ToolSystem is re-instantiated.
        elif self._tool_registry["system_update_tool_metadata"].get('is_method_on_instance'):
            self._tool_registry["system_update_tool_metadata"]['callable_cache'] = self._system_update_tool_metadata_impl
        if is_debug_mode():
            print("ToolSystem: System tools registered.")


    def register_tool(
        self,
        tool_name: str,
        description: str,
        module_path: str,
        function_name_in_module: str,
        tool_type: str = "dynamic",
        func_callable: Optional[Callable] = None,
        schema_details: Optional[Dict[str, Any]] = None # New parameter
    ) -> bool:
        """
        Registers a new tool or updates an existing one.
        If func_callable is provided, it's cached. Otherwise, it's loaded on first execution.
        """
        if tool_name in self._tool_registry:
            existing_tool = self._tool_registry[tool_name]
            # More robust check for re-registration: allow if all key metadata matches OR if it's an update.
            # For simplicity, if name exists, we'll treat it as an update if core details differ,
            # or just refresh the callable if provided.
            if (existing_tool['module_path'] != module_path or
                existing_tool['function_name'] != function_name_in_module or
                existing_tool['type'] != tool_type):
                if existing_tool['type'] == 'system_internal' and tool_type == 'system_internal' and func_callable is not None:
                    if is_debug_mode():
                        print(f"ToolSystem: Re-caching system tool '{tool_name}'.")
                elif is_debug_mode(): # pragma: no cover
                    # This error might be too strict if we want to allow overriding a builtin with a custom tool of same name.
                    # For now, let's log a warning and proceed with update.
                    print(
                        # Changed to print from logger.warning to avoid logger setup dependency here
                        f"ToolSystem: Info - Tool '{tool_name}' already registered with different metadata "
                        f"(module: {existing_tool['module_path']}, func: {existing_tool['function_name']}, type: {existing_tool['type']}). "
                        f"Updating with new details: (module: {module_path}, func: {function_name_in_module}, type: {tool_type})."
                    )
            # Always log if a tool is being "updated" (even if just re-registering with same info)
            if is_debug_mode():
                print(f"ToolSystem: Tool '{tool_name}' is being re-registered/updated. Description: '{description}'")
        tool_entry = {
            "tool_name": tool_name, # Added tool_name here for consistency
            "module_path": module_path,
            "function_name": function_name_in_module, # Name of the function within its module
            "description": description,
            "type": tool_type,
            "callable_cache": func_callable,
            "schema_details": schema_details # Store schema_details
        }
        self._tool_registry[tool_name] = tool_entry
        return True

    def remove_tool(self, name: str) -> bool:
        """Removes a registered tool. Returns True if successful."""
        if name in self._tool_registry:
            del self._tool_registry[name]
            if is_debug_mode():
                print(f"ToolSystem: Tool '{name}' removed from registry.")
            return True
        else:
            if is_debug_mode():
                print(f"ToolSystem: Tool '{name}' not found in registry. Cannot remove.")
            return False

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """Retrieves tool metadata from the registry."""
        return self._tool_registry.get(name)

    async def execute_tool(self, name: str, args: Tuple = (), kwargs: Optional[Dict[str, Any]] = None,
                         task_manager: Optional[TaskManager] = None,
                         notification_manager: Optional[NotificationManager] = None) -> Any: # Type hint updated
        """
        Executes a registered tool by its name.
        Loads the tool function dynamically if not already cached.
        If task_manager or notification_manager is provided and the tool accepts them, they will be passed.
        Handles both synchronous and asynchronous tool functions.
        """
        if kwargs is None:
            kwargs = {}

        tool_info = self._tool_registry.get(name)
        if not tool_info:
            raise ToolNotFoundError(f"Tool '{name}' not found.")

        func_to_execute = tool_info.get('callable_cache')

        if not func_to_execute:
            module_path = tool_info['module_path']
            function_name = tool_info['function_name']
            if is_debug_mode():
                print(f"ToolSystem: Tool '{name}': Function not cached. Attempting to load from {module_path}.{function_name}")
            try:
                module = await asyncio.to_thread(importlib.import_module, module_path)
                func_to_execute = getattr(module, function_name)
                self._tool_registry[name]['callable_cache'] = func_to_execute
                if is_debug_mode():
                    print(f"ToolSystem: Tool '{name}': Function loaded and cached successfully.")
            except ModuleNotFoundError: # pragma: no cover
                raise ToolExecutionError(f"Could not load function for tool '{name}': Module '{module_path}' not found.")
            except AttributeError: # pragma: no cover
                raise ToolExecutionError(f"Could not load function for tool '{name}': Function '{function_name}' not found in '{module_path}'.")
            except Exception as e: # pragma: no cover
                raise ToolExecutionError(f"Could not load function for tool '{name}': An unexpected error occurred - {e}")

        if not callable(func_to_execute): # pragma: no cover
             raise ToolExecutionError(f"Tool '{name}': Loaded attribute '{tool_info['function_name']}' is not callable.")

        final_kwargs = kwargs.copy()

        # Try to get signature once for both injections
        sig = None
        try:
            sig = inspect.signature(func_to_execute)
        except (ValueError, TypeError): # inspect.signature can fail for some built-ins or non-Python functions
            if is_debug_mode():
                print(f"ToolSystem: Warning - Could not inspect signature for tool '{name}'. Dependency injection might be limited.")
            pass # Proceed without signature-based injection if inspection fails

        if sig:
            if task_manager and 'task_manager' in sig.parameters:
                final_kwargs['task_manager'] = task_manager
                if is_debug_mode():
                    print(f"ToolSystem: Injecting TaskManager into tool '{name}'.")

            if notification_manager and 'notification_manager' in sig.parameters:
                final_kwargs['notification_manager'] = notification_manager
                if is_debug_mode():
                    print(f"ToolSystem: Injecting NotificationManager into tool '{name}'.")

        try:
            if is_debug_mode():
                print(f"ToolSystem: Executing tool '{name}' with args={args}, final_kwargs={final_kwargs}")
            # Check if the function is an async function
            if inspect.iscoroutinefunction(func_to_execute):
                result = await func_to_execute(*args, **final_kwargs)
            else:
                # Run synchronous function in a separate thread to avoid blocking asyncio event loop
                result = await asyncio.to_thread(func_to_execute, *args, **final_kwargs)
            if is_debug_mode():
                print(f"ToolSystem: Tool '{name}' executed successfully. Result (first 200 chars): {str(result)[:200]}")
            return result
        except Exception as e: # pragma: no cover
            print(f"ToolSystem: Error during execution of tool '{name}': {type(e).__name__} - {e}")
            # Consider re-raising a more specific ToolExecutionError or the original error
            raise ToolExecutionError(f"Error during execution of tool '{name}': {e}") from e


    def list_tools(self) -> Dict[str, str]:
        """Returns a dictionary of tool names to their descriptions."""
        return {name: tool["description"] for name, tool in self._tool_registry.items()}

    def list_tools_with_sources(self) -> Dict[str, Dict[str, str]]:
        """
        Returns a dictionary of all registered tools with their detailed metadata,
        including module_path, function_name, and description.

        The key of the outer dictionary is the tool_name (registry key).
        The inner dictionary contains 'module_path', 'function_name', 'description', and 'schema_details'.
        """
        detailed_tools = {}
        for tool_name, tool_data in self._tool_registry.items():
            detailed_tools[tool_name] = {
                "module_path": tool_data.get("module_path", "N/A"),
                "function_name": tool_data.get("function_name", tool_name),
                "description": tool_data.get("description", "No description available."),
                "schema_details": tool_data.get("schema_details") # Include schema_details
            }
        return detailed_tools

    def save_registered_tools(self) -> bool:
        """Saves the metadata of all registered tools (excluding callables and schema details if complex) to a JSON file."""
        # Ensure the 'data' directory exists
        os.makedirs(DEFAULT_TOOLS_FILE_DIR, exist_ok=True)

        data_to_save = {}
        for name, tool_data in self._tool_registry.items():
            # Do not persist system_internal tools with bound methods in callable_cache
            if tool_data.get('type') == 'system_internal' and tool_data.get('is_method_on_instance') and is_debug_mode():
                print(f"ToolSystem: Debug - Skipping persistence of system_internal tool '{name}' with instance method.")
                continue

            # Create a copy and remove the non-serializable 'callable_cache'
            serializable_data = tool_data.copy()
            serializable_data.pop('callable_cache', None) # Remove if exists
            serializable_data.pop('is_method_on_instance', None) # Remove helper flag if exists
            data_to_save[name] = serializable_data

        # Specific debug print for the problematic tool before saving
        if "get_self_awareness_info_and_converse" in data_to_save:
            print(f"[DEBUG SAVE TOOL_SYSTEM] Saving 'get_self_awareness_info_and_converse' with module_path: {data_to_save['get_self_awareness_info_and_converse'].get('module_path')}")
        elif is_debug_mode(): # Only print if debug mode is on and the tool isn't in data_to_save
            print("[DEBUG SAVE TOOL_SYSTEM] 'get_self_awareness_info_and_converse' not in data_to_save (it might be a system_internal tool with instance method).")

        try:
            with open(self._persisted_tool_metadata_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=4)
            if is_debug_mode():
                print(f"ToolSystem: Tools saved successfully to {self._persisted_tool_metadata_file}")
            return True
        except IOError as e: # pragma: no cover
            print(f"ToolSystem: Error saving tools to {self._persisted_tool_metadata_file}: {e}")
            return False
        except Exception as e: # pragma: no cover
            print(f"ToolSystem: Unexpected error saving tools: {e}")
            return False


    def load_persisted_tools(self):
        """Loads tool metadata from the persisted JSON file and registers them."""
        if is_debug_mode():
            print(f"ToolSystem: Loading persisted tools from '{self._persisted_tool_metadata_file}'...")
        if not os.path.exists(self._persisted_tool_metadata_file):
            # This is a common scenario on first run, so make it less verbose if not in debug.
            if is_debug_mode():
                print(f"ToolSystem: Tools file '{self._persisted_tool_metadata_file}' not found. No tools loaded from persistence.")
            return

        try:
            with open(self._persisted_tool_metadata_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content: # Handle empty file
                    if is_debug_mode():
                        print("ToolSystem: Tools file is empty. No tools loaded from persistence.")
                    return
                loaded_tool_metadata = json.loads(content)
        except json.JSONDecodeError as e: # pragma: no cover
            print(f"ToolSystem: Error decoding JSON from '{self._persisted_tool_metadata_file}': {e}. No tools loaded from persistence.")
            return
        except IOError as e: # pragma: no cover
            print(f"ToolSystem: Error reading tools file '{self._persisted_tool_metadata_file}': {e}. No tools loaded from persistence.")
            return

        # Specific debug print for the problematic tool after loading
        if loaded_tool_metadata and "get_self_awareness_info_and_converse" in loaded_tool_metadata:
            print(f"[DEBUG LOAD TOOL_SYSTEM] Loaded 'get_self_awareness_info_and_converse' with module_path: {loaded_tool_metadata['get_self_awareness_info_and_converse'].get('module_path')}")
        elif loaded_tool_metadata and is_debug_mode():
            print("[DEBUG LOAD TOOL_SYSTEM] 'get_self_awareness_info_and_converse' not found in loaded_tool_metadata from file.")
        elif is_debug_mode(): # Only print if debug and no metadata loaded
            print("[DEBUG LOAD TOOL_SYSTEM] loaded_tool_metadata is empty or None after attempting to read file.")

        if not loaded_tool_metadata: # Should be caught by empty content check, but good failsafe
            if is_debug_mode():
                print("ToolSystem: No tools data found in file. Skipping load from persistence.")
            return

        loaded_count = 0
        for tool_name, tool_data in loaded_tool_metadata.items():
            # Skip system_internal tools that are instance methods, as they are registered by _register_system_tools
            if tool_data.get('type') == 'system_internal' and tool_data.get('is_method_on_instance'):
                if is_debug_mode():
                    print(f"ToolSystem: Skipping load of system_internal tool '{tool_name}' from file; will be instance-registered.")
                continue

            try:
                # Ensure all necessary keys are present for registration
                # 'tool_name' is the key in loaded_tool_metadata, so it's implicitly present.
                # We use tool_data.get('tool_name', tool_name) to be safe if tool_name wasn't stored inside tool_data itself.
                self.register_tool(
                    tool_name=tool_data.get('tool_name', tool_name),
                    description=tool_data['description'],
                    module_path=tool_data['module_path'],
                    function_name_in_module=tool_data['function_name'],
                    tool_type=tool_data.get('type', 'dynamic'),
                    func_callable=None, # Callables are loaded on demand
                    schema_details=tool_data.get('schema_details') # Load schema_details
                )
                loaded_count +=1
            except ToolAlreadyRegisteredError as e: # pragma: no cover
                # This is expected if example tools are also in the JSON file, allow update.
                if is_debug_mode():
                    print(f"ToolSystem: Info while loading persisted tool '{tool_name}': {e}")
            except KeyError as e: # pragma: no cover
                print(f"ToolSystem: Error - Persisted tool '{tool_name}' has missing metadata: {e}. Skipping.")
            except Exception as e: # pragma: no cover
                print(f"ToolSystem: Error loading persisted tool '{tool_name}': {e}. Skipping.")
        if is_debug_mode() or loaded_count > 0 : # Print if debug or if any tools were actually loaded
            print(f"ToolSystem: Successfully processed {loaded_count} persisted tools from file.")

    # Import is_debug_mode at the top of the file
    def register_example_tools(self):
        """Registers a set of example tools. Idempotent."""
        current_module_obj = sys.modules[self.__class__.__module__]

        example_tools_data = [
            ("greet_user", "Greets the user. Args: name (str)", "_example_greet_user"),
            ("add_numbers", "Adds two integers. Args: a (int), b (int)", "_example_add_numbers"),
            ("multiply_numbers", "Multiplies two floats. Args: x (float), y (float)", "_example_multiply_numbers"),
            ("no_op_tool", "Does nothing, useful for default plans.", "_example_no_op_tool"),
            ("view_function_code", "Retrieves the source code of a specified function. Inputs: module_path (str), function_name (str).", "_tool_view_function_code"),
            ("simulate_edit_function_code", "Simulates editing source code. Inputs: module_path (str), function_name (str), new_code_block (str).", "_tool_simulate_edit_function_code"),
            ("maybe_fail_tool", "A tool that fails on its 1st, 3rd, etc. call and succeeds on its 2nd, 4th, etc. call.", "_example_maybe_fail_tool"),
        ]
        for tool_name, description, func_name_str in example_tools_data:
            try:
                func_callable = getattr(current_module_obj, func_name_str, None)
                if not func_callable or not callable(func_callable): # pragma: no cover
                    print(f"ToolSystem: Error - Example tool function '{func_name_str}' not found or not callable in {current_module_obj.__name__}. Skipping.")
                    continue

                self.register_tool(
                    tool_name=tool_name,
                    description=description,
                    module_path=current_module_obj.__name__,
                    function_name_in_module=func_name_str,
                    tool_type="builtin", # Mark as a built-in example tool
                    func_callable=func_callable # Cache the callable
                )
            except ToolAlreadyRegisteredError as e: # pragma: no cover
                # This is expected if tools were loaded from persistence first
                if is_debug_mode():
                    print(f"ToolSystem: Info while registering example tool '{tool_name}': {e}")
            except Exception as e: # pragma: no cover
                print(f"ToolSystem: Error registering example tool {tool_name}: {e}")
        if is_debug_mode():
            print(f"ToolSystem: Example tools registration attempt finished.")
            
# --- Example Tool Function Definitions ---
# These must be at the module level for getattr to find them.
def _example_greet_user(name: str) -> str:
    return f"Hello, {name}!"
def _example_add_numbers(a: int, b: int) -> int:
    try: 
        return int(a) + int(b)
    except ValueError:
        raise ValueError("'a' and 'b' must be integers.")
def _example_multiply_numbers(x: float, y: float) -> float:
    try:
        return float(x) * float(y)
    except ValueError:
        raise ValueError("'x' and 'y' must be floats.")
def _example_no_op_tool() -> str:
    return "No-op tool executed successfully."
_maybe_fail_tool_counter = 0
def _example_maybe_fail_tool() -> str:
    global _maybe_fail_tool_counter
    _maybe_fail_tool_counter += 1
    if _maybe_fail_tool_counter % 2 != 0:
        raise ValueError(f"Intentional failure from maybe_fail_tool on call #{_maybe_fail_tool_counter}!")
    return f"maybe_fail_tool succeeded on call #{_maybe_fail_tool_counter}."
def _tool_view_function_code(module_path: str, function_name: str) -> str:
    source_code = get_function_source_code(module_path, function_name)
    if source_code is None:
        return "Error: Could not retrieve source code. Module or function not found, or source not available."
    return source_code
def _tool_simulate_edit_function_code(module_path: str, function_name: str, new_code_block: str) -> str:
    # This is a placeholder. Actual edit_function_source_code might do more.
    print(f"Simulating edit for {module_path}.{function_name} with:\n{new_code_block}")
    return f"Simulation of code edit for '{module_path}.{function_name}' completed. No actual changes made by this simulation tool."

# Global instance, initialized once when the module is first imported.
tool_system_instance = ToolSystem()

# --- Module-level convenience functions (wrappers around the instance methods) ---
def register_tool(
    tool_name: str, description: str, module_path: str, function_name_in_module: str,
    tool_type: str = "dynamic", func_callable: Optional[Callable] = None
) -> bool:
    return tool_system_instance.register_tool(
        tool_name, description, module_path, function_name_in_module, tool_type, func_callable
    )

def remove_tool(name: str) -> bool:
    """Removes a registered tool. Returns True if successful."""
    return tool_system_instance.remove_tool(name)

def get_tool(name: str) -> Optional[Dict[str, Any]]:
    return tool_system_instance.get_tool(name)

# Update module-level wrapper if it's intended for external use and needs this new param.
# For now, assuming direct tool_system_instance.execute_tool is used more internally where task_manager is available.
async def execute_tool(name: str, args: Tuple = (), kwargs: Optional[Dict[str, Any]] = None,
                       task_manager: Optional[TaskManager] = None,
                       notification_manager: Optional[NotificationManager] = None) -> Any: # Type hint updated
    return await tool_system_instance.execute_tool(name, args, kwargs,
                                                  task_manager=task_manager,
                                                  notification_manager=notification_manager)
def list_tools() -> Dict[str, str]:
    return tool_system_instance.list_tools()
def save_registered_tools() -> bool: # Should primarily be called internally by ToolSystem
    return tool_system_instance.save_registered_tools()
def load_persisted_tools(): # For explicit reload if needed, __init__ handles initial
    tool_system_instance.load_persisted_tools()
def register_example_tools(): # For explicit re-registration if needed
    tool_system_instance.register_example_tools()

def list_tools_with_sources() -> Dict[str, Dict[str, str]]:
    """Module-level wrapper for ToolSystem.list_tools_with_sources()."""
    return tool_system_instance.list_tools_with_sources()

async def main_test(): # pragma: no cover
    print("\n--- ToolSystem Direct Execution Test (using global instance) ---")
    print("Listing tools from the globally initialized instance:")
    all_tools = list_tools()
    for t_name, t_desc in all_tools.items():
        print(f"  - {t_name}: {t_desc[:70]}...")

    print("\nTesting execution of 'greet_user' tool:")
    try:
        greeting = await execute_tool("greet_user", args=("ModuleTester",), notification_manager=None)
        print(f"Greeting result: {greeting}")
    except Exception as e:
        print(f"Error executing greet_user: {e}")

    print("\nTesting execution of 'add_numbers' tool:")
    try:
        sum_result = await execute_tool("add_numbers", args=(5, "7"), notification_manager=None) # "7" to test type conversion
        print(f"Sum result: {sum_result}")
    except Exception as e:
        print(f"Error executing add_numbers: {e}")

    print("\nTesting new 'manage_auto_approve_list' tool (if registered):")
    if "manage_auto_approve_list" in all_tools:
        try:
            list_result = await execute_tool("manage_auto_approve_list", args=("list",), notification_manager=None)
            print(f"Manage auto-approve list result: {list_result}")
            add_result = await execute_tool("manage_auto_approve_list", args=("add", "greet_user"), notification_manager=None)
            print(f"Add 'greet_user' to auto-approve: {add_result}")
            list_after_add = await execute_tool("manage_auto_approve_list", args=("list",), notification_manager=None)
            print(f"List after add: {list_after_add}")
        except Exception as e:
            print(f"Error executing manage_auto_approve_list: {e}")
    else:
        print("'manage_auto_approve_list' not found in registered tools for this test run.")

    print("\n--- Testing list_tools_with_sources ---")
    detailed_tools_list = list_tools_with_sources() # Using the module-level wrapper
    if detailed_tools_list:
        print(f"Found {len(detailed_tools_list)} tools with details. First few:")
        count = 0
        for tool_name, details in detailed_tools_list.items():
            print(f"  Tool: {tool_name}")
            print(f"    Module Path: {details.get('module_path')}")
            print(f"    Function Name: {details.get('function_name')}")
            print(f"    Description: {details.get('description', '')[:70]}...")
            count += 1
            if count >= 3: # Print details for a few tools
                break
    else:
        print("No detailed tools found by list_tools_with_sources.")

    print("\n--- ToolSystem Direct Execution Test Finished ---")

if __name__ == '__main__': # pragma: no cover
    # This ensures that if the script is run directly, the async main_test runs.
    # Note: ToolSystem is instantiated globally, so its __init__ runs on import.
    # This __main__ block then calls functions on that global instance.
    asyncio.run(main_test())
