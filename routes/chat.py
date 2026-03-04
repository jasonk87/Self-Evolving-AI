
from flask import request, jsonify
from ai_assistant.core.conversational_alerts import execute_alert_action
from . import api_bp
import logging
import app_globals
from ai_assistant.core.project_manager import find_project
from ai_assistant.llm_interface.gemini_client import invoke_split_brain_async
import json
import asyncio
from ai_assistant.custom_tools.reminder_tool import set_reminder, list_reminders, delete_reminder, update_reminder
from ai_assistant.core.task_manager import ActiveTaskType, ActiveTaskStatus

logger = logging.getLogger(__name__)

DEFAULT_NOTICE_SCOPE = "local_default"


def _handle_chat_config_command(session_id, message, images=None):
    stripped_message = (message or '').strip()
    managed_settings = app_globals.config_manager.get_all_settings()

    if stripped_message.startswith('/show-config'):
        parts = stripped_message.split(maxsplit=1)
        if len(parts) == 1:
            available_keys = ', '.join(sorted(managed_settings.keys()))
            response = f"Managed settings: {available_keys}. Use /show-config <KEY> to inspect."
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": True, "status_code": 200}

        key = parts[1].strip()
        if key not in managed_settings:
            response = f"Unknown setting '{key}'. Use /show-config to list available keys."
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        value = managed_settings[key]
        response = f"{key} = {json.dumps(value) if isinstance(value, (dict, list)) else value}"
        app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
        app_globals.chat_manager.add_message(session_id, 'assistant', response)
        return {"response": response, "success": True, "status_code": 200}

    if stripped_message.startswith('/set-config '):
        parts = stripped_message.split(maxsplit=2)
        if len(parts) < 3:
            response = 'Usage: /set-config <SETTING_KEY> <value>'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        key = parts[1].strip()
        raw_value = parts[2].strip()
        if key not in managed_settings:
            response = f"Unknown setting '{key}'. Use /show-config to list available keys."
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        try:
            coerced_value = app_globals.config_manager.coerce_setting_value(key, raw_value)
        except (TypeError, ValueError) as e:
            response = f"Failed to parse value for {key}: {e}"
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        updated = app_globals.config_manager.update_setting(key, coerced_value)
        response = f"Updated {key} to {coerced_value}." if updated else f"Failed to update {key}."
        app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
        app_globals.chat_manager.add_message(session_id, 'assistant', response)
        return {"response": response, "success": bool(updated), "status_code": 200 if updated else 400}

    if stripped_message.startswith('/set-reminder '):
        payload = stripped_message[len('/set-reminder '):].strip()
        if '::' not in payload:
            response = 'Usage: /set-reminder <time> :: <message> (examples: /set-reminder in 5 minutes :: stretch, /set-reminder every 2 hours :: hydrate)'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        time_str, reminder_message = [segment.strip() for segment in payload.split('::', 1)]
        if not time_str or not reminder_message:
            response = 'Usage: /set-reminder <time> :: <message>'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        result = set_reminder(reminder_message, time_str)
        success = not str(result).startswith('Error:')
        app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
        app_globals.chat_manager.add_message(session_id, 'assistant', result)
        return {"response": result, "success": success, "status_code": 200 if success else 400}

    if stripped_message.startswith('/list-reminders'):
        parts = stripped_message.split(maxsplit=1)
        status = 'pending'
        if len(parts) == 2 and parts[1].strip().lower() in {'pending', 'fired', 'all'}:
            status = parts[1].strip().lower()
        result = list_reminders(status=status)
        app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
        app_globals.chat_manager.add_message(session_id, 'assistant', result)
        return {"response": result, "success": True, "status_code": 200}

    if stripped_message.startswith('/delete-reminder '):
        parts = stripped_message.split(maxsplit=1)
        if len(parts) < 2:
            response = 'Usage: /delete-reminder <reminder_id>'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        reminder_id = parts[1].strip()
        result = delete_reminder(reminder_id)
        success = 'deleted' in result.lower()
        app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
        app_globals.chat_manager.add_message(session_id, 'assistant', result)
        return {"response": result, "success": success, "status_code": 200 if success else 404}

    if stripped_message.startswith('/update-reminder '):
        payload = stripped_message[len('/update-reminder '):].strip()
        segments = [segment.strip() for segment in payload.split('::')]

        if len(segments) < 2 or not segments[0]:
            response = 'Usage: /update-reminder <reminder_id> :: <new_time_or_-> [:: <new_message_or_->]'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        reminder_id = segments[0]
        new_time = segments[1] if len(segments) > 1 and segments[1] not in {'', '-'} else None
        new_message = segments[2] if len(segments) > 2 and segments[2] not in {'', '-'} else None

        if new_time is None and new_message is None:
            response = 'Provide at least one update value for time or message.'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        result = update_reminder(reminder_id, new_time_str=new_time, new_message=new_message)
        success = not str(result).startswith('Error:') and 'not found' not in str(result).lower()
        status_code = 200 if success else (404 if 'not found' in str(result).lower() else 400)
        app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
        app_globals.chat_manager.add_message(session_id, 'assistant', result)
        return {"response": result, "success": success, "status_code": status_code}

    return None


