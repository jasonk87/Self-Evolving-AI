import asyncio
import os
from typing import Optional # Added this import
from flask import Flask, render_template, request, jsonify
import logging # Import logging
from dataclasses import asdict
from enum import Enum
from datetime import datetime

# --- BEGIN DIAGNOSTIC LOGGING ---
print("--- DIAGNOSTIC: app.py (root) - Top of file reached ---")
logging.basicConfig(level=logging.INFO) # Basic logging config
logger = logging.getLogger(__name__)
logger.info("--- DIAGNOSTIC: app.py (root) - Logger initialized ---")
logger.info(f"--- DIAGNOSTIC: app.py (root) - Current file path: {os.path.abspath(__file__)} ---")
# --- END DIAGNOSTIC LOGGING ---

# Attempt to set up Python paths similar to main.py
import sys
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if project_root not in sys.path:
    # If app.py is in the root, project_root will be its parent.
    # We want the actual project root where ai_assistant package is.
    # Assuming app.py is at the same level as the ai_assistant directory
    current_dir_as_project_root = os.path.abspath(os.path.dirname(__file__))
    if "ai_assistant" in os.listdir(current_dir_as_project_root):
        project_root = current_dir_as_project_root
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        logger.info(f"--- DIAGNOSTIC: app.py (root) - Added project root to sys.path: {project_root} ---")

# AI Assistant Core Imports
logger.info("--- DIAGNOSTIC: app.py (root) - Attempting AI Assistant Core Imports... ---")
from ai_assistant.core.orchestrator import DynamicOrchestrator
logger.info("--- DIAGNOSTIC: app.py (root) - Imported DynamicOrchestrator ---")
# Import the new centralized initialization function
from ai_assistant.core.startup_services import initialize_core_services
logger.info("--- DIAGNOSTIC: app.py (root) - Imported initialize_core_services ---")
# TaskManager and NotificationManager might still be needed for type hints or direct use if any
from ai_assistant.core.task_manager import TaskManager
logger.info("--- DIAGNOSTIC: app.py (root) - Imported TaskManager ---")
from ai_assistant.core.notification_manager import NotificationManager
logger.info("--- DIAGNOSTIC: app.py (root) - Imported NotificationManager ---")
import ai_assistant.core.suggestion_manager as suggestion_manager_module # Import the module
logger.info("--- DIAGNOSTIC: app.py (root) - Imported suggestion_manager_module ---")


# Global variables for AI services
orchestrator: Optional[DynamicOrchestrator] = None
# Keep references to task_manager and notification_manager if needed by other parts of app.py
# For now, they are primarily managed within initialize_core_services
_task_manager_instance: Optional[TaskManager] = None
_notification_manager_instance: Optional[NotificationManager] = None
# No _suggestion_manager_instance needed as we'll use module functions

def startup_event():
    """Initializes AI services. Designed to be run in an asyncio event loop."""
    global orchestrator, _task_manager_instance, _notification_manager_instance
    logger.info("--- DIAGNOSTIC: startup_event() called ---")
    if orchestrator is not None: # Assuming orchestrator is the primary flag for initialization
        logger.info("--- DIAGNOSTIC: startup_event() - AI services already initialized. Skipping. ---")
        print("Flask App: AI services already initialized.")
        return

    logger.info("--- DIAGNOSTIC: startup_event() - Initializing AI services... ---")
    print("Flask App: Initializing AI services via centralized function...")
    try:
        logger.info("--- DIAGNOSTIC: startup_event() - Entering try block for initialize_core_services ---")
        # initialize_core_services is an async function
        # We need to run it in an event loop.
        # Flask's app.before_first_request is synchronous.
        # A common pattern for Flask is to run async setup in a separate thread
        # or manage an event loop if the web server supports it (e.g., Gunicorn with Uvicorn workers).
        # For simplicity here, and assuming a dev environment, we'll run it using asyncio.run.
        # This might block if initialize_core_services is very long-running.
        # For production, consider a more robust async integration.

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Assuming initialize_core_services returns orchestrator, task_manager, notification_manager (3 items)
        orch, tm, nm = loop.run_until_complete(initialize_core_services())
        loop.close()

        orchestrator = orch
        _task_manager_instance = tm
        _notification_manager_instance = nm
        logger.info(f"--- DIAGNOSTIC: startup_event() - Orchestrator initialized: {isinstance(orchestrator, DynamicOrchestrator)} ---")
        logger.info(f"--- DIAGNOSTIC: startup_event() - TaskManager initialized: {isinstance(_task_manager_instance, TaskManager)} ---")
        logger.info(f"--- DIAGNOSTIC: startup_event() - NotificationManager initialized: {isinstance(_notification_manager_instance, NotificationManager)} ---")
        # Suggestion manager module is imported, no instance to check here.
        print("Flask App: AI services initialized successfully.")

    except Exception as e:
        print(f"Flask App: CRITICAL ERROR during AI services initialization: {e}")
        logger.error(f"--- DIAGNOSTIC: startup_event() - CRITICAL ERROR: {e} ---", exc_info=True)
        orchestrator = None # Ensure it's None if initialization fails
        _task_manager_instance = None # Also ensure these are None on error
        _notification_manager_instance = None # Also ensure these are None on error

