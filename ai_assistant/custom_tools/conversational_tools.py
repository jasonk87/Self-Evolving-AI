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



def _request_user_clarification(question_text: Optional[str]=None, options: Optional[List[str]]=None, **kwargs) -> Dict[str, Any]:
    """
    Asks the user a clarifying question and returns their textual response.
    This tool is intended to be called by the AI planner when it needs more information
    or needs to resolve ambiguity to proceed with a task.

    Args:
        question_text: The question to ask the user.
        options: Optional. A list of suggested options for the user to choose from.
        **kwargs: Support for LLMs that hallucinate argument names (e.g. 'question').

    Returns:
        The user's textual reply as a special signal dictionary to pause execution.
    """
    if not question_text and 'question' in kwargs:
        question_text = kwargs['question']
    if not question_text:
        return {'status': 'ERROR', 'message': "Missing required argument 'question_text' (or 'question')."}
    print(f'\n--- AI Assistant Needs Clarification ---')
    print(question_text)
    if options and isinstance(options, list) and (len(options) > 0):
        print('Options:')
        for i, opt in enumerate(options):
            print(f'  {i + 1}. {opt}')
    formatted_msg = f'\n--- AI Assistant Needs Clarification ---\n{question_text}'
    if options and isinstance(options, list) and (len(options) > 0):
        formatted_msg += '\nOptions:'
        for i, opt in enumerate(options):
            formatted_msg += f'\n  {i + 1}. {opt}'
    formatted_msg += '\n\n(Please provide your answer in the next prompt)'
    return {'status': 'PAUSED', 'message': f'Please Answer: {question_text}', 'question': question_text, 'options': options}

if __name__ == '__main__':
    from unittest.mock import patch
    import datetime
    print('--- Testing conversational_tools.py ---')
    print('\n--- Test 1: Question with no options ---')
    with patch('builtins.input', return_value='User says yes, proceed.'):
        response1 = _request_user_clarification('Are you sure you want to format the drive?')
        print(f'Response 1: {response1}')
        assert response1 == 'User says yes, proceed.'
    print('\n--- Test 2: Question with options, user chooses number ---')
    with patch('builtins.input', return_value='2'):
        response2 = _request_user_clarification('Which project do you mean?', options=['Project Alpha', 'Project Beta', 'Project Gamma (new)'])
        print(f'Response 2: {response2}')
        assert response2 == '2'
    print('\n--- Test 3: Question with options, user types full option (simulated) ---')
    with patch('builtins.input', return_value='Project Beta'):
        response3 = _request_user_clarification('Which project do you mean?', options=['Project Alpha', 'Project Beta', 'Project Gamma (new)'])
        print(f'Response 3: {response3}')
        assert response3 == 'Project Beta'
    print('\n--- Conversational Tools Test Finished ---')