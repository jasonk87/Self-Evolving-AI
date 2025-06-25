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