logger.info("--- DIAGNOSTIC: app.py (root) - Initializing Flask app object... ---")
app = Flask(__name__)
logger.info("--- DIAGNOSTIC: app.py (root) - Flask app object initialized. ---")
startup_event() # Call the startup_event function


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/chat_api', methods=['POST'])
async def chat_api():
    global orchestrator
    if orchestrator is None:
        return jsonify({"error": "AI services are not initialized. Please check server logs."}), 500

    data = request.get_json()
    user_message = data.get('message')
    user_id = data.get('user_id') # Extract user_id

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    try:
        # Orchestrator now returns: success_bool, data_dict
        # data_dict = {"chat_response": Optional[str], "project_area_html": Optional[str]}
        success, response_data = await orchestrator.process_prompt(user_message, user_id=user_id)

        chat_response_text: Optional[str] = None
        project_area_html_content: Optional[str] = None

        if isinstance(response_data, dict):
            chat_response_text = response_data.get("chat_response")
            project_area_html_content = response_data.get("project_area_html")
        elif isinstance(response_data, str):
            chat_response_text = response_data
            # project_area_html_content remains None, which is correct
        else:
            # Should not happen based on orchestrator's current return types, but good for robustness
            success = False # Indicate an issue if the response type is unexpected
            chat_response_text = "Error: Received unexpected response format from AI core."
            # project_area_html_content remains None

        # Construct the final JSON response for the frontend
        json_response_payload = {
            "success": success,
            "chat_response": chat_response_text,
            "project_area_html": project_area_html_content
        }

        # Determine HTTP status code.
        # Even if 'success' from orchestrator is False (e.g. plan failed but error rephrased),
        # the API call itself might be considered successful (HTTP 200) if a message is returned.
        # Critical internal server errors would still result in HTTP 500 from the except block.
        status_code = 200

        return jsonify(json_response_payload), status_code
    except Exception as e:
        print(f"Error in /chat_api: {e}") # Log the error
        # Ensure consistent error structure for critical failures
        return jsonify({
            "success": False,
            "chat_response": "An internal server error occurred while processing your message.",
            "project_area_html": None
        }), 500

@app.route('/api/proactive_greeting', methods=['GET'])
def proactive_greeting():
    """
    Endpoint to provide a proactive greeting message.
    """
    greeting_message = "Hello! I'm Weibo, your AI assistant. How can I help you today?"
    logger.info(f"Proactive greeting requested, sending: '{greeting_message}'")
    return jsonify({"message": greeting_message})

# --- API Endpoints for Status Panel Data & Analysis ---

def format_task_for_json(task):
    """Helper to convert an ActiveTask object (or similar) to a JSON-serializable dict."""
    if not task:
        return None

    task_dict = {}
    if hasattr(task, '__dict__'): # For standard objects
        task_dict = task.__dict__.copy()
    elif hasattr(task, '_asdict'): # For namedtuples
        task_dict = task._asdict()
    elif hasattr(task, 'task_id'): # Fallback for ActiveTask like objects if not easily dictable
        task_dict = {
            "task_id": task.task_id,
            "description": task.description,
            "status": task.status,
            "task_type": task.task_type,
            "related_item_id": task.related_item_id,
            "created_at": task.created_at,
            "last_updated_at": task.last_updated_at,
            "status_reason": task.status_reason,
            "current_step_description": task.current_step_description,
            "progress_percentage": task.progress_percentage,
            "details": task.details
        }
    else: # Should not happen for ActiveTask
        return str(task)


    for key, value in task_dict.items():
        if isinstance(value, Enum): # Enum was imported at the top
            task_dict[key] = value.name
        elif isinstance(value, datetime): # datetime was imported at the top
            task_dict[key] = value.isoformat()
        # Add other type conversions if necessary (e.g., complex nested objects)
    return task_dict