def _record_delegated_task(session_id: str, task_id: str, delegated_prompt: str):
    session = app_globals.chat_manager.get_session(session_id) or {}
    metadata = dict(session.get('metadata') or {})
    delegated_tasks = list(metadata.get('delegated_tasks') or [])
    delegated_tasks.append({
        'task_id': task_id,
        'prompt_preview': delegated_prompt[:120],
        'created_at': app_globals.config_manager.get_time() if app_globals.config_manager else None,
    })
    metadata['delegated_tasks'] = delegated_tasks[-20:]
    app_globals.chat_manager.update_session_metadata(session_id, metadata)


def _launch_delegated_code_task(task_id: str, session_id: str, delegated_prompt: str, history_snapshot):
    if not app_globals.orchestrator or not app_globals.ai_loop:
        app_globals.task_manager.update_task_status(
            task_id,
            ActiveTaskStatus.FAILED_INTERRUPTED,
            reason='Delegation unavailable: orchestrator loop not ready.',
        )
        app_globals.chat_manager.add_message(session_id, 'assistant', f"Delegated task {task_id[:8]} failed to start: orchestrator unavailable.")
        return

    async def _run_delegated_prompt():
        try:
            app_globals.task_manager.update_task_status(
                task_id,
                ActiveTaskStatus.GENERATING_CODE,
                step_desc='Delegated coding agent running',
            )
            response_text, response_images = await app_globals.orchestrator.process_prompt(
                delegated_prompt,
                conversation_history=history_snapshot,
                user_session_id=session_id,
            )
            app_globals.task_manager.update_task_status(
                task_id,
                ActiveTaskStatus.COMPLETED_SUCCESSFULLY,
                reason='Delegated coding task completed.',
                out_preview=(response_text or '')[:300],
            )
            completion_message = f"Delegated task {task_id[:8]} completed.\n\n{response_text or 'No response generated.'}"
            app_globals.chat_manager.add_message(session_id, 'assistant', completion_message, images=response_images or None)
            _append_user_work_notice(
                message=f"Delegated task {task_id[:8]} completed.",
                source_session_id=session_id,
                task_id=task_id,
                status='completed',
            )
        except Exception as e:
            app_globals.task_manager.update_task_status(
                task_id,
                ActiveTaskStatus.FAILED_UNKNOWN,
                reason=f'Delegated task failed: {e}',
            )
            app_globals.chat_manager.add_message(session_id, 'assistant', f"Delegated task {task_id[:8]} failed: {e}")
            _append_user_work_notice(
                message=f"Delegated task {task_id[:8]} failed: {e}",
                source_session_id=session_id,
                task_id=task_id,
                status='failed',
            )

    asyncio.run_coroutine_threadsafe(_run_delegated_prompt(), app_globals.ai_loop)




def _append_user_work_notice(message: str,
                             source_session_id: str,
                             task_id: str,
                             status: str):
    if not hasattr(app_globals.chat_manager, 'add_user_notice'):
        return
    try:
        app_globals.chat_manager.add_user_notice(
            user_scope=DEFAULT_NOTICE_SCOPE,
            message=message,
            notice_type='delegated_work',
            source_session_id=source_session_id,
            task_id=task_id,
            status=status,
            metadata={},
        )
    except Exception:
        logger.exception('Failed to append user work notice')


