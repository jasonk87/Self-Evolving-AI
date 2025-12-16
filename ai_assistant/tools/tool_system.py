from typing import Any
import importlib
import os
import sys
import json
import inspect
import asyncio
from typing import Callable, Dict, Any, Optional, Tuple, List, TYPE_CHECKING
from ai_assistant.config import is_debug_mode, get_data_dir
from ai_assistant.core.self_modification import get_function_source_code
if TYPE_CHECKING:
    from ..core.task_manager import TaskManager
    from ..core.notification_manager import NotificationManager
DEFAULT_TOOLS_FILE_DIR = get_data_dir()
DEFAULT_TOOL_REGISTRY_FILE = os.path.join(DEFAULT_TOOLS_FILE_DIR, 'tool_registry.json')

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

    def __init__(self, tool_registry_file: Optional[str]=None):
        self._tool_registry: Dict[str, Dict[str, Any]] = {}
        self._persisted_tool_metadata_file = tool_registry_file or DEFAULT_TOOL_REGISTRY_FILE
        if is_debug_mode():
            print(f'ToolSystem: Initializing with registry file: {self._persisted_tool_metadata_file}')
        self.load_persisted_tools()
        self._register_system_tools()
        self.custom_tool_modules_to_discover = [('ai_assistant.custom_tools.my_extra_tools', 'my_extra_tools.py'), ('ai_assistant.custom_tools.awareness_tools', 'awareness_tools.py'), ('ai_assistant.custom_tools.config_management_tools', 'config_management_tools.py'), ('ai_assistant.custom_tools.conversational_tools', 'conversational_tools.py'), ('ai_assistant.custom_tools.project_management_tools', 'project_management_tools.py'), ('ai_assistant.custom_tools.project_execution_tools', 'project_execution_tools.py'), ('ai_assistant.custom_tools.code_execution_tools', 'code_execution_tools.py'), ('ai_assistant.custom_tools.file_system_tools', 'file_system_tools.py'), ('ai_assistant.custom_tools.git_tools', 'git_tools.py'), ('ai_assistant.custom_tools.knowledge_tools', 'knowledge_tools.py'), ('ai_assistant.custom_tools.meta_programming_tools', 'meta_programming_tools.py'), ('ai_assistant.custom_tools.suggestion_management_tools', 'suggestion_management_tools.py'), ('ai_assistant.custom_tools.agent_tools', 'agent_tools.py'), ('ai_assistant.custom_tools.calendar_tools', 'calendar_tools.py'), ('ai_assistant.custom_tools.generated', 'generated_tools_module'), ('ai_assistant.custom_tools.system_tools', 'system_tools.py'), ('ai_assistant.custom_tools.architect_tools', 'architect_tools.py'), ('ai_assistant.custom_tools.navigation_tools', 'navigation_tools.py')]
        any_new_tools_registered_overall = False
        for module_import_path, module_filename in self.custom_tool_modules_to_discover:
            try:
                module_to_inspect = importlib.import_module(module_import_path)
                if self._discover_and_register_custom_tools(module_to_inspect, module_import_path):
                    if is_debug_mode():
                        print(f'ToolSystem: New custom tools discovered from {module_filename}. Triggering save.')
                    any_new_tools_registered_overall = True
            except ImportError:
                if is_debug_mode():
                    print(f'ToolSystem: Could not import {module_filename} (path: {module_import_path}), skipping custom tool discovery from it.')
            except Exception as e:
                print(f'ToolSystem: Warning - Error during custom tool discovery from {module_filename} (path: {module_import_path}): {e}')
        self.register_example_tools()
        if any_new_tools_registered_overall:
            self.save_registered_tools()
        if is_debug_mode():
            print(f'ToolSystem: Initialization complete. {len(self._tool_registry)} tools registered.')

    def _discover_and_register_custom_tools(self, module_to_inspect, module_path_str: str) -> bool:
        """
        Discovers and registers public functions from a given module as tools.
        Skips functions starting with '_' or not defined directly in the module.
        If a tool with the same name and module_path already exists, it's skipped.
        Returns True if any new tools were registered from this module, False otherwise.
        """
        new_tools_registered_in_this_module = False
        if is_debug_mode():
            print(f'ToolSystem: Discovering custom tools from module: {module_path_str}')
        for name, func_object in inspect.getmembers(module_to_inspect, inspect.isfunction):
            if name.startswith('_'):
                continue
            if func_object.__module__ != module_to_inspect.__name__ and (not func_object.__module__.startswith(module_to_inspect.__name__ + '.')):
                continue
            if name in self._tool_registry and self._tool_registry[name].get('module_path') == module_path_str:
                continue
            docstring = inspect.getdoc(func_object) or 'No description available.'
            first_line_of_docstring = docstring.splitlines()[0] if docstring else 'No description available.'
            tool_description_for_registration = first_line_of_docstring
            discovered_schema_details = None
            schema_variable_name = f'{name.upper()}_SCHEMA'
            if hasattr(module_to_inspect, schema_variable_name):
                potential_schema = getattr(module_to_inspect, schema_variable_name)
                if isinstance(potential_schema, dict) and 'name' in potential_schema and ('description' in potential_schema):
                    discovered_schema_details = potential_schema
                    tool_description_for_registration = discovered_schema_details.get('description', first_line_of_docstring)
                    if is_debug_mode():
                        print(f"ToolSystem: Found schema '{schema_variable_name}' for tool '{name}'. Using schema description.")
                elif is_debug_mode():
                    print(f"ToolSystem: Found schema variable '{schema_variable_name}' for tool '{name}', but it's not a valid schema dict.")
            if is_debug_mode():
                print(f"ToolSystem: Attempting to register discovered custom tool '{name}' from '{module_path_str}'.")
            try:
                self.register_tool(tool_name=name, description=tool_description_for_registration, module_path=module_path_str, function_name_in_module=name, tool_type='custom_discovered', func_callable=func_object, schema_details=discovered_schema_details)
                new_tools_registered_in_this_module = True
                if is_debug_mode():
                    print(f"ToolSystem: Successfully registered custom tool '{name}'.")
            except ToolAlreadyRegisteredError as e:
                if is_debug_mode():
                    print(f"ToolSystem: Info during discovery for custom tool '{name}': {e}")
            except Exception as e:
                print(f"ToolSystem: Error - Failed to register discovered custom tool '{name}': {e}")
        if new_tools_registered_in_this_module:
            if is_debug_mode():
                print(f"ToolSystem: Finished discovery for '{module_path_str}'. New tools were registered in this pass.")
        elif is_debug_mode():
            print(f"ToolSystem: Finished discovery for '{module_path_str}'. No new tools were registered in this pass. ")
        return new_tools_registered_in_this_module

    def refresh_custom_tools(self) -> str:
        """
        Reloads all custom tool modules and re-registers tools.
        Useful when new tools are generated or code is modified at runtime.
        """
        results = []
        for module_import_path, module_filename in self.custom_tool_modules_to_discover:
            try:
                if module_import_path in sys.modules:
                    module = sys.modules[module_import_path]
                    importlib.reload(module)
                    if is_debug_mode():
                        print(f'ToolSystem: Reloaded module {module_import_path}')
                else:
                    module = importlib.import_module(module_import_path)
                if self._discover_and_register_custom_tools(module, module_import_path):
                    results.append(f'Discovered new tools in {module_filename}')
            except Exception as e:
                results.append(f'Failed to refresh {module_filename}: {e}')
        self.save_registered_tools()
        self.register_example_tools()
        return 'Tool refresh complete. ' + ('; '.join(results) if results else 'No new tools discovered, but modules were reloaded.')

    def _system_update_tool_metadata_impl(self, tool_name: str, new_description: Optional[str]=None) -> bool:
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
            elif is_debug_mode():
                print(f"SystemTool: New description for '{tool_name}' is same as old; no update made.")
        if updated:
            if self.save_registered_tools():
                if is_debug_mode():
                    print(f"SystemTool: Tool registry saved successfully after updating '{tool_name}'.")
                return True
            else:
                print(f"Error (system_update_tool_metadata): Failed to save tool registry after updating '{tool_name}'.")
                return False
        else:
            return True

    def _register_system_tools(self):
        """Registers tools that are internal to the ToolSystem or for system management."""
        system_tool_entry = {'tool_name': 'system_update_tool_metadata', 'description': 'Updates the metadata of a registered tool, such as its description. For system use. Args: tool_name (str), new_description (str, optional)', 'type': 'system_internal', 'module_path': self.__class__.__module__, 'function_name': '_system_update_tool_metadata_impl', 'callable_cache': self._system_update_tool_metadata_impl, 'is_method_on_instance': True}
        if 'system_update_tool_metadata' not in self._tool_registry:
            self._tool_registry['system_update_tool_metadata'] = system_tool_entry
        elif self._tool_registry['system_update_tool_metadata'].get('is_method_on_instance'):
            self._tool_registry['system_update_tool_metadata']['callable_cache'] = self._system_update_tool_metadata_impl
        if is_debug_mode():
            print('ToolSystem: System tools registered.')
        refresh_tool_entry = {'tool_name': 'refresh_available_tools', 'description': 'Reloads all custom tool modules to discover new or updated tools without restarting. Returns status.', 'type': 'system_internal', 'module_path': self.__class__.__module__, 'function_name': 'refresh_custom_tools', 'callable_cache': self.refresh_custom_tools, 'is_method_on_instance': True}
        self._tool_registry['refresh_available_tools'] = refresh_tool_entry

    def register_tool(self, tool_name: str, description: str, module_path: str, function_name_in_module: str, tool_type: str='dynamic', func_callable: Optional[Callable]=None, schema_details: Optional[Dict[str, Any]]=None) -> bool:
        """
        Registers a new tool or updates an existing one.
        If func_callable is provided, it's cached. Otherwise, it's loaded on first execution.
        """
        if tool_name in self._tool_registry:
            existing_tool = self._tool_registry[tool_name]
            if existing_tool['module_path'] != module_path or existing_tool['function_name'] != function_name_in_module or existing_tool['type'] != tool_type:
                if existing_tool['type'] == 'system_internal' and tool_type == 'system_internal' and (func_callable is not None):
                    if is_debug_mode():
                        print(f"ToolSystem: Re-caching system tool '{tool_name}'.")
                elif is_debug_mode():
                    print(f"ToolSystem: Info - Tool '{tool_name}' already registered with different metadata (module: {existing_tool['module_path']}, func: {existing_tool['function_name']}, type: {existing_tool['type']}). Updating with new details: (module: {module_path}, func: {function_name_in_module}, type: {tool_type}).")
            if is_debug_mode():
                print(f"ToolSystem: Tool '{tool_name}' is being re-registered/updated. Description: '{description}'")
        tool_entry = {'tool_name': tool_name, 'module_path': module_path, 'function_name': function_name_in_module, 'description': description, 'type': tool_type, 'callable_cache': func_callable, 'schema_details': schema_details}
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

    def get_tool_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Alias for get_tool for backward compatibility."""
        return self.get_tool(name)

    async def execute_tool(self, name: str, args: Tuple=(), kwargs: Optional[Dict[str, Any]]=None, task_manager: Optional['TaskManager']=None, notification_manager: Optional['NotificationManager']=None, action_executor: Optional[Any]=None) -> Any:
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
            except ModuleNotFoundError:
                raise ToolExecutionError(f"Could not load function for tool '{name}': Module '{module_path}' not found.")
            except AttributeError:
                raise ToolExecutionError(f"Could not load function for tool '{name}': Function '{function_name}' not found in '{module_path}'.")
            except Exception as e:
                raise ToolExecutionError(f"Could not load function for tool '{name}': An unexpected error occurred - {e}")
        if not callable(func_to_execute):
            raise ToolExecutionError(f"Tool '{name}': Loaded attribute '{tool_info['function_name']}' is not callable.")
        final_kwargs = kwargs.copy()
        sig = None
        try:
            sig = inspect.signature(func_to_execute)
        except (ValueError, TypeError):
            if is_debug_mode():
                print(f"ToolSystem: Warning - Could not inspect signature for tool '{name}'. Dependency injection might be limited.")
            pass
        if sig:
            if task_manager and 'task_manager' in sig.parameters:
                final_kwargs['task_manager'] = task_manager
                if is_debug_mode():
                    print(f"ToolSystem: Injecting TaskManager into tool '{name}'.")
            if notification_manager and 'notification_manager' in sig.parameters:
                final_kwargs['notification_manager'] = notification_manager
                if is_debug_mode():
                    print(f"ToolSystem: Injecting NotificationManager into tool '{name}'.")
            if action_executor and 'action_executor' in sig.parameters:
                final_kwargs['action_executor'] = action_executor
                if is_debug_mode():
                    print(f"ToolSystem: Injecting ActionExecutor into tool '{name}'.")
        try:
            if is_debug_mode():
                print(f"ToolSystem: Executing tool '{name}' with args={args}, final_kwargs={final_kwargs}")
            if inspect.iscoroutinefunction(func_to_execute):
                result = await func_to_execute(*args, **final_kwargs)
            else:
                result = await asyncio.to_thread(func_to_execute, *args, **final_kwargs)
            if is_debug_mode():
                print(f"ToolSystem: Tool '{name}' executed successfully. Result (first 200 chars): {str(result)[:200]}")
            return result
        except Exception as e:
            print(f"ToolSystem: Error during execution of tool '{name}': {type(e).__name__} - {e}")
            raise ToolExecutionError(f"Error during execution of tool '{name}': {e}") from e

    def get_tools_description(self) -> str:
        """
        Returns a formatted string describing all available tools.
        Used for the LLM system prompt.
        """
        tools_list = []
        for name, data in self._tool_registry.items():
            desc = data.get('description', 'No description available.')
            # If schema details exist, maybe add args? For now keep it simple as per original intent.
            tools_list.append(f"- {name}: {desc}")
        return "\n".join(tools_list)

    def list_tools(self) -> Dict[str, str]:
        """Returns a dictionary of tool names to their descriptions."""
        return {name: tool['description'] for name, tool in self._tool_registry.items()}

    def list_tools_with_sources(self) -> Dict[str, Dict[str, str]]:
        """
        Returns a dictionary of all registered tools with their detailed metadata,
        including module_path, function_name, and description.

        The key of the outer dictionary is the tool_name (registry key).
        The inner dictionary contains 'module_path', 'function_name', 'description', and 'schema_details'.
        """
        detailed_tools = {}
        for tool_name, tool_data in self._tool_registry.items():
            detailed_tools[tool_name] = {'module_path': tool_data.get('module_path', 'N/A'), 'function_name': tool_data.get('function_name', tool_name), 'description': tool_data.get('description', 'No description available.'), 'schema_details': tool_data.get('schema_details')}
        return detailed_tools

    def save_registered_tools(self) -> bool:
        """Saves the metadata of all registered tools (excluding callables and schema details if complex) to a JSON file."""
        os.makedirs(DEFAULT_TOOLS_FILE_DIR, exist_ok=True)
        data_to_save = {}
        for name, tool_data in self._tool_registry.items():
            if tool_data.get('type') == 'system_internal' and tool_data.get('is_method_on_instance') and is_debug_mode():
                print(f"ToolSystem: Debug - Skipping persistence of system_internal tool '{name}' with instance method.")
                continue
            serializable_data = tool_data.copy()
            serializable_data.pop('callable_cache', None)
            serializable_data.pop('is_method_on_instance', None)
            data_to_save[name] = serializable_data
        if 'get_self_awareness_info_and_converse' in data_to_save:
            print(f"[DEBUG SAVE TOOL_SYSTEM] Saving 'get_self_awareness_info_and_converse' with module_path: {data_to_save['get_self_awareness_info_and_converse'].get('module_path')}")
        elif is_debug_mode():
            print("[DEBUG SAVE TOOL_SYSTEM] 'get_self_awareness_info_and_converse' not in data_to_save (it might be a system_internal tool with instance method).")
        try:
            with open(self._persisted_tool_metadata_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=4)
            if is_debug_mode():
                print(f'ToolSystem: Tools saved successfully to {self._persisted_tool_metadata_file}')
            return True
        except IOError as e:
            print(f'ToolSystem: Error saving tools to {self._persisted_tool_metadata_file}: {e}')
            return False
        except Exception as e:
            print(f'ToolSystem: Unexpected error saving tools: {e}')
            return False

    def load_persisted_tools(self):
        """Loads tool metadata from the persisted JSON file and registers them."""
        if is_debug_mode():
            print(f"ToolSystem: Loading persisted tools from '{self._persisted_tool_metadata_file}'...")
        if not os.path.exists(self._persisted_tool_metadata_file):
            if is_debug_mode():
                print(f"ToolSystem: Tools file '{self._persisted_tool_metadata_file}' not found. No tools loaded from persistence.")
            return
        try:
            with open(self._persisted_tool_metadata_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content:
                    if is_debug_mode():
                        print('ToolSystem: Tools file is empty. No tools loaded from persistence.')
                    return
                loaded_tool_metadata = json.loads(content)
        except json.JSONDecodeError as e:
            print(f"ToolSystem: Error decoding JSON from '{self._persisted_tool_metadata_file}': {e}. No tools loaded from persistence.")
            return
        except IOError as e:
            print(f"ToolSystem: Error reading tools file '{self._persisted_tool_metadata_file}': {e}. No tools loaded from persistence.")
            return
        if loaded_tool_metadata and 'get_self_awareness_info_and_converse' in loaded_tool_metadata:
            print(f"[DEBUG LOAD TOOL_SYSTEM] Loaded 'get_self_awareness_info_and_converse' with module_path: {loaded_tool_metadata['get_self_awareness_info_and_converse'].get('module_path')}")
        elif loaded_tool_metadata and is_debug_mode():
            print("[DEBUG LOAD TOOL_SYSTEM] 'get_self_awareness_info_and_converse' not found in loaded_tool_metadata from file.")
        elif is_debug_mode():
            print('[DEBUG LOAD TOOL_SYSTEM] loaded_tool_metadata is empty or None after attempting to read file.')
        if not loaded_tool_metadata:
            if is_debug_mode():
                print('ToolSystem: No tools data found in file. Skipping load from persistence.')
            return
        loaded_count = 0
        for tool_name, tool_data in loaded_tool_metadata.items():
            if tool_data.get('type') == 'system_internal' and tool_data.get('is_method_on_instance'):
                if is_debug_mode():
                    print(f"ToolSystem: Skipping load of system_internal tool '{tool_name}' from file; will be instance-registered.")
                continue
            try:
                self.register_tool(tool_name=tool_data.get('tool_name', tool_name), description=tool_data['description'], module_path=tool_data['module_path'], function_name_in_module=tool_data['function_name'], tool_type=tool_data.get('type', 'dynamic'), func_callable=None, schema_details=tool_data.get('schema_details'))
                loaded_count += 1
            except ToolAlreadyRegisteredError as e:
                if is_debug_mode():
                    print(f"ToolSystem: Info while loading persisted tool '{tool_name}': {e}")
            except KeyError as e:
                print(f"ToolSystem: Error - Persisted tool '{tool_name}' has missing metadata: {e}. Skipping.")
            except Exception as e:
                print(f"ToolSystem: Error loading persisted tool '{tool_name}': {e}. Skipping.")
        if is_debug_mode() or loaded_count > 0:
            print(f'ToolSystem: Successfully processed {loaded_count} persisted tools from file.')

    def register_example_tools(self):
        """Registers a set of example tools. Idempotent."""
        current_module_obj = sys.modules[self.__class__.__module__]
        example_tools_data = [('greet_user', 'Greets the user. Args: name (str)', '_example_greet_user'), ('add_numbers', 'Adds two integers. Args: a (int), b (int)', '_example_add_numbers'), ('multiply_numbers', 'Multiplies two floats. Args: x (float), y (float)', '_example_multiply_numbers'), ('no_op_tool', 'Does nothing, useful for default plans.', '_example_no_op_tool'), ('view_function_code', 'Retrieves the source code of a specified function. Inputs: module_path (str), function_name (str).', '_tool_view_function_code'), ('simulate_edit_function_code', 'Simulates editing source code. Inputs: module_path (str), function_name (str), new_code_block (str).', '_tool_simulate_edit_function_code'), ('maybe_fail_tool', 'A tool that fails on its 1st, 3rd, etc. call and succeeds on its 2nd, 4th, etc. call.', '_example_maybe_fail_tool')]
        for tool_name, description, func_name_str in example_tools_data:
            try:
                func_callable = getattr(current_module_obj, func_name_str, None)
                if not func_callable or not callable(func_callable):
                    print(f"ToolSystem: Error - Example tool function '{func_name_str}' not found or not callable in {current_module_obj.__name__}. Skipping.")
                    continue
                self.register_tool(tool_name=tool_name, description=description, module_path=current_module_obj.__name__, function_name_in_module=func_name_str, tool_type='builtin', func_callable=func_callable)
            except ToolAlreadyRegisteredError as e:
                if is_debug_mode():
                    print(f"ToolSystem: Info while registering example tool '{tool_name}': {e}")
            except Exception as e:
                print(f'ToolSystem: Error registering example tool {tool_name}: {e}')
        if is_debug_mode():
            print(f'ToolSystem: Example tools registration attempt finished.')

def _example_greet_user(name: str) -> str:
    return f'Hello, {name}!'

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
    return 'No-op tool executed successfully.'
_maybe_fail_tool_counter = 0

def _example_maybe_fail_tool() -> str:
    global _maybe_fail_tool_counter
    _maybe_fail_tool_counter += 1
    if _maybe_fail_tool_counter % 2 != 0:
        raise ValueError(f'Intentional failure from maybe_fail_tool on call #{_maybe_fail_tool_counter}!')
    return f'maybe_fail_tool succeeded on call #{_maybe_fail_tool_counter}.'

def _tool_view_function_code(module_path: str, function_name: str) -> str:
    try:
        from ai_assistant.tools.tool_system import get_function_source_code
        source_code = get_function_source_code(module_path, function_name)
        if source_code is None:
            return 'Error: Could not retrieve source code. Module or function not found, or source not available.'
        return source_code
    except Exception as e:
        return f'Error: Could not retrieve source code. An exception occurred: {e}'

def _tool_simulate_edit_function_code(module_path: str, function_name: str, new_code_block: str) -> str:
    print(f'Simulating edit for {module_path}.{function_name} with:\n{new_code_block}')
    return f"Simulation of code edit for '{module_path}.{function_name}' completed. No actual changes made by this simulation tool."
tool_system_instance = ToolSystem()

def register_tool(tool_name: str, description: str, module_path: str, function_name_in_module: str, tool_type: str='dynamic', func_callable: Optional[Callable]=None) -> bool:
    return tool_system_instance.register_tool(tool_name, description, module_path, function_name_in_module, tool_type, func_callable)

def remove_tool(name: str) -> bool:
    """Removes a registered tool. Returns True if successful."""
    return tool_system_instance.remove_tool(name)

def get_tool(name: str) -> Optional[Dict[str, Any]]:
    return tool_system_instance.get_tool(name)

async def execute_tool(name: str, args: Tuple=(), kwargs: Optional[Dict[str, Any]]=None, task_manager: Optional['TaskManager']=None, notification_manager: Optional['NotificationManager']=None, action_executor: Optional[Any]=None) -> Any:
    return await tool_system_instance.execute_tool(name, args, kwargs, task_manager=task_manager, notification_manager=notification_manager, action_executor=action_executor)

def list_tools() -> Dict[str, str]:
    return tool_system_instance.list_tools()

def save_registered_tools() -> bool:
    return tool_system_instance.save_registered_tools()

def load_persisted_tools():
    tool_system_instance.load_persisted_tools()

def register_example_tools():
    tool_system_instance.register_example_tools()

def list_tools_with_sources() -> Dict[str, Dict[str, str]]:
    """Module-level wrapper for ToolSystem.list_tools_with_sources()."""
    return tool_system_instance.list_tools_with_sources()

async def main_test():
    print('\n--- ToolSystem Direct Execution Test (using global instance) ---')
    print('Listing tools from the globally initialized instance:')
    all_tools = list_tools()
    for t_name, t_desc in all_tools.items():
        print(f'  - {t_name}: {t_desc[:70]}...')
    print("\nTesting execution of 'greet_user' tool:")
    try:
        greeting = await execute_tool('greet_user', args=('ModuleTester',), notification_manager=None)
        print(f'Greeting result: {greeting}')
    except Exception as e:
        print(f'Error executing greet_user: {e}')
    print("\nTesting execution of 'add_numbers' tool:")
    try:
        sum_result = await execute_tool('add_numbers', args=(5, '7'), notification_manager=None)
        print(f'Sum result: {sum_result}')
    except Exception as e:
        print(f'Error executing add_numbers: {e}')
    print("\nTesting new 'manage_auto_approve_list' tool (if registered):")
    if 'manage_auto_approve_list' in all_tools:
        try:
            list_result = await execute_tool('manage_auto_approve_list', args=('list',), notification_manager=None)
            print(f'Manage auto-approve list result: {list_result}')
            add_result = await execute_tool('manage_auto_approve_list', args=('add', 'greet_user'), notification_manager=None)
            print(f"Add 'greet_user' to auto-approve: {add_result}")
            list_after_add = await execute_tool('manage_auto_approve_list', args=('list',), notification_manager=None)
            print(f'List after add: {list_after_add}')
        except Exception as e:
            print(f'Error executing manage_auto_approve_list: {e}')
    else:
        print("'manage_auto_approve_list' not found in registered tools for this test run.")
    print('\n--- Testing list_tools_with_sources ---')
    detailed_tools_list = list_tools_with_sources()
    if detailed_tools_list:
        print(f'Found {len(detailed_tools_list)} tools with details. First few:')
        count = 0
        for tool_name, details in detailed_tools_list.items():
            print(f'  Tool: {tool_name}')
            print(f"    Module Path: {details.get('module_path')}")
            print(f"    Function Name: {details.get('function_name')}")
            print(f"    Description: {details.get('description', '')[:70]}...")
            count += 1
            if count >= 3:
                break
    else:
        print('No detailed tools found by list_tools_with_sources.')
    print('\n--- ToolSystem Direct Execution Test Finished ---')
if __name__ == '__main__':
    asyncio.run(main_test())