@app.route('/api/status/active_tasks', methods=['GET'])
def get_active_tasks():
    global _task_manager_instance
    logger.info(f"--- DIAGNOSTIC: Route /api/status/active_tasks called. Task Manager: {_task_manager_instance} ---")
    if _task_manager_instance is None:
        logger.warning("--- DIAGNOSTIC: /api/status/active_tasks - Task manager not initialized, returning 503 ---")
        return jsonify({"error": "Task manager not initialized"}), 503

    try:
        active_tasks = _task_manager_instance.list_active_tasks()
        logger.info(f"--- DIAGNOSTIC: /api/status/active_tasks - Found {len(active_tasks)} active tasks. ---")

        # Convert tasks to JSON-serializable format
        formatted_tasks = [format_task_for_json(task) for task in active_tasks]
        return jsonify(formatted_tasks)
    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/status/active_tasks - Error fetching active tasks: {e} ---", exc_info=True)
        return jsonify({"error": "Failed to fetch active tasks"}), 500

@app.route('/api/status/notifications', methods=['GET'])
def get_recent_notifications():
    global _notification_manager_instance
    logger.info(f"--- DIAGNOSTIC: Route /api/status/notifications called. Notification Manager: {_notification_manager_instance} ---")
    if _notification_manager_instance is None:
        logger.warning("--- DIAGNOSTIC: /api/status/notifications - Notification manager not initialized, returning 503 ---")
        return jsonify({"error": "Notification manager not initialized"}), 503

    # Fetch, for example, the 5 most recent unread notifications
    # Make sure NotificationStatus is imported or accessible if using Enum directly
    from ai_assistant.core.notification_manager import NotificationStatus # Import if not already
    logger.info("--- DIAGNOSTIC: /api/status/notifications - NotificationStatus imported ---")

    try:
        # Assuming get_notifications can take status_filter as string or Enum
        # Adjust if your NotificationManager expects Enum type directly for status_filter
        notifications = _notification_manager_instance.get_notifications(
            status_filter=NotificationStatus.UNREAD, # Or "unread" if your method handles string
            limit=5
        )
        logger.info(f"--- DIAGNOSTIC: /api/status/notifications - Found {len(notifications)} unread notifications. ---")
    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/status/notifications - Error fetching notifications: {e} ---", exc_info=True)
        print(f"Error fetching notifications: {e}") # Log error
        return jsonify({"error": "Failed to fetch notifications"}), 500

    formatted_notifications = []
    for notif in notifications:
        notif_dict = {
            "notification_id": notif.notification_id,
            "event_type": notif.event_type.name if isinstance(notif.event_type, Enum) else str(notif.event_type),
            "summary_message": notif.summary_message,
            "timestamp": notif.timestamp.isoformat() if isinstance(notif.timestamp, datetime) else str(notif.timestamp),
            "status": notif.status.name if isinstance(notif.status, Enum) else str(notif.status),
            "related_item_id": notif.related_item_id,
            "related_item_type": notif.related_item_type
        }
        formatted_notifications.append(notif_dict)

    return jsonify(formatted_notifications)

@app.route('/api/analyze_display', methods=['POST'])
async def analyze_display_api():
    global orchestrator
    if orchestrator is None:
        return jsonify({"success": False, "error": "AI services are not initialized."}), 503

    data = request.get_json()
    html_content = data.get('html_content')

    if not html_content:
        return jsonify({"success": False, "error": "No HTML content provided for analysis."}), 400

    try:
        analysis_prompt = (
            f"The user has requested an analysis of the following HTML content currently displayed in their project area. "
            f"Please review this HTML code and provide a brief, user-friendly summary or analysis of its structure, "
            f"purpose, or any notable features. If it contains scripts, briefly describe what they might do. "
            f"Avoid simply repeating the code. Focus on insights.\n\n"
            f"HTML Content to Analyze:\n```html\n{html_content[:3000]}\n```"
            f"{'... (HTML content truncated)' if len(html_content) > 3000 else ''}"
        )

        success, response_data = await orchestrator.process_prompt(analysis_prompt, user_id="system_display_analyzer")
        ai_analysis_text = response_data.get("chat_response")

        if success and ai_analysis_text:
            return jsonify({"success": True, "analysis_text": ai_analysis_text}), 200
        elif ai_analysis_text:
             return jsonify({"success": False, "analysis_text": ai_analysis_text, "error": "AI processed the request but indicated an issue."}), 200
        else:
            error_message = response_data.get("error", "AI analysis failed or returned no text.")
            return jsonify({"success": False, "error": error_message}), 500

    except Exception as e:
        print(f"Error in /api/analyze_display: {e}")
        return jsonify({"success": False, "error": "An internal server error occurred during content analysis."}), 500

