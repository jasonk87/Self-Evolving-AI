import logging
from typing import Optional
from ai_assistant.core.events import emit_system_event

logger = logging.getLogger(__name__)

class ToolBase:
    """
    Base class for tools that want to emit status updates.
    """
    def __init__(self, tool_name: str = "Tool"):
        self.tool_name = tool_name

    def emit_status(self, message: str, session_id: Optional[str] = None):
        """
        Emits a status update for this tool.
        """
        data = {
            'message': message,
            'tool': self.tool_name,
            'status': 'RUNNING'
        }
        if session_id:
            data['session_id'] = session_id

        logger.info(f"[{self.tool_name}] Status: {message}")
        emit_system_event('tool_status', data)
