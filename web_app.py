import os
import sys
import asyncio
import logging
import threading
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import time
import json

# Add the project root to sys.path
project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import AI Assistant components
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
from ai_assistant.config import get_projects_dir

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

def telemetry_watcher():
    """
    Watches for changes in telemetry.json files in any subfolder of data/projects/.
    Emits project_update event when changes are detected.
    """
    projects_dir = get_projects_dir()
    file_states = {}  # Map filepath -> last_modified_time

    while True:
        try:
            # Walk through the projects directory
            if os.path.exists(projects_dir):
                for root, dirs, files in os.walk(projects_dir):
                    if 'telemetry.json' in files:
                        filepath = os.path.join(root, 'telemetry.json')
                        try:
                            mtime = os.path.getmtime(filepath)

                            # Check if file is new or modified
                            if filepath not in file_states or file_states[filepath] != mtime:
                                file_states[filepath] = mtime

                                # Read content
                                with open(filepath, 'r', encoding='utf-8') as f:
                                    content = json.load(f)

                                # Determine project name from path
                                # Assuming structure: data/projects/{project_name}/telemetry.json
                                # root is .../data/projects/{project_name}
                                project_name = os.path.basename(root)

                                # Emit event
                                socketio.emit('project_update', {
                                    'project': project_name,
                                    'telemetry': content
                                })
                                logger.info(f"Telemetry updated for project: {project_name}")

                        except Exception as e:
                            logger.error(f"Error reading telemetry file {filepath}: {e}")

            socketio.sleep(1) # Use socketio.sleep for compatibility with greenlets if used
        except Exception as e:
            logger.error(f"Error in telemetry watcher: {e}")
            socketio.sleep(5)

if __name__ == '__main__':
    socketio.start_background_task(telemetry_watcher)
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