def format_suggestion_for_json(suggestion):
    """Helper to convert a Suggestion object to a JSON-serializable dict."""
    if not suggestion:
        return None
    # Assuming Suggestion object might have attributes like:
    # suggestion_id, title, description, status, created_at, last_updated_at, reason, type
    # Adjust fields as necessary based on actual Suggestion object structure
    sug_dict = {}
    if hasattr(suggestion, '__dict__'):
        sug_dict = suggestion.__dict__.copy()
    elif hasattr(suggestion, '_asdict'): # For namedtuples
        sug_dict = suggestion._asdict()
    else: # Manual creation if no easy dict conversion
        sug_dict = {
            "suggestion_id": getattr(suggestion, 'suggestion_id', None),
            "title": getattr(suggestion, 'title', 'N/A'),
            "description": getattr(suggestion, 'description', 'No description'),
            "status": getattr(suggestion, 'status', 'UNKNOWN'),
            "created_at": getattr(suggestion, 'created_at', None),
            "last_updated_at": getattr(suggestion, 'last_updated_at', None),
            "reason": getattr(suggestion, 'reason', None),
            "type": getattr(suggestion, 'suggestion_type', 'GENERAL') # Example field name
        }

    for key, value in sug_dict.items():
        if isinstance(value, Enum):
            sug_dict[key] = value.name
        elif isinstance(value, datetime):
            sug_dict[key] = value.isoformat()
    return sug_dict

@app.route('/api/suggestions', methods=['GET'])
def get_suggestions_api():
    # Using suggestion_manager_module directly
    logger.info(f"--- DIAGNOSTIC: Route /api/suggestions called. Using suggestion_manager_module. ---")

    try:
        status_filter_str = request.args.get('status')
        logger.info(f"--- DIAGNOSTIC: /api/suggestions - Requested status filter: {status_filter_str} ---")

        all_suggestions = suggestion_manager_module.list_suggestions()

        suggestions_to_return = []
        if status_filter_str and status_filter_str != 'all':
            # Ensure status comparison is robust, assuming suggestion['status'] is a string
            suggestions_to_return = [
                sug for sug in all_suggestions
                if str(sug.get('status', '')).lower() == status_filter_str.lower()
            ]
        else: # 'all' or no status filter means return all suggestions
            suggestions_to_return = all_suggestions

        logger.info(f"--- DIAGNOSTIC: /api/suggestions - Found {len(suggestions_to_return)} suggestions after filtering. ---")
        formatted_suggestions = [format_suggestion_for_json(sug) for sug in suggestions_to_return]
        return jsonify(formatted_suggestions)
    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/suggestions - Error fetching suggestions: {e} ---", exc_info=True)
        return jsonify({"error": "Failed to fetch suggestions"}), 500

@app.route('/api/suggestions/<string:suggestion_id>/approve', methods=['POST'])
def approve_suggestion_api(suggestion_id):
    global _notification_manager_instance # Needed by suggestion_manager_module.approve_suggestion
    logger.info(f"--- DIAGNOSTIC: Route /api/suggestions/{suggestion_id}/approve POST called ---")
    data = request.get_json()
    reason = data.get('reason') if data else None

    try:
        # Assuming approve_suggestion function exists in the module and handles NotificationManager correctly
        success = suggestion_manager_module.approve_suggestion(
            suggestion_id,
            reason=reason,
            notification_manager=_notification_manager_instance
        )
        if success:
            updated_suggestion = suggestion_manager_module.find_suggestion(suggestion_id)
            return jsonify({"success": True, "message": "Suggestion approved.", "suggestion": format_suggestion_for_json(updated_suggestion)}), 200
        else:
            # find_suggestion to check if it was not found vs. other failure
            sugg = suggestion_manager_module.find_suggestion(suggestion_id)
            if sugg is None:
                return jsonify({"success": False, "error": "Suggestion not found."}), 404
            return jsonify({"success": False, "error": "Failed to approve suggestion."}), 400 # Or 500 if it's an internal error
    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/suggestions/{suggestion_id}/approve - Error: {e} ---", exc_info=True)
        return jsonify({"success": False, "error": "An internal server error occurred."}), 500

