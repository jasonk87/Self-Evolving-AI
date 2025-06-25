import asyncio
import os
from typing import Optional # Added this import
from flask import Flask, render_template, request, jsonify

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

# AI Assistant Core Imports
from ai_assistant.core.orchestrator import DynamicOrchestrator
# Import the new centralized initialization function
from ai_assistant.core.startup_services import initialize_core_services
# TaskManager and NotificationManager might still be needed for type hints or direct use if any
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager

app = Flask(__name__)

# Global variables for AI services
orchestrator: Optional[DynamicOrchestrator] = None
# Keep references to task_manager and notification_manager if needed by other parts of app.py
# For now, they are primarily managed within initialize_core_services
_task_manager_instance: Optional[TaskManager] = None
_notification_manager_instance: Optional[NotificationManager] = None


def startup_event():
    """Initializes AI services. Designed to be run in an asyncio event loop."""
    global orchestrator, _task_manager_instance, _notification_manager_instance
    if orchestrator is not None:
        print("Flask App: AI services already initialized.")
        return

    print("Flask App: Initializing AI services via centralized function...")
    try:
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
        # Call the orchestrator's process_prompt method, now passing user_id
        success, response_message = await orchestrator.process_prompt(user_message, user_id=user_id)

        if success:
            return jsonify({"response": response_message})
        else:
            # Consider if we want to expose detailed errors or keep them generic
            return jsonify({"response": response_message, "error_detail": "Processing failed"}), 200 # Still 200, error is in payload
    except Exception as e:
        print(f"Error in /chat_api: {e}") # Log the error
        return jsonify({"error": "An internal error occurred processing your message."}), 500


if __name__ == '__main__':
    # Initialize AI services once before starting the app if running directly
    startup_event() # Call the startup_event function

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
    if _task_manager_instance is None:
        return jsonify({"error": "Task manager not initialized"}), 503

    active_tasks = _task_manager_instance.list_active_tasks()

    # Convert tasks to JSON-serializable format
    formatted_tasks = [format_task_for_json(task) for task in active_tasks]

    return jsonify(formatted_tasks)

@app.route('/api/status/notifications', methods=['GET'])
def get_recent_notifications():
    global _notification_manager_instance
    if _notification_manager_instance is None:
        return jsonify({"error": "Notification manager not initialized"}), 503

    # Fetch, for example, the 5 most recent unread notifications
    # Make sure NotificationStatus is imported or accessible if using Enum directly
    from ai_assistant.core.notification_manager import NotificationStatus # Import if not already

    try:
        # Assuming get_notifications can take status_filter as string or Enum
        # Adjust if your NotificationManager expects Enum type directly for status_filter
        notifications = _notification_manager_instance.get_notifications(
            status_filter=NotificationStatus.UNREAD, # Or "unread" if your method handles string
            limit=5
        )
    except Exception as e:
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
