import asyncio
import os
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
from ai_assistant.planning.planning import PlannerAgent
from ai_assistant.planning.execution import ExecutionAgent
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager
from ai_assistant.llm_interface.ollama_client import OllamaProvider
from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner
from ai_assistant.core.startup_services import resume_interrupted_tasks # For task resumption
from ai_assistant.config import get_data_dir # For learning agent insights path

app = Flask(__name__)

# Global variable for the orchestrator
orchestrator: Optional[DynamicOrchestrator] = None

def initialize_ai_services():
    global orchestrator
    if orchestrator is not None:
        return

    print("Initializing AI services for Flask app...")
    try:
        notification_manager = NotificationManager()
        task_manager = TaskManager(notification_manager=notification_manager)

        # It's important to run resume_interrupted_tasks in an event loop
        # For Flask, this initialization happens outside an async route, so we manage a loop.
        try:
            asyncio.run(resume_interrupted_tasks(task_manager, notification_manager))
            print("Resumed interrupted tasks.")
        except Exception as e_resume:
            print(f"Error resuming interrupted tasks: {e_resume}")


        llm_provider = OllamaProvider() # Assuming Ollama is running
        print("LLM Provider initialized.")

        hierarchical_planner = HierarchicalPlanner(llm_provider=llm_provider)
        print("Hierarchical Planner initialized.")

        # insights_file_path = os.path.join(get_data_dir(), "actionable_insights.json")
        # The path for LearningAgent in cli.py is:
        insights_file_path_actual = os.path.join(os.path.expanduser("~"), ".ai_assistant", "actionable_insights.json")
        os.makedirs(os.path.dirname(insights_file_path_actual), exist_ok=True)


        learning_agent = LearningAgent(
            insights_filepath=insights_file_path_actual,
            task_manager=task_manager,
            notification_manager=notification_manager
        )
        print("Learning Agent initialized.")

        action_executor = ActionExecutor(
            learning_agent=learning_agent,
            task_manager=task_manager,
            notification_manager=notification_manager
        )
        print("Action Executor initialized.")

        planner_agent = PlannerAgent()
        execution_agent = ExecutionAgent()
        print("Planner and Execution Agents initialized.")

        orchestrator = DynamicOrchestrator(
            planner=planner_agent,
            executor=execution_agent,
            learning_agent=learning_agent,
            action_executor=action_executor,
            task_manager=task_manager,
            notification_manager=notification_manager,
            hierarchical_planner=hierarchical_planner
        )
        print("Dynamic Orchestrator initialized successfully.")
    except Exception as e:
        print(f"CRITICAL ERROR during AI services initialization: {e}")
        orchestrator = None # Ensure it's None if initialization fails

@app.before_first_request
def startup():
    # Ensure AI services are initialized before the first request
    # This is a Flask specific decorator.
    # For production, you might initialize earlier or differently.
    initialize_ai_services()


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/chat_api', methods=['POST'])
async def chat_api():
    global orchestrator
    if orchestrator is None:
        return jsonify({"error": "AI services are not initialized. Please check server logs."}), 500

    data = await request.get_json()
    user_message = data.get('message')

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    try:
        # Call the orchestrator's process_prompt method
        # This is an async method, so we await it.
        # The Flask route itself must be async for this to work directly.
        success, response_message = await orchestrator.process_prompt(user_message)

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
    # initialize_ai_services() # Called by @app.before_first_request now

    # Note: For development only. In production, use a proper WSGI server like Gunicorn.
    # The default Flask dev server is single-threaded by default.
    # For async operations, especially if they are CPU-bound or involve external I/O
    # not handled by asyncio-native libraries, you might need an ASGI server
    # or run Flask with `threaded=True` for some concurrency.
    # However, since `process_prompt` is async and Flask supports async routes,
    # it should integrate with asyncio's event loop.
    app.run(debug=True)