@app.route('/api/suggestions/<string:suggestion_id>/deny', methods=['POST'])
def deny_suggestion_api(suggestion_id):
    global _notification_manager_instance # Needed by suggestion_manager_module.deny_suggestion
    logger.info(f"--- DIAGNOSTIC: Route /api/suggestions/{suggestion_id}/deny POST called ---")
    data = request.get_json()
    reason = data.get('reason') if data else None

    try:
        success = suggestion_manager_module.deny_suggestion(
            suggestion_id,
            reason=reason,
            notification_manager=_notification_manager_instance
        )
        if success:
            updated_suggestion = suggestion_manager_module.find_suggestion(suggestion_id)
            return jsonify({"success": True, "message": "Suggestion denied.", "suggestion": format_suggestion_for_json(updated_suggestion)}), 200
        else:
            sugg = suggestion_manager_module.find_suggestion(suggestion_id)
            if sugg is None:
                return jsonify({"success": False, "error": "Suggestion not found."}), 404
            return jsonify({"success": False, "error": "Failed to deny suggestion."}), 400
    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/suggestions/{suggestion_id}/deny - Error: {e} ---", exc_info=True)
        return jsonify({"success": False, "error": "An internal server error occurred."}), 500

@app.route('/api/notifications/<string:notification_id>/actioned', methods=['POST'])
def mark_notification_actioned_api(notification_id):
    global _notification_manager_instance
    logger.info(f"--- DIAGNOSTIC: Route /api/notifications/{notification_id}/actioned POST called ---")
    if _notification_manager_instance is None:
        return jsonify({"success": False, "error": "Notification manager not initialized"}), 503

    try:
        success = _notification_manager_instance.mark_as_read([notification_id])
        if success:
            return jsonify({"success": True, "message": f"Notification {notification_id} marked as actioned (read)."}), 200
        else:
            # Check if the notification exists to give a more specific error
            notification = _notification_manager_instance._get_notification_by_id(notification_id) # Protected access for check
            if not notification:
                return jsonify({"success": False, "error": "Notification not found or already actioned."}), 404
            return jsonify({"success": False, "error": "Failed to mark notification as actioned."}), 400
    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/notifications/{notification_id}/actioned - Error: {e} ---", exc_info=True)
        return jsonify({"success": False, "error": "An internal server error occurred."}), 500

# --- Task Specific Action API Endpoints ---

@app.route('/api/tasks/<string:task_id>/plan', methods=['GET'])
def get_task_plan_api(task_id):
    global _task_manager_instance
    logger.info(f"--- DIAGNOSTIC: Route /api/tasks/{task_id}/plan GET called ---")
    if _task_manager_instance is None:
        logger.warning(f"--- DIAGNOSTIC: /api/tasks/{task_id}/plan - Task manager not initialized, returning 503 ---")
        return jsonify({"error": "Task manager not initialized"}), 503

    try:
        # Assuming TaskManager has a method to get plan details
        # This method might return a list of strings, or a list of step objects, etc.
        # For now, let's assume it returns something directly serializable or a simple structure.
        if hasattr(_task_manager_instance, 'get_task_by_id'):
            task = _task_manager_instance.get_task_by_id(task_id)
            if not task:
                return jsonify({"error": "Task not found"}), 404

            # Assuming the task object has a 'plan_details' attribute or similar
            # This is highly dependent on the Task object structure from TaskManager
            plan_details = getattr(task, 'plan_details', None)
            if hasattr(task, 'get_plan_steps_descriptions'): # Prioritize a method if exists
                plan_details = task.get_plan_steps_descriptions()
            elif hasattr(task, 'plan') and isinstance(task.plan, list): # Common attribute name
                plan_details = task.plan

            if plan_details is not None:
                # If plan_details are complex objects, they might need their own formatting function.
                # For now, assume they are simple enough or TaskManager returns them ready.
                return jsonify({"success": True, "task_id": task_id, "plan": plan_details}), 200
            else:
                return jsonify({"success": False, "error": "Plan details not available for this task or task attribute missing."}), 404
        else:
            logger.warning(f"--- DIAGNOSTIC: /api/tasks/{task_id}/plan - TaskManager missing get_task_by_id method. ---")
            return jsonify({"error": "Task retrieval method not available on manager."}), 500

    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/tasks/{task_id}/plan - Error: {e} ---", exc_info=True)
        return jsonify({"success": False, "error": "An internal server error occurred while fetching task plan."}), 500

