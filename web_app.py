import eventlet
eventlet.monkey_patch()

import os
import sys
import json
import asyncio
import logging
import threading
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

# Add the project root to sys.path
project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import AI Assistant components
from ai_assistant.config import get_projects_dir
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.planning.execution import ExecutionAgent
from ai_assistant.planning.planning import PlannerAgent
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.llm_interface.ollama_client import OllamaProvider
from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner
from ai_assistant.core.startup_services import resume_interrupted_tasks
from ai_assistant.core.project_manager import list_projects
from ai_assistant.custom_tools.file_system_tools import list_project_files, get_project_file_content, save_project_file_content

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

# Global Orchestrator instance
orchestrator = None
# We need a dedicated event loop for the agents if they rely on one.
# For simplicity in this skeleton, we'll use `asyncio.run` for the single calls or create a loop.
# However, Flask is WSGI (sync). `orchestrator.process_prompt` is async.
# We will use `asyncio.run()` inside the route for now, but better to use an async loop manager if we want background tasks.
# Given the orchestrator might use `aiohttp` (via OllamaProvider), it expects to be in an async context.
# We will just run the process_prompt in a new loop for each request or re-use a loop if possible.
# Since `ai_assistant` seems designed with `asyncio`, we should probably use `quart` or just manage the loop.
# For this task, "Flask Web Interface Skeleton", standard Flask with `async` route handlers (Flask 2.0+) is sufficient.
# Flask 2.0+ will handle the loop for async routes.

async def init_orchestrator():
    global orchestrator

    # Instantiate NotificationManager
    notification_manager = NotificationManager()

    # Instantiate TaskManager
    task_manager = TaskManager(notification_manager=notification_manager)

    # Resume interrupted tasks
    try:
        await resume_interrupted_tasks(task_manager, notification_manager)
    except Exception as e:
        logger.error(f"Failed to resume interrupted tasks: {e}")

    # Instantiate LLM Provider and Hierarchical Planner
    llm_provider = None
    try:
        llm_provider = OllamaProvider()
    except Exception as e:
        logger.error(f"Failed to initialize OllamaProvider: {e}")

    hierarchical_planner = None
    if llm_provider:
        hierarchical_planner = HierarchicalPlanner(llm_provider=llm_provider)

    # Insights path
    insights_file_path = os.path.join(os.path.expanduser("~"), ".ai_assistant", "actionable_insights.json")
    os.makedirs(os.path.dirname(insights_file_path), exist_ok=True)

    # Instantiate Agents
    learning_agent = LearningAgent(
        insights_filepath=insights_file_path,
        task_manager=task_manager,
        notification_manager=notification_manager
    )

    action_executor = ActionExecutor(
        learning_agent=learning_agent,
        task_manager=task_manager,
        notification_manager=notification_manager
    )

    execution_agent = ExecutionAgent()
    planner_agent = PlannerAgent()

    orchestrator = DynamicOrchestrator(
        planner=planner_agent,
        executor=execution_agent,
        learning_agent=learning_agent,
        action_executor=action_executor,
        task_manager=task_manager,
        notification_manager=notification_manager,
        hierarchical_planner=hierarchical_planner
    )
    logger.info("Orchestrator initialized successfully.")

# Run initialization.
# Since we are at module level, we can't easily await.
# We'll run it in a thread or just run_until_complete if we are sure no other loop is running.
def run_init():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_orchestrator())
    # We don't close the loop here because objects created might be bound to it?
    # Actually, `OllamaProvider` uses `aiohttp.ClientSession` which is context manager based in `invoke_ollama_model_async_internal`.
    # It creates a new session every time. So it should be fine.

