import os
import json
from typing import Optional, List, Dict, Any, Union
from ai_assistant.core.chat_manager import ChatSessionManager
import ai_assistant.config as config

_chat_manager_instance = None

def _get_chat_manager():
    global _chat_manager_instance
    if _chat_manager_instance is None:
        _chat_manager_instance = ChatSessionManager(os.path.join(config.project_root, "_memory_", "chat_sessions"))
    return _chat_manager_instance

def get_chat_history(session_id: Optional[str] = None, limit: int = 20) -> str:
    """
    Retrieves the chat history for a session.

    Args:
        session_id (str, optional): The ID of the session. If None, tries to find the most recent session.
        limit (int): The number of recent messages to retrieve. Defaults to 20.

    Returns:
        str: A formatted JSON string of the chat history, or an error message.
    """
    manager = _get_chat_manager()
    
    if not session_id:
        # Try to find the latest updated session
        sessions = manager.list_sessions()
        if not sessions:
            return "No chat sessions found."
        session_id = sessions[0]['id']
    
    session = manager.get_session(session_id)
    if not session:
        return f"Session {session_id} not found."
    
    history = session.get('history', [])
    # Get last 'limit' messages
    recent_history = history[-limit:]
    
    return json.dumps(recent_history, indent=2)



# Function _request_user_clarification removed per user request to use direct conversation via FINAL ANSWER.

# Tests removed as _request_user_clarification is deprecated.
# if __name__ == '__main__':
#     ...