@app.route('/api/tasks/<string:task_id>/complete', methods=['POST'])
def complete_task_api(task_id):
    global _task_manager_instance
    logger.info(f"--- DIAGNOSTIC: Route /api/tasks/{task_id}/complete POST called ---")
    if _task_manager_instance is None:
        return jsonify({"error": "Task manager not initialized"}), 503

    data = request.get_json()
    reason = data.get('reason') if data else "Completed via API call"

    try:
        if hasattr(_task_manager_instance, 'mark_task_status_as_completed'): # Updated method name assumption
            success = _task_manager_instance.mark_task_status_as_completed(task_id, reason=reason)
            if success:
                updated_task = _task_manager_instance.get_task_by_id(task_id)
                return jsonify({"success": True, "message": "Task marked as complete.", "task": format_task_for_json(updated_task)}), 200
            else:
                # Check if task exists to differentiate not found from other failure
                task = _task_manager_instance.get_task_by_id(task_id)
                if not task:
                    return jsonify({"success": False, "error": "Task not found."}), 404
                return jsonify({"success": False, "error": "Failed to mark task as complete."}), 400
        else:
            logger.warning(f"--- DIAGNOSTIC: /api/tasks/{task_id}/complete - TaskManager missing mark_task_status_as_completed method. ---")
            return jsonify({"error": "Task completion method not available on manager."}), 500
    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/tasks/{task_id}/complete - Error: {e} ---", exc_info=True)
        return jsonify({"success": False, "error": "An internal server error occurred."}), 500

@app.route('/api/tasks/<string:task_id>/archive', methods=['POST'])
def archive_task_api(task_id):
    global _task_manager_instance
    logger.info(f"--- DIAGNOSTIC: Route /api/tasks/{task_id}/archive POST called ---")
    if _task_manager_instance is None:
        return jsonify({"error": "Task manager not initialized"}), 503

    data = request.get_json()
    reason = data.get('reason') if data else "Archived via API call"

    try:
        if hasattr(_task_manager_instance, 'archive_task_by_id'): # Updated method name assumption
            success = _task_manager_instance.archive_task_by_id(task_id, reason=reason)
            if success:
                # Optionally, try to fetch the task to confirm its archived status or just return success
                # For an archived task, get_task_by_id might still return it, or it might be in a different list.
                # Let's assume for now that success from archive_task_by_id is sufficient.
                return jsonify({"success": True, "message": "Task archived."}), 200
            else:
                task = _task_manager_instance.get_task_by_id(task_id) # Check if it exists
                if not task:
                     return jsonify({"success": False, "error": "Task not found."}), 404
                return jsonify({"success": False, "error": "Failed to archive task."}), 400
        else:
            logger.warning(f"--- DIAGNOSTIC: /api/tasks/{task_id}/archive - TaskManager missing archive_task_by_id method. ---")
            return jsonify({"error": "Task archival method not available on manager."}), 500
    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/tasks/{task_id}/archive - Error: {e} ---", exc_info=True)
        return jsonify({"success": False, "error": "An internal server error occurred."}), 500