run_init()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
async def chat():
    global orchestrator
    if not orchestrator:
        return jsonify({"error": "Orchestrator not initialized"}), 500

    data = request.json
    message = data.get('message')

    if not message:
        return jsonify({"error": "No message provided"}), 400

    try:
        # Flask 2.0+ supports async views.
        success, response = await orchestrator.process_prompt(message)
        return jsonify({
            "response": response,
            "success": success
        })
    except Exception as e:
        logger.error(f"Error processing prompt: {e}")
        # Return a JSON error but with 200 OK so the frontend handles it gracefully if needed,
        # or 500 if it's a server crash.
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/projects', methods=['GET'])
def get_projects():
    """Returns a list of all projects."""
    try:
        projects = list_projects()
        return jsonify({"projects": projects, "success": True})
    except Exception as e:
        logger.error(f"Error listing projects: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/files/list', methods=['GET'])
def list_files():
    """Lists files for a given project and subdirectory."""
    project_name = request.args.get('project_name')
    path = request.args.get('path', '')

    if not project_name:
        return jsonify({"error": "Project name is required", "success": False}), 400

    try:
        result = list_project_files(project_name, path)
        if result['status'] == 'error':
            return jsonify({"error": result['message'], "success": False}), 400
        return jsonify({"files": result['files'], "directories": result['directories'], "path": result['path_listed'], "success": True})
    except Exception as e:
        logger.error(f"Error listing files for project {project_name}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/files/read', methods=['GET'])
def read_file():
    """Reads the content of a file."""
    project_name = request.args.get('project_name')
    path = request.args.get('path')

    if not project_name or not path:
        return jsonify({"error": "Project name and path are required", "success": False}), 400

    try:
        result = get_project_file_content(project_name, path)
        if result['status'] == 'error':
            return jsonify({"error": result['message'], "success": False}), 400
        return jsonify({"content": result['content'], "file_path": result['file_path'], "success": True})
    except Exception as e:
        logger.error(f"Error reading file {path} for project {project_name}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/files/save', methods=['POST'])
def save_file():
    """Saves content to a file."""
    data = request.json
    project_name = data.get('project_name')
    path = data.get('path')
    content = data.get('content')

    if not project_name or not path or content is None:
        return jsonify({"error": "Project name, path, and content are required", "success": False}), 400

    try:
        result = save_project_file_content(project_name, path, content)
        if result['status'] == 'error':
            return jsonify({"error": result['message'], "success": False}), 400
        return jsonify({"file_path": result['file_path'], "success": True})
    except Exception as e:
        logger.error(f"Error saving file {path} for project {project_name}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

# SocketIO Event Handlers
@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    logger.info('Client disconnected')

def handle_log_event(data):
    """
    Broadcasts log messages to connected clients.
    Expected data format: {'message': 'Log content here', 'level': 'INFO'}
    """
    socketio.emit('log_event', data)

def watch_telemetry():
    """Background task to watch for telemetry updates."""
    projects_dir = get_projects_dir()
    last_modified_times = {}
    logger.info(f"Starting telemetry watcher on {projects_dir}")

    while True:
        try:
            if os.path.exists(projects_dir):
                # Iterate over subdirectories in projects_dir
                for project_name in os.listdir(projects_dir):
                    project_path = os.path.join(projects_dir, project_name)
                    if os.path.isdir(project_path):
                        telemetry_path = os.path.join(project_path, "telemetry.json")
                        if os.path.exists(telemetry_path):
                            mtime = os.path.getmtime(telemetry_path)

                            # Check if file is modified
                            if telemetry_path not in last_modified_times or last_modified_times[telemetry_path] < mtime:
                                last_modified_times[telemetry_path] = mtime
                                try:
                                    with open(telemetry_path, 'r', encoding='utf-8') as f:
                                        data = json.load(f)
                                        # Add project name to data if not present, or wrap it
                                        payload = {
                                            "project": project_name,
                                            "data": data
                                        }
                                        socketio.emit('project_update', payload)
                                        logger.info(f"Emitted project_update for {project_name}")
                                except Exception as e:
                                    logger.error(f"Error reading telemetry for {project_name}: {e}")

        except Exception as e:
            logger.error(f"Error in watch_telemetry: {e}")

        socketio.sleep(1)

if __name__ == '__main__':
    socketio.start_background_task(watch_telemetry)
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
