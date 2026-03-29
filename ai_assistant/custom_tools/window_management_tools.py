import platform
import logging
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

from ai_assistant.tools.tool_system import tool_system_instance

logger = logging.getLogger(__name__)

# Try to import PyGetWindow, failing gracefully if headless or unsupported OS
try:
    import pygetwindow as gw
except ImportError:
    gw = None
    logger.warning("PyGetWindow not installed or unsupported on this OS. Window management tools will be disabled.")
except NotImplementedError:
    # PyGetWindow throws NotImplementedError on Linux Wayland/macOS sometimes without explicit backends
    gw = None
    logger.warning("PyGetWindow backend not implemented for this OS environment.")

# ----------------------------------------------------------------------------
# Tool: List Active Windows
# ----------------------------------------------------------------------------
class ListWindowsSchema(BaseModel):
    pass # No parameters needed

def list_active_windows() -> Dict[str, Any]:
    """
    Returns a list of titles for all currently open, visible application windows on the desktop.
    Crucial for finding the exact name of a window before managing it.
    """
    if not gw:
        return {"success": False, "error_message": "Window management is not supported or installed on this system."}

    try:
        # Get all window titles, filter out empty/hidden ones
        titles = [title for title in gw.getAllTitles() if title.strip()]

        if not titles:
            return {"success": True, "result": "No active windows found or visible."}

        return {
            "success": True,
            "result": f"Found {len(titles)} active windows:\n- " + "\n- ".join(titles)
        }
    except Exception as e:
        return {"success": False, "error_message": f"Failed to list windows: {str(e)}"}

# ----------------------------------------------------------------------------
# Tool: Manage Window
# ----------------------------------------------------------------------------
class ManageWindowSchema(BaseModel):
    window_title: str = Field(description="A substring of the target window's title (e.g., 'Google Chrome', 'Slack', 'Notepad'). Case-insensitive.")
    action: str = Field(description="The action to perform. Valid options: 'minimize', 'maximize', 'restore', 'close', 'move', 'resize'.")
    x: Optional[int] = Field(default=None, description="The new X coordinate (required if action='move').")
    y: Optional[int] = Field(default=None, description="The new Y coordinate (required if action='move').")
    width: Optional[int] = Field(default=None, description="The new width (required if action='resize').")
    height: Optional[int] = Field(default=None, description="The new height (required if action='resize').")

def manage_window(window_title: str, action: str, x: Optional[int] = None, y: Optional[int] = None, width: Optional[int] = None, height: Optional[int] = None) -> Dict[str, Any]:
    """
    Performs physical OS-level window management: minimizing, maximizing, closing, moving, or resizing a specific application window.
    """
    if not gw:
        return {"success": False, "error_message": "Window management is not supported or installed on this system."}

    action = action.lower()
    valid_actions = ['minimize', 'maximize', 'restore', 'close', 'move', 'resize']

    if action not in valid_actions:
        return {"success": False, "error_message": f"Invalid action '{action}'. Must be one of {valid_actions}."}

    try:
        # Search for windows matching the title substring
        matching_windows = gw.getWindowsWithTitle(window_title)

        if not matching_windows:
            return {"success": False, "error_message": f"No window found matching title '{window_title}'. Use list_active_windows to see exact titles."}

        # Target the first matching window
        target = matching_windows[0]
        actual_title = target.title

        if action == 'minimize':
            target.minimize()
        elif action == 'maximize':
            target.maximize()
        elif action == 'restore':
            target.restore()
        elif action == 'close':
            target.close()
        elif action == 'move':
            if x is None or y is None:
                return {"success": False, "error_message": "Both 'x' and 'y' coordinates are required for the 'move' action."}
            target.moveTo(x, y)
        elif action == 'resize':
            if width is None or height is None:
                return {"success": False, "error_message": "Both 'width' and 'height' are required for the 'resize' action."}
            target.resizeTo(width, height)

        return {"success": True, "result": f"Successfully performed '{action}' on window '{actual_title}'."}

    except Exception as e:
        return {"success": False, "error_message": f"Failed to perform '{action}' on window '{window_title}': {str(e)}"}

# ----------------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------------
tool_system_instance.register_tool(
    tool_name="list_active_windows",
    description="Returns a list of all currently open and visible application windows on the user's desktop. Required to find exact titles.",
    module_path=__name__,
    function_name_in_module="list_active_windows",
    func_callable=list_active_windows,
    pydantic_model=ListWindowsSchema
)

tool_system_instance.register_tool(
    tool_name="manage_window",
    description="OS-level control to minimize, maximize, restore, close, move, or resize a specific application window on the desktop.",
    module_path=__name__,
    function_name_in_module="manage_window",
    func_callable=manage_window,
    pydantic_model=ManageWindowSchema
)