def _resolve_task_from_query(task_query: str):
    if not app_globals.task_manager:
        return None

    task_query = (task_query or '').strip()
    if not task_query:
        return None

    exact = app_globals.task_manager.get_task_including_archive(task_query)
    if exact:
        return exact

    candidates = []
    try:
        for task in app_globals.task_manager.list_active_tasks():
            if task.task_id.startswith(task_query):
                candidates.append(task)
        for task in app_globals.task_manager.list_archived_tasks(limit=200):
            if task.task_id.startswith(task_query):
                candidates.append(task)
    except Exception:
        return None

    if len(candidates) == 1:
        return candidates[0]
    return None

def _handle_delegation_commands(session_id: str, message: str, images=None):
    stripped = (message or '').strip()

    if stripped.startswith('/delegate-code '):
        delegated_prompt = stripped[len('/delegate-code '):].strip()
        if not delegated_prompt:
            response = 'Usage: /delegate-code <coding task request>'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 400}

        if not app_globals.task_manager:
            response = 'Delegation unavailable: task manager not initialized.'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 503}

        new_task = app_globals.task_manager.add_task(
            description=f'Delegated coding task: {delegated_prompt[:120]}',
            task_type=ActiveTaskType.EPHEMERAL_AGENT_TASK,
            details={
                'source': 'chat_delegate',
                'delegated_prompt': delegated_prompt,
                'worker_profile': 'coder_worker',
                'scope_type': 'session',
                'capability_profile': 'workspace_code_generation',
                'retention_policy': 'drop_task_memory_on_completion_keep_artifacts',
            },
            session_id=session_id,
        )

        _record_delegated_task(session_id, new_task.task_id, delegated_prompt)

        session_data = app_globals.chat_manager.get_session(session_id) or {}
        history_snapshot = list(session_data.get('history', []))

        app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
        ack = (
            "I’m on it — I spun this up in the background and I’ll let you know when it’s ready. "
            f"Ask /work-status {new_task.task_id[:8]} anytime."
        )
        app_globals.chat_manager.add_message(session_id, 'assistant', ack)

        _launch_delegated_code_task(new_task.task_id, session_id, delegated_prompt, history_snapshot)

        return {"response": ack, "success": True, "status_code": 202}

    if stripped.startswith('/work-inbox'):
        if not hasattr(app_globals.chat_manager, 'list_user_notices'):
            response = 'Work inbox is unavailable in this runtime.'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": False, "status_code": 503}

        parts = stripped.split(maxsplit=1)
        arg_raw = parts[1].strip() if len(parts) > 1 else ''
        arg_lower = arg_raw.lower()

        if arg_lower == 'clear':
            cleared = app_globals.chat_manager.mark_user_notices_read(DEFAULT_NOTICE_SCOPE)
            response = f'Marked {cleared} work notice(s) as read.'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": True, "status_code": 200}

        if arg_lower == 'read' or arg_lower.startswith('read '):
            raw_ids = arg_raw[4:].strip()
            notice_ids = [part.strip() for part in raw_ids.split(',') if part.strip()]
            if not notice_ids:
                response = 'Usage: /work-inbox read <notice_id>[,<notice_id2>,...]'
                app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
                app_globals.chat_manager.add_message(session_id, 'assistant', response)
                return {"response": response, "success": False, "status_code": 400}

            cleared = app_globals.chat_manager.mark_user_notices_read(DEFAULT_NOTICE_SCOPE, notice_ids=notice_ids)
            response = f'Marked {cleared} selected work notice(s) as read.'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": True, "status_code": 200}

        include_read = arg_lower == 'all'
        notices = app_globals.chat_manager.list_user_notices(DEFAULT_NOTICE_SCOPE, include_read=include_read, limit=10)
        if not notices:
            response = 'No work notices yet.'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": True, "status_code": 200}

        lines = ['Work Inbox:']
        for n in notices:
            tid = (n.get('task_id') or '')[:8]
            status = n.get('status') or 'unknown'
            source = (n.get('source_session_id') or '')[:8]
            msg = n.get('message') or ''
            lines.append(f"- [{n.get('id')}] {tid} ({status}) from {source}: {msg}")
        response = "\n".join(lines)
        app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
        app_globals.chat_manager.add_message(session_id, 'assistant', response)
        return {"response": response, "success": True, "status_code": 200}

    if stripped.startswith('/work-status'):
        parts = stripped.split(maxsplit=1)
        target_id = parts[1].strip() if len(parts) > 1 else None

        if target_id:
            task = _resolve_task_from_query(target_id)
            if not task:
                response = f'No task found for id {target_id}.'
                app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
                app_globals.chat_manager.add_message(session_id, 'assistant', response)
                return {"response": response, "success": False, "status_code": 404}

            response = f"Task {task.task_id[:8]} is {task.status.name}."
            if task.current_step_description:
                response += f" Step: {task.current_step_description}."
            if task.status_reason:
                response += f" Reason: {task.status_reason}"
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": True, "status_code": 200}

        session_data = app_globals.chat_manager.get_session(session_id) or {}
        delegated_tasks = ((session_data.get('metadata') or {}).get('delegated_tasks') or [])
        if not delegated_tasks:
            response = 'No delegated tasks tracked in this chat yet.'
            app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
            app_globals.chat_manager.add_message(session_id, 'assistant', response)
            return {"response": response, "success": True, "status_code": 200}

        latest_entries = []
        for entry in delegated_tasks[-3:]:
            task_id = (entry.get('task_id') or '').strip()
            if not task_id:
                continue
            task = _resolve_task_from_query(task_id)
            status_text = task.status.name if task else 'UNKNOWN'
            latest_entries.append(f"{task_id[:8]}:{status_text}")

        response = (
            f"Recent delegated tasks: {', '.join(latest_entries)}. "
            "Use /work-status <task_id> for details."
        )
        app_globals.chat_manager.add_message(session_id, 'user', message, images=images)
        app_globals.chat_manager.add_message(session_id, 'assistant', response)
        return {"response": response, "success": True, "status_code": 200}

    return None



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
def summarize_session(session_id):
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
        import asyncio
        future = asyncio.run_coroutine_threadsafe(
            invoke_split_brain_async(prompt, context_text="Summarizing Session"),
            app_globals.ai_loop
        )
        response_text, _ = future.result()

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
def chat():
    from ai_assistant.core.background_service import report_user_activity
    report_user_activity() # Signal user activity
    
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

    stripped_message = (message or "").strip()
    if stripped_message.startswith('/task-action '):
        parts = stripped_message.split(maxsplit=2)
        if len(parts) < 3:
            action_response = "Usage: /task-action <task_id> <retry|summarize|pause>"
            app_globals.chat_manager.add_message(session_id, "user", message, images=images)
            app_globals.chat_manager.add_message(session_id, "assistant", action_response)
            return jsonify({"response": action_response, "session_id": session_id, "success": False, "images": []}), 400

        task_id = parts[1].strip()
        action = parts[2].strip().lower()
        result = execute_alert_action(task_id, action)
        action_response = result.get("message") or result.get("error") or "Action processed."

        app_globals.chat_manager.add_message(session_id, "user", message, images=images)
        app_globals.chat_manager.add_message(session_id, "assistant", action_response)

        return jsonify({
            "response": action_response,
            "session_id": session_id,
            "success": bool(result.get("success")),
            "images": []
        }), (200 if result.get("success") else 400)

    delegation_command_result = _handle_delegation_commands(session_id, message, images=images)
    if delegation_command_result:
        return jsonify({
            "response": delegation_command_result["response"],
            "session_id": session_id,
            "success": delegation_command_result["success"],
            "images": []
        }), delegation_command_result["status_code"]

    config_command_result = _handle_chat_config_command(session_id, message, images=images)
    if config_command_result:
        return jsonify({
            "response": config_command_result["response"],
            "session_id": session_id,
            "success": config_command_result["success"],
            "images": []
        }), config_command_result["status_code"]

    updated_session = app_globals.chat_manager.add_message(session_id, "user", message, images=images)
    if not updated_session:
         updated_session = session_data 
    
    current_history_list = updated_session.get('history', [])

    if not app_globals.orchestrator:
        return jsonify({"error": "Orchestrator not initialized", "success": False, "session_id": session_id}), 500
    
    try:
        import asyncio
        future = asyncio.run_coroutine_threadsafe(
            app_globals.orchestrator.process_prompt(
                full_message,
                conversation_history=current_history_list,
                session_id=session_id,
                images=images
            ),
            app_globals.ai_loop
        )
        success, response, collected_images = future.result()

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
