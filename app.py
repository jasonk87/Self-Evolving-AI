import asyncio
import os
from typing import Optional # Added this import
from flask import Flask, render_template, request, jsonify
import logging # Import logging

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

# Global variables for AI services
orchestrator: Optional[DynamicOrchestrator] = None
# Keep references to task_manager and notification_manager if needed by other parts of app.py
# For now, they are primarily managed within initialize_core_services
_task_manager_instance: Optional[TaskManager] = None
_notification_manager_instance: Optional[NotificationManager] = None

def startup_event():
    """Initializes AI services. Designed to be run in an asyncio event loop."""
    global orchestrator, _task_manager_instance, _notification_manager_instance
    logger.info("--- DIAGNOSTIC: startup_event() called ---")
    if orchestrator is not None:
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
        # We don't need to pass existing managers as app.py creates its own scope
        orch, tm, nm = loop.run_until_complete(initialize_core_services())
        loop.close()

        orchestrator = orch
        _task_manager_instance = tm
        _notification_manager_instance = nm
        print("Flask App: AI services initialized successfully.")

    except Exception as e:
        print(f"Flask App: CRITICAL ERROR during AI services initialization: {e}")
        orchestrator = None # Ensure it's None if initialization fails

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


if __name__ == '__main__':
    # Note: For development only. In production, use a proper WSGI server like Gunicorn.
    # The default Flask dev server is single-threaded by default.
    # For async operations, especially if they are CPU-bound or involve external I/O
    # not handled by asyncio-native libraries, you might need an ASGI server
    # or run Flask with `threaded=True` for some concurrency.
    # However, since `process_prompt` is async and Flask supports async routes,
    # it should integrate with asyncio's event loop.
    app.run(host='0.0.0.0', debug=True, use_reloader=False)

# --- New API Endpoint for Status Panel Data ---
from dataclasses import asdict
from enum import Enum
from datetime import datetime # Added import

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
        if isinstance(value, Enum):
            task_dict[key] = value.name
        elif isinstance(value, datetime): # Ensure datetime is imported if used here
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

    active_tasks = _task_manager_instance.list_active_tasks()
    logger.info(f"--- DIAGNOSTIC: /api/status/active_tasks - Found {len(active_tasks)} active tasks. ---")

    # Convert tasks to JSON-serializable format
    formatted_tasks = [format_task_for_json(task) for task in active_tasks]

    return jsonify(formatted_tasks)

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

    # The format_task_for_json helper should be generic enough if Notification objects
    # have similar attribute access (like .name for enums, .isoformat() for datetime)
    # or can be converted to dicts. Otherwise, a specific format_notification_for_json is needed.
    # For now, let's assume format_task_for_json can be adapted or Notification objects are dict-like.

    formatted_notifications = []
    for notif in notifications:
        # Create a dictionary for each notification manually to ensure correct fields
        notif_dict = {
            "notification_id": notif.notification_id,
            "event_type": notif.event_type.name if isinstance(notif.event_type, Enum) else str(notif.event_type),
            "summary_message": notif.summary_message,
            "timestamp": notif.timestamp.isoformat() if isinstance(notif.timestamp, datetime) else str(notif.timestamp),
            "status": notif.status.name if isinstance(notif.status, Enum) else str(notif.status),
            "related_item_id": notif.related_item_id,
            "related_item_type": notif.related_item_type
            # Add other relevant fields from Notification object if needed
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
        # Construct a specific prompt for the AI to analyze the HTML
        # Making this a bit more specific about what kind of analysis is expected.
        analysis_prompt = (
            f"The user has requested an analysis of the following HTML content currently displayed in their project area. "
            f"Please review this HTML code and provide a brief, user-friendly summary or analysis of its structure, "
            f"purpose, or any notable features. If it contains scripts, briefly describe what they might do. "
            f"Avoid simply repeating the code. Focus on insights.\n\n"
            f"HTML Content to Analyze:\n```html\n{html_content[:3000]}\n```"
            f"{'... (HTML content truncated)' if len(html_content) > 3000 else ''}"
        )

        # Use the orchestrator to process this prompt.
        # The orchestrator will handle planning (if any) or direct LLM interaction.
        # The response from process_prompt is (success_bool, data_dict)
        # where data_dict = {"chat_response": Optional[str], "project_area_html": Optional[str]}
        # For analysis, we primarily care about the chat_response.

        success, response_data = await orchestrator.process_prompt(analysis_prompt, user_id="system_display_analyzer") # Using a system user_id

        ai_analysis_text = response_data.get("chat_response")

        if success and ai_analysis_text:
            return jsonify({"success": True, "analysis_text": ai_analysis_text}), 200
        elif ai_analysis_text: # Success might be false if plan failed, but we still got some text
             return jsonify({"success": False, "analysis_text": ai_analysis_text, "error": "AI processed the request but indicated an issue."}), 200
        else:
            # This case means orchestrator.process_prompt returned success=False AND no chat_response,
            # or success=True but no chat_response (which would be odd for an analysis prompt).
            error_message = response_data.get("error", "AI analysis failed or returned no text.")
            return jsonify({"success": False, "error": error_message}), 500

    except Exception as e:
        print(f"Error in /api/analyze_display: {e}") # Log the error
        # Ensure consistent error structure for critical failures
        return jsonify({"success": False, "error": "An internal server error occurred during content analysis."}), 500
