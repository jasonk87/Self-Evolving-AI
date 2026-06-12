from ai_assistant.tools.base import ToolBase
# ToolConfig is not used in this codebase's ToolBase, it seems tools are registered via tool_system.

class NavigateInterface(ToolBase):
    def __init__(self):
        super().__init__(tool_name="navigate_interface")
        # In this system, metadata seems to be handled by the tool registration mechanism (tool_system.py),
        # or docstrings, or schemas defined in module.
        # But looking at tool_system.py, it registers custom tools via discovery.
        # The schema is discovered from module variables if present.

    async def run(self, page: str) -> str:
        """
        Remotely navigates the user's interface to a specific page.
        """
        # Mapping pages to internal identifiers or routes
        # 'dashboard' -> 'view-mission-control'
        # 'chat' -> 'view-chat'
        # 'logs' -> 'view-sidebar-council'
        # 'settings' -> 'view-settings' (if exists)
        # 'memory_viewer' -> 'view-cortex'

        target_map = {
            "chat": "view-chat",
            "dashboard": "view-mission-control",
            "memory_viewer": "view-cortex",
            "logs": "view-sidebar-council",
            "settings": "view-settings"
        }

        target_id = target_map.get(page, "view-chat")

        try:
            # Runtime import to avoid circular dependency
            from web_app import socketio
            socketio.emit('force_navigation', {'target': target_id, 'page': page})
            return f"Navigation command sent. The user is now seeing the {page}."
        except ImportError:
            return "Error: Could not access socketio to send navigation command."
        except Exception as e:
            return f"Error sending navigation command: {str(e)}"

# Define schema for ToolSystem discovery
NAVIGATE_INTERFACE_SCHEMA = {
    "name": "navigate_interface",
    "description": "Remotely navigates the user's interface to a specific page.",
    "parameters": {
        "type": "object",
        "properties": {
            "page": {
                "type": "string",
                "description": "The page to navigate to. Valid options: 'dashboard', 'logs', 'settings', 'chat', 'memory_viewer'.",
                "enum": ["dashboard", "logs", "settings", "chat", "memory_viewer"]
            }
        },
        "required": ["page"]
    }
}

# The function to be registered
async def navigate_interface(page: str) -> str:
    """
    Remotely navigates the user's interface to a specific page.
    """
    tool = NavigateInterface()
    return await tool.run(page)
