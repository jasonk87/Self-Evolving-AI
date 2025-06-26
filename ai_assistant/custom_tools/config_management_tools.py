# ai_assistant/custom_tools/config_management_tools.py
import json
import os
from typing import List, Optional, Dict, Any, Tuple
# Import get_data_dir from the main config to centralize data paths
from ..config import get_data_dir

# Define the path to the configuration file for tools requiring user confirmation.
TOOL_CONFIRMATION_CONFIG_PATH = os.path.join(get_data_dir(), "tool_confirmation_config.json")

def _load_requires_confirmation_list() -> List[str]:
    """
    Loads the list of tools that require user confirmation from the JSON configuration file.
    If the file doesn't exist or is invalid, it returns an empty list (all tools auto-approved by default).
    """
    default_list: List[str] = [] # Default is an empty list, meaning all tools are auto-approved
    if not os.path.exists(TOOL_CONFIRMATION_CONFIG_PATH):
        return default_list
    try:
        with open(TOOL_CONFIRMATION_CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip(): # Handle empty file
                return default_list
            data = json.loads(content)
        # Ensure the loaded data is a list of strings.
        loaded_list = data.get("requires_confirmation_tools", default_list)
        if not isinstance(loaded_list, list) or not all(isinstance(item, str) for item in loaded_list):
            print(f"Warning: 'requires_confirmation_tools' in '{TOOL_CONFIRMATION_CONFIG_PATH}' is not a list of strings. Using default (empty list).")
            return default_list
        return loaded_list
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Could not load or parse {TOOL_CONFIRMATION_CONFIG_PATH}. Using default (empty list). Error: {e}")
        return default_list

def _save_requires_confirmation_list(tools_list: List[str]) -> bool:
    """
    Saves the provided list of tools requiring confirmation to the JSON configuration file.
    Creates the directory if it doesn't exist.
    """
    try:
        # Ensure the 'data' directory exists.
        os.makedirs(os.path.dirname(TOOL_CONFIRMATION_CONFIG_PATH), exist_ok=True)
        with open(TOOL_CONFIRMATION_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump({"requires_confirmation_tools": tools_list}, f, indent=2)
        return True
    except IOError as e:
        print(f"Error: Could not write to {TOOL_CONFIRMATION_CONFIG_PATH}. Error: {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred while saving to {TOOL_CONFIRMATION_CONFIG_PATH}. Error: {e}")
        return False


def manage_tool_confirmation_settings(action: str, tool_name: Optional[str] = None) -> str:
    """
    Manages the list of tools that require explicit user confirmation before execution.
    Other tools are considered auto-approved by default.

    This tool allows adding, removing, or listing tools in the 'requires confirmation' configuration.
    The configuration is stored in 'data/tool_confirmation_config.json'.

    Args:
        action (str): The action to perform. Valid actions are:
                      "add": Adds a specified tool_name to the 'requires confirmation' list.
                             (This makes the tool NOT auto-approved).
                      "remove": Removes a specified tool_name from the 'requires confirmation' list.
                                (This makes the tool auto-approved again).
                      "list": Lists all tools currently requiring user confirmation.
                      "add_all": Adds all known, non-system-internal tools to the 'requires confirmation' list.
                                 (Makes all tools require confirmation).
                      "remove_all": Clears the 'requires confirmation' list.
                                    (Makes all tools auto-approved).
        tool_name (Optional[str]): The name of the tool to add or remove.
                                   This is required for "add" and "remove" actions.

    Returns:
        str: A message confirming the action taken or an error message if the action failed.
    """
    current_confirmation_list = _load_requires_confirmation_list()
    action = action.lower().strip() # Normalize action string

    # Import ToolSystem here to access all registered tools for validation and "add_all"
    try:
        from ai_assistant.tools.tool_system import tool_system_instance
        all_known_tools_dict = tool_system_instance.list_tools() 
        all_known_tool_names = list(all_known_tools_dict.keys())
        # Filter out system_internal tools for "add_all"
        non_system_internal_tool_names = [
            name for name, data in tool_system_instance._tool_registry.items() # Access internal for type
            if data.get('type') != 'system_internal'
        ]

    except ImportError:
        print("Error: Could not import ToolSystem to validate tool names for manage_tool_confirmation_settings.")
        all_known_tool_names = None
        non_system_internal_tool_names = None


    if action == "list":
        if not current_confirmation_list:
            return "No tools currently require user confirmation (all tools are auto-approved by default)."
        return "Tools currently requiring user confirmation: " + ", ".join(sorted(current_confirmation_list))

    if action == "add_all":
        if non_system_internal_tool_names is not None:
            updated_list = sorted(list(set(non_system_internal_tool_names))) # Add all non-system tools, ensure uniqueness and sort
            if _save_requires_confirmation_list(updated_list):
                return f"All {len(updated_list)} known non-system-internal tools now require user confirmation."
            else:
                return "Error: Failed to set all tools to require confirmation due to a save error."
        else:
            return "Error: Cannot perform 'add_all' as the list of all system tools could not be retrieved."


    if action == "remove_all":
        if not current_confirmation_list: # Already empty
            return "No tools require user confirmation (all tools are already auto-approved by default)."
        if _save_requires_confirmation_list([]): # Save an empty list
            return "All tools removed from the 'requires confirmation' list. All tools are now auto-approved."
        else:
            return "Error: Failed to clear the 'requires confirmation' list due to a save error."

    # Actions "add" and "remove" require a tool_name
    if not tool_name:
        return f"Error: 'tool_name' is required for the action '{action}'."

    tool_name = tool_name.strip() # Clean the tool_name input

    # Validate tool_name against known tools if possible
    if all_known_tool_names and tool_name not in all_known_tool_names:
        return f"Error: Tool '{tool_name}' is not a recognized tool in the system. Cannot '{action}' it."

    if action == "add": # Add to "requires confirmation" list
        if tool_name not in current_confirmation_list:
            current_confirmation_list.append(tool_name)
            if _save_requires_confirmation_list(sorted(current_confirmation_list)):
                return f"Tool '{tool_name}' now requires user confirmation (added to 'requires confirmation' list)."
            else:
                current_confirmation_list.remove(tool_name) # Revert in-memory change
                return f"Error: Failed to save updated 'requires confirmation' list after attempting to add '{tool_name}'."
        else:
            return f"Tool '{tool_name}' already requires user confirmation. No action taken."
    elif action == "remove": # Remove from "requires confirmation" list
        if tool_name in current_confirmation_list:
            current_confirmation_list.remove(tool_name)
            if _save_requires_confirmation_list(sorted(current_confirmation_list)):
                return f"Tool '{tool_name}' no longer requires user confirmation (removed from 'requires confirmation' list; now auto-approved)."
            else:
                current_confirmation_list.append(tool_name) # Revert in-memory change
                return f"Error: Failed to save updated 'requires confirmation' list after attempting to remove '{tool_name}'."
        else:
            return f"Tool '{tool_name}' does not require user confirmation (it's already auto-approved). Cannot remove from list."
    else:
        return f"Error: Unknown action '{action}'. Valid actions are 'add', 'remove', 'list', 'add_all', 'remove_all'."

if __name__ == '__main__': # pragma: no cover
    print("--- Testing Tool Confirmation Settings (manage_tool_confirmation_settings) ---")

    original_config_content = None
    if os.path.exists(TOOL_CONFIRMATION_CONFIG_PATH):
        with open(TOOL_CONFIRMATION_CONFIG_PATH, 'r', encoding='utf-8') as f_orig:
            original_config_content = f_orig.read()
        os.remove(TOOL_CONFIRMATION_CONFIG_PATH)

    class MockToolSystemInstanceForConfigTest:
        def __init__(self):
            self._tool_registry = {
                "search_duckduckgo": {"description": "Searches the web.", "type": "custom_discovered"},
                "get_self_awareness_info_and_converse": {"description": "Provides info about the AI.", "type": "custom_discovered"},
                "another_example_tool": {"description": "Does something else.", "type": "custom_discovered"},
                "project_tool_A": {"description": "Manages project A.", "type": "custom_discovered"},
                "system_update_tool_metadata": {"description": "Internal system tool.", "type": "system_internal"}
            }
        def list_tools(self) -> Dict[str, str]:
            return {name: data["description"] for name, data in self._tool_registry.items()}

    from ai_assistant.tools import tool_system as ts_main_module
    original_main_ts_instance = ts_main_module.tool_system_instance
    ts_main_module.tool_system_instance = MockToolSystemInstanceForConfigTest()


    print("\nInitial state (should be default: all tools auto-approved):")
    print(f"List: {manage_tool_confirmation_settings(action='list')}") 

    print("\nAdding tools to 'requires confirmation':")
    print(f"Add 'get_self_awareness_info_and_converse': {manage_tool_confirmation_settings(action='add', tool_name='get_self_awareness_info_and_converse')}")
    print(f"List after add: {manage_tool_confirmation_settings(action='list')}")
    assert "get_self_awareness_info_and_converse" in _load_requires_confirmation_list()

    print(f"\nAdd 'non_existent_tool': {manage_tool_confirmation_settings(action='add', tool_name='non_existent_tool')}") 
    assert "non_existent_tool" not in _load_requires_confirmation_list()

    print(f"\nAdd 'get_self_awareness_info_and_converse' again: {manage_tool_confirmation_settings(action='add', tool_name='get_self_awareness_info_and_converse')}")
    print(f"List after adding existing: {manage_tool_confirmation_settings(action='list')}")

    print("\nRemoving tools from 'requires confirmation' (making them auto-approved):")
    print(f"Remove 'get_self_awareness_info_and_converse': {manage_tool_confirmation_settings(action='remove', tool_name='get_self_awareness_info_and_converse')}")
    print(f"List after remove: {manage_tool_confirmation_settings(action='list')}")
    assert "get_self_awareness_info_and_converse" not in _load_requires_confirmation_list()

    print(f"\nRemove 'get_self_awareness_info_and_converse' again: {manage_tool_confirmation_settings(action='remove', tool_name='get_self_awareness_info_and_converse')}")

    print("\nTesting 'add_all' (make all non-system tools require confirmation) and 'remove_all' (make all tools auto-approved):")
    print(f"Add 'search_duckduckgo' to require confirmation: {manage_tool_confirmation_settings(action='add', tool_name='search_duckduckgo')}")
    print(f"Current list: {manage_tool_confirmation_settings(action='list')}")
    
    print(f"\n'add_all' action (make all non-system tools require confirmation):")
    print(manage_tool_confirmation_settings(action='add_all'))
    list_after_add_all = _load_requires_confirmation_list()
    print(f"List after 'add_all': {manage_tool_confirmation_settings(action='list')}")
    assert len(list_after_add_all) == 4 # search_duckduckgo, get_self_awareness..., another_example..., project_tool_A
    assert "system_update_tool_metadata" not in list_after_add_all


    print(f"\n'remove_all' action (make all tools auto-approved):")
    print(manage_tool_confirmation_settings(action='remove_all'))
    print(f"List after 'remove_all': {manage_tool_confirmation_settings(action='list')}")
    assert not _load_requires_confirmation_list() 

    ts_main_module.tool_system_instance = original_main_ts_instance

    if original_config_content is not None:
        with open(TOOL_CONFIRMATION_CONFIG_PATH, 'w', encoding='utf-8') as f_restore:
            f_restore.write(original_config_content)
        print(f"\nRestored original content to {TOOL_CONFIRMATION_CONFIG_PATH}")
    elif os.path.exists(TOOL_CONFIRMATION_CONFIG_PATH):
        os.remove(TOOL_CONFIRMATION_CONFIG_PATH)
        print(f"\nRemoved test config file {TOOL_CONFIRMATION_CONFIG_PATH}")
    
    data_dir = os.path.dirname(TOOL_CONFIRMATION_CONFIG_PATH)
    if os.path.exists(data_dir) and not os.listdir(data_dir):
        os.rmdir(data_dir)
        print(f"Removed empty data directory: {data_dir}")


    print("\n--- Tool Confirmation Settings Tests Finished ---")
