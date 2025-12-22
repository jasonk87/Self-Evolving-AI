
from flask import request, jsonify
from . import api_bp
import logging
import app_globals
from ai_assistant.core.project_manager import find_project
from ai_assistant.llm_interface.gemini_client import invoke_split_brain_async
import json

logger = logging.getLogger(__name__)

# --- Session Management Endpoints ---

@api_bp.route('/sessions', methods=['GET'])
def list_sessions():
    """Lists all chat sessions."""
    try:
        sessions = app_globals.chat_manager.list_sessions()
        return jsonify({"sessions": sessions, "success": True})
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/sessions', methods=['POST'])
def create_session():
    """Creates a new chat session."""
    data = request.json or {}
    title = data.get('title', 'New Chat')
    try:
        session_id = app_globals.chat_manager.create_session(title=title)
        return jsonify({"session_id": session_id, "success": True})
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    """Gets history for a specific session."""
    try:
        session = app_globals.chat_manager.get_session(session_id)
        if not session:
             return jsonify({"error": "Session not found", "success": False}), 404
        return jsonify({"session": session, "success": True})
    except Exception as e:
        logger.error(f"Error getting session {session_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """Deletes a chat session."""
    try:
        success = app_globals.chat_manager.delete_session(session_id)
        if success:
             return jsonify({"success": True})
        return jsonify({"error": "Session not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error deleting session {session_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/sessions/<session_id>/summarize', methods=['POST'])
async def summarize_session(session_id):
    """Triggers summarization of a chat session into an episodic memory."""
    try:
        # 1. Fetch Chat History
        session = app_globals.chat_manager.get_session(session_id)
        if not session:
            return jsonify({"error": "Session not found", "success": False}), 404

        history = session.get('history', [])
        if not history:
            return jsonify({"error": "Session is empty", "success": False}), 400

        # 2. Construct Prompt for LLM
        prompt = "Analyze the following chat session and create a concise episodic summary.\\n" \
                 "Extract key topics and the main outcome.\\n\\n" \
                 "Chat History:\\n"
        
        for msg in history[-20:]: # Limit to last 20 messages for speed/context limits
             role = msg.get('role', 'unknown')
             content = msg.get('content', '')
             prompt += f"{role.upper()}: {content[:500]}\\n" # Truncate long messages

        prompt += "\\nFormat the output as JSON with keys: 'summary' (string), 'title' (string), 'topics' (list of strings)."

        # 3. Call LLM (using split brain)
        response_text, _ = await invoke_split_brain_async(prompt, context_text="Summarizing Session")

        # 4. Parse JSON Response
        try:
             # Basic cleanup for code blocks if LLM wraps in ```json ... ```
             clean_text = response_text.replace('```json', '').replace('```', '').strip()
             data = json.loads(clean_text)
             summary = data.get('summary', 'No summary generated.')
             title = data.get('title', 'Untitled Episode')
             topics = data.get('topics', [])
        except Exception:
             # Fallback if specific formatting failed
             summary = response_text
             title = f"Episode {session_id[:8]}"
             topics = []

        # 5. Save Episode
        episode = app_globals.memory_manager.add_episode(summary, title, session_id, topics)
        
        return jsonify({"episode": episode, "success": True})

    except Exception as e:
        logger.error(f"Error summarising session {session_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

# Note: The main /chat route is distinct as it's not under /api usually, or we can move it there.
# For now, let's keep it consistent with the frontend expectations.
# Wait, the original was @app.route('/chat', methods=['POST']).
# app_globals.orchestrator is needed.

from flask import Blueprint
# We need to expose /chat, currently api_bp is /api.
# We can create a separate blueprint for chat or just add it to views_bp or define a new one.
# Let's add it to api_bp but override url? No, Blueprint URL prefix is fixed.
# Let's add it to a new blueprint 'chat_bp' or just put it in web_app.py?
# Better to put it here and attach to a blueprint. 
# The frontend likely calls /chat.
# Let's verify existing route. Yes, @app.route('/chat').
# We can use a root level blueprint or just handle it.
# Let's put it in `routes/chat.py` but register it to a blueprint that has no prefix or just use api_bp and change frontend?
# Changing frontend is out of scope for "just refactoring" if we want to minimize breakage.
# I will define a separate blueprint for root-level chat if needed, or just import it in web_app and register.

chat_bp = Blueprint('chat_bp', __name__)

@chat_bp.route('/chat', methods=['POST'])
async def chat():
    from ai_assistant.core.background_service import report_user_activity
    report_user_activity() # Signal user activity
    
    if not app_globals.orchestrator:
        return jsonify({"error": "Orchestrator not initialized"}), 500

    data = request.json
    message = data.get('message')
    images = data.get('images') # List of base64 strings
    context = data.get('context', {})
    session_id = data.get('session_id')

    # Allow processing if either message OR images are present (multimodal)
    if not message and not images:
        return jsonify({"error": "No message or images provided"}), 400

    # Ensure message is not None for safety downstream
    if message is None:
        message = ""

    # Handle Session
    if not session_id:
        # Create new session if none provided
        session_id = app_globals.chat_manager.create_session()
    
    session_data = app_globals.chat_manager.get_session(session_id)
    if not session_data:
         # Fallback if invalid ID passed
         session_id = app_globals.chat_manager.create_session()
         session_data = app_globals.chat_manager.get_session(session_id)

    # Load History
    # conversation_history = session_data.get('history', []) # Not used directly passed, orchestrator handles it via session_id or we pass it.
    
    # Inject Context into Message
    system_context = ""
    current_file = context.get('currentFile')
    
    if current_file:
        file_path = current_file.get('path')
        project_name = current_file.get('project')
        content = current_file.get('content')
        system_context += f"[System Context] User is looking at file: {file_path} in project {project_name}.\n"
        
        # Resolve Project Root for AI
        if project_name:
             try:
                 proj = find_project(project_name)
                 if proj and proj.get('root_path'):
                     system_context += f"Project Root Path: {proj.get('root_path')}\n"
                     system_context += f"NOTE: When reading files, prepend the Project Root Path if the file is not found in the root workspace.\n"
             except Exception as e:
                 logger.error(f"Failed to resolve project root for context: {e}")

        if content:
            system_context += f"Content:\n```{content}```\n"

    terminal_output = context.get('terminalOutput')
    if terminal_output:
        system_context += f"[System Context] Last Terminal Output:\n{terminal_output}\n"

    full_message = system_context + "\n" + message if system_context else message

    updated_session = app_globals.chat_manager.add_message(session_id, "user", message, images=images)
    if not updated_session:
         updated_session = session_data 
    
    current_history_list = updated_session.get('history', [])
    
    try:
        success, response, collected_images = await app_globals.orchestrator.process_prompt(
            full_message,
            conversation_history=current_history_list,
            session_id=session_id,
            images=images
        )

        if response:
             updated_session = app_globals.chat_manager.add_message(session_id, "assistant", response, images=collected_images)
        
        return jsonify({
            "response": response,
            "session_id": session_id,
            "success": success,
            "images": collected_images
        })
    except Exception as e:
        logger.error(f"Error processing prompt: {e}")
        return jsonify({"error": str(e), "success": False}), 500