@app.route('/api/project_output/<string:task_id>', methods=['GET'])
def get_project_output_api(task_id):
    global _task_manager_instance
    logger.info(f"--- DIAGNOSTIC: Route /api/project_output/{task_id} GET called ---")
    if _task_manager_instance is None:
        return jsonify({"error": "Task manager not initialized"}), 503

    try:
        # Task might be active or archived. TaskManager needs a way to get completed/archived tasks.
        # Assuming get_task can find it, or a new method like get_archived_task_by_id exists.
        # For now, let's try get_task and if not found, check an assumed archive retrieval.
        task = _task_manager_instance.get_task(task_id)
        if not task:
            # Try fetching from archive if TaskManager supports it
            if hasattr(_task_manager_instance, 'get_archived_task_by_id'):
                task = _task_manager_instance.get_archived_task_by_id(task_id) # Assumed method
            elif hasattr(_task_manager_instance, 'list_archived_tasks'): # Fallback to searching list
                archived_tasks = _task_manager_instance.list_archived_tasks(limit=500) # Adjust limit as needed
                task = next((t for t in archived_tasks if t.task_id == task_id), None)

        if not task:
            return jsonify({"success": False, "error": "Project task not found."}), 404

        if task.status != ActiveTaskStatus.COMPLETED_SUCCESSFULLY:
            return jsonify({"success": False, "error": f"Project task '{task.description[:50]}' is not yet completed successfully. Current status: {task.status.name}"}), 400

        details = task.details if task.details else {}
        html_content = details.get("final_output_html_content")
        output_path = details.get("final_output_path") # e.g., relative to a known project base dir

        if html_content:
            logger.info(f"--- DIAGNOSTIC: Serving direct HTML content for project task {task_id} ---")
            return jsonify({"success": True, "project_name": task.description, "html_content": html_content}), 200
        elif output_path:
            # IMPORTANT: Construct the full, safe path to the file.
            # This needs to be relative to a secure, known base directory for AI-generated projects.
            # For now, using a placeholder base_dir. This MUST be configured securely.
            # Assuming file_system_tools.BASE_PROJECTS_DIR is the correct base.
            from ai_assistant.custom_tools.file_system_tools import BASE_PROJECTS_DIR, read_text_from_file as fs_read_text

            # Ensure output_path is treated as relative to the project's specific directory
            # Example: if output_path is "index.html" and project was "my_game",
            # full_path should be something like ".../ai_generated_projects/my_game/index.html"
            # This requires knowing the project's sanitized name or specific folder.
            # For now, let's assume output_path might be relative to BASE_PROJECTS_DIR or a subfolder.
            # This path resolution needs to be robust.

            # A simple, potentially insecure way if output_path is not sanitized or relative:
            # full_file_path = os.path.join(BASE_PROJECTS_DIR, output_path)
            # A better way would be if 'output_path' is relative to the specific project's dir
            # and task.details contains 'project_sanitized_name' or similar.
            project_sanitized_name = details.get("project_sanitized_name", details.get("project_name_for_context"))
            if not project_sanitized_name: # Fallback if not explicitly stored
                 project_sanitized_name = sanitize_project_name(task.description.replace("Project: ", "")[:50])


            if project_sanitized_name:
                full_file_path = os.path.join(BASE_PROJECTS_DIR, project_sanitized_name, output_path)
            else: # Fallback if no project name context, treat output_path as relative to BASE_PROJECTS_DIR
                full_file_path = os.path.join(BASE_PROJECTS_DIR, output_path)

            logger.info(f"--- DIAGNOSTIC: Attempting to read project output from path {full_file_path} for task {task_id} ---")

            # Security: Ensure the path is within the allowed base directory
            if not os.path.abspath(full_file_path).startswith(os.path.abspath(BASE_PROJECTS_DIR)):
                logger.error(f"--- DIAGNOSTIC: Security alert! Attempt to access file outside project directory: {full_file_path} ---")
                return jsonify({"success": False, "error": "Access to file path is restricted."}), 403

            file_content = fs_read_text(full_file_path) # Use the one from file_system_tools
            if file_content.startswith("Error:"):
                logger.error(f"--- DIAGNOSTIC: Error reading file {full_file_path}: {file_content} ---")
                return jsonify({"success": False, "error": f"Could not read project output file: {output_path}. Detail: {file_content}"}), 500

            logger.info(f"--- DIAGNOSTIC: Successfully read content from {full_file_path} for task {task_id} ---")
            return jsonify({"success": True, "project_name": task.description, "html_content": file_content}), 200
        else:
            return jsonify({"success": False, "error": "No final output (HTML content or file path) found for this completed project task."}), 404

    except Exception as e:
        logger.error(f"--- DIAGNOSTIC: /api/project_output/{task_id} - Error: {e} ---", exc_info=True)
        return jsonify({"success": False, "error": "An internal server error occurred while fetching project output."}), 500

if __name__ == '__main__':
    # Note: For development only. In production, use a proper WSGI server like Gunicorn.
    # The default Flask dev server is single-threaded by default.
    # For async operations, especially if they are CPU-bound or involve external I/O
    # not handled by asyncio-native libraries, you might need an ASGI server
    # or run Flask with `threaded=True` for some concurrency.
    # However, since `process_prompt` is async and Flask supports async routes,
    # it should integrate with asyncio's event loop.
    app.run(host='0.0.0.0', debug=True, use_reloader=False)
