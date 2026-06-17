# app_globals.py
"""
Shared global state for the application to avoid circular imports.
Initialized in web_app.py.
"""
import logging

from flask_socketio import SocketIO

# Global Instances
orchestrator = None
controller = None
memory_manager = None
chat_manager = None
config_manager = None
task_manager = None
notification_manager = None
latest_active_chat_session_id = None

# Initialize SocketIO here to allow imports in other modules
socketio = SocketIO()


# Logger for globals if needed
logger = logging.getLogger("app_globals")
