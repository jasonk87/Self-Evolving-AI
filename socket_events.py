
"""
SocketIO event handlers and background tasks.
"""
import logging
import app_globals
import json
import os
import asyncio
import ast
from ai_assistant.core.events import EventEmitter
from ai_assistant.config import get_projects_dir
from typing import Dict, Any

logger = logging.getLogger(__name__)

def register_socket_events(socketio):
    """Registers SocketIO events and listeners."""

    @socketio.on('connect')
    def handle_connect():
        logger.info('Client connected')

    @socketio.on('disconnect')
    def handle_disconnect():
        logger.info('Client disconnected')

    @socketio.on('message')
    def handle_message(data):
        """Handles incoming socket messages (e.g. from Terminal)."""
        # Note: report_user_activity is usually imported from background_service. 
        # But global import might be safer to avoid circular if background_service imports this.
        # Ideally, we emit an event or call a safe module.
        from ai_assistant.core.background_service import report_user_activity
        report_user_activity()

        if not app_globals.orchestrator:
            socketio.emit('response', {'response': "Error: System not initialized.", 'success': False})
            return

        message = data.get('message')
        session_id = data.get('session_id')
        context = data.get('context', {})

        if not message:
            return

        if session_id:
            app_globals.chat_manager.add_message(session_id, "user", message)
        else:
            session_id = app_globals.chat_manager.create_session("Terminal Session")

        try:
            from ai_assistant.core.models.state import ExecutionState, ExecutionStatus
            state = ExecutionState(original_user_prompt=message, context_limits={"max_tokens": 100000})

            future = asyncio.run_coroutine_threadsafe(
                app_globals.orchestrator.process_prompt(state=state, session_id=session_id),
                app_globals.ai_loop
            )

            execution_state = future.result()
            success = execution_state.current_status == ExecutionStatus.COMPLETED

            response = execution_state.final_answer or ""
            collected_images = execution_state.final_images or []

            if not success and not response:
                response = "Task encountered errors:\n" + "\n".join(execution_state.errors)
            
            if session_id:
                 app_globals.chat_manager.add_message(session_id, "assistant", response, images=collected_images)

            clarification_needed = False
            if isinstance(response, str) and "'status': 'PAUSED'" in response:
                 try:
                     clean_response = response.strip()
                     if "{" in clean_response:
                        start = clean_response.find("{")
                        end = clean_response.rfind("}") + 1
                        potential_dict = clean_response[start:end]
                        
                        resp_dict = ast.literal_eval(potential_dict)
                        if isinstance(resp_dict, dict) and resp_dict.get('status') == 'PAUSED':
                                socketio.emit('request_clarification', {
                                    'question': resp_dict.get('question'),
                                    'options': resp_dict.get('options')
                                })
                                clarification_needed = True
                 except Exception as e:
                     logger.warning(f"Failed to parse potential clarification response: {e}")

            if not clarification_needed:
                socketio.emit('response', {
                    'response': response,
                    'success': success,
                    'session_id': session_id,
                    'images': collected_images
                })

        except Exception as e:
            logger.error(f"Socket message processing error: {e}")
            socketio.emit('response', {'response': f"Error: {str(e)}", 'success': False})


    # --- Bridge System Events ---
    def bridge_system_events(event_name: str, data: Dict[str, Any]):
        """Bridges internal system events to SocketIO."""
        socketio.emit(event_name, data)

    EventEmitter.register_listener(bridge_system_events)

    # --- Log Handler Logic (if needed to serve logs via socket) ---
    # The LogHandler in web_app.py uses `socketio.emit` directly. 
    # Since we passed `socketio` instance around or imported it, it works.

def watch_telemetry(shutdown_manager):
    """Background task to watch for telemetry updates."""
    # Importing here to access socketio instance from app_globals
    socketio = app_globals.socketio
    projects_dir = get_projects_dir()
    last_modified_times = {}
    logger.info(f"Starting telemetry watcher on {projects_dir}")

    while shutdown_manager.should_continue():
        try:
            if os.path.exists(projects_dir):
                for project_name in os.listdir(projects_dir):
                    project_path = os.path.join(projects_dir, project_name)
                    if os.path.isdir(project_path):
                        telemetry_path = os.path.join(project_path, "telemetry.json")
                        if os.path.exists(telemetry_path):
                            mtime = os.path.getmtime(telemetry_path)
                            if telemetry_path not in last_modified_times or last_modified_times[telemetry_path] < mtime:
                                last_modified_times[telemetry_path] = mtime
                                try:
                                    with open(telemetry_path, 'r', encoding='utf-8') as f:
                                        data = json.load(f)
                                        payload = {
                                            "project": project_name,
                                            "data": data
                                        }
                                        socketio.emit('project_update', payload)
                                        logger.info(f"Emitted project_update for {project_name}")

                                        if app_globals.orchestrator and app_globals.orchestrator.action_executor:
                                            def _run_proactive_check():
                                                try:
                                                    future = asyncio.run_coroutine_threadsafe(
                                                        app_globals.orchestrator.action_executor.handle_telemetry_update(project_name, data),
                                                        app_globals.ai_loop
                                                    )
                                                    response = future.result()
                                                    if response:
                                                        socketio.emit('log_event', {'message': f"AI: {response}", 'level': 'INFO'})
                                                        socketio.emit('chat_response', {'response': f"(Proactive) {response}", 'success': True})
                                                except Exception as e:
                                                    logger.error(f"Error in proactive check: {e}")
                                            
                                            socketio.start_background_task(_run_proactive_check)

                                except Exception as e:
                                    logger.error(f"Error reading telemetry for {project_name}: {e}")
        except Exception as e:
            logger.error(f"Error in watch_telemetry: {e}")
        
        socketio.sleep(1)
