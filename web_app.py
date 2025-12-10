from typing import Dict, Any
# import eventlet
# eventlet.monkey_patch()

import os
import sys
import json
import asyncio
import logging
import threading
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import subprocess

# Add the project root to sys.path
project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import AI Assistant components
from ai_assistant.config import get_projects_dir, LLM_PROVIDER
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
from ai_assistant.core.events import EventEmitter
from ai_assistant.core.memory_manager import MemoryManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Initialize SocketIO
# Initialize SocketIO with threading mode to avoid eventlet/asyncio conflicts
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global Orchestrator instance
orchestrator = None
# Global Conversation History
conversation_history = []
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
        if LLM_PROVIDER == "gemini":
            logger.info("Initializing LLM Provider (Gemini via wrapper)...")
            # We still use OllamaProvider class as it wraps the Gemini client when configured
            llm_provider = OllamaProvider() 
        else:
             logger.info("Initializing LLM Provider (Ollama)...")
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

# Initialize Memory Manager
memory_manager = MemoryManager()

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
    global orchestrator, conversation_history
    if not orchestrator:
        return jsonify({"error": "Orchestrator not initialized"}), 500

    data = request.json
    message = data.get('message')
    context = data.get('context', {})

    if not message:
        return jsonify({"error": "No message provided"}), 400

    # Inject Context into Message
    # This is a simple way to make the AI aware without changing the Orchestrator signature yet.
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
                 from ai_assistant.core.project_manager import find_project
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

    # Add user message to history (Display original message to user in UI, but send full context to AI? 
    # Actually, history usually tracks what was said. If we hide context, AI might reference it and confuse user if they didn't see it.
    # But listing 1000 lines of code in history is bad.
    # The 'content' field in history is what the AI sees. 
    # The UI should probably display the 'message' separate from the 'context'.
    # Here we are appending to `conversation_history` which is used by `orchestrator`.
    # So we MUST append the full message here for the AI to see it.
    conversation_history.append({"role": "user", "content": full_message})

    try:
        # Flask 2.0+ supports async views.
        success, response = await orchestrator.process_prompt(full_message, conversation_history=conversation_history)
        
        # Add assistant response to history
        conversation_history.append({"role": "assistant", "content": response})

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

@app.route('/api/run', methods=['POST'])
def run_script():
    """Executes a Python script."""
    data = request.json
    path = data.get('path')

    if not path or not path.startswith('projects/'):
        return jsonify({"error": "Invalid path format. Must start with 'projects/'", "success": False}), 400

    # Parse project name and relative path
    # Expected format: "projects/<project_name>/<relative_path>"
    try:
        parts = path.split('/', 2)
        if len(parts) < 3:
             return jsonify({"error": "Invalid path format. Missing project name or file path.", "success": False}), 400
        
        project_name = parts[1]
        file_relative_path = parts[2]
    except Exception as e:
        return jsonify({"error": f"Failed to parse path: {e}", "success": False}), 400

    try:
        # Use find_project to get the true root path
        from ai_assistant.core.project_manager import find_project
        project = find_project(project_name)

        if not project:
            return jsonify({"error": f"Project '{project_name}' not found.", "success": False}), 404
        
        root_path = project.get('root_path')
        if not root_path or not os.path.exists(root_path):
            return jsonify({"error": f"Project root path invalid for '{project_name}'.", "success": False}), 500

        # Construct full path
        full_path = os.path.abspath(os.path.join(root_path, file_relative_path))

        # Security check: ensure path is within root_path
        if not full_path.startswith(os.path.abspath(root_path)):
             return jsonify({"error": "Access denied: Path traversal detected.", "success": False}), 403

        if not os.path.exists(full_path):
            return jsonify({"error": f"File not found: {full_path}", "success": False}), 404

        # determine cwd (script's directory)
        cwd = os.path.dirname(full_path)

        # Execute
        result = subprocess.run(
            [sys.executable, full_path],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr

        if not output:
             output = "Script finished with no output."

        return jsonify({"output": output, "success": True})

    except subprocess.TimeoutExpired:
        return jsonify({"output": "Error: Execution timed out (limit: 10s)", "success": False}), 200
    except Exception as e:
        logger.error(f"Error executing script {path}: {e}")
        return jsonify({"output": f"Error: {str(e)}", "success": False}), 500

# --- Memory Management Endpoints ---

@app.route('/api/memory/facts', methods=['GET'])
def get_facts():
    """Returns a list of all learned facts."""
    try:
        facts = memory_manager.get_all_facts()
        return jsonify({"facts": facts, "success": True})
    except Exception as e:
        logger.error(f"Error fetching facts: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/memory/facts', methods=['POST'])
def add_fact():
    """Adds a new fact."""
    data = request.json
    text = data.get('text')

    if not text:
        return jsonify({"error": "Fact text is required", "success": False}), 400

    try:
        new_fact = memory_manager.add_fact(text)
        return jsonify({"fact": new_fact, "success": True})
    except Exception as e:
        logger.error(f"Error adding fact: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/memory/facts/<fact_id>', methods=['PUT'])
def update_fact(fact_id):
    """Updates an existing fact."""
    data = request.json
    text = data.get('text')

    if not text:
        return jsonify({"error": "Fact text is required", "success": False}), 400

    try:
        updated_fact = memory_manager.update_fact(fact_id, text)
        if updated_fact:
            return jsonify({"fact": updated_fact, "success": True})
        else:
            return jsonify({"error": "Fact not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error updating fact {fact_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/memory/facts/<fact_id>', methods=['DELETE'])
def delete_fact(fact_id):
    """Deletes a fact."""
    try:
        success = memory_manager.delete_fact(fact_id)
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Fact not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error deleting fact {fact_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/memory/insights', methods=['GET'])
def get_insights():
    """Returns a list of all actionable insights."""
    try:
        insights = memory_manager.get_all_insights()
        return jsonify({"insights": insights, "success": True})
    except Exception as e:
        logger.error(f"Error fetching insights: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/memory/insights/<insight_id>', methods=['PUT'])
def update_insight_status(insight_id):
    """Updates the status of an insight."""
    data = request.json
    status = data.get('status')

    if not status:
        return jsonify({"error": "Status is required", "success": False}), 400

    try:
        updated_insight = memory_manager.update_insight_status(insight_id, status)
        if updated_insight:
            return jsonify({"insight": updated_insight, "success": True})
        else:
            return jsonify({"error": "Insight not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error updating insight {insight_id}: {e}")
        return jsonify({"error": str(e), "success": False}), 500

@app.route('/api/memory/insights/<insight_id>', methods=['DELETE'])
def delete_insight(insight_id):
    """Deletes an insight."""
    try:
        success = memory_manager.delete_insight(insight_id)
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Insight not found", "success": False}), 404
    except Exception as e:
        logger.error(f"Error deleting insight {insight_id}: {e}")
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

def bridge_system_events(event_name: str, data: Dict[str, Any]):
    """Bridges internal system events to SocketIO."""
    socketio.emit(event_name, data)

# Register the bridge
EventEmitter.register_listener(bridge_system_events)

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

                                        # Proactive Hearing: Notify ActionExecutor
                                        if orchestrator and orchestrator.action_executor:
                                            # We need to run this async method. Since we are in a thread (watch_telemetry is threaded via start_background_task),
                                            # and handle_telemetry_update is async, we need a way to run it.
                                            # In Flask-SocketIO eventlet mode, greenlets are used.
                                            # However, handle_telemetry_update uses await.
                                            # We can't easily await here.
                                            # We can spawn a greenlet? Or run sync if we can?
                                            # Orchestrator uses async/await everywhere.
                                            # Let's try to run it in a new event loop or use socketio.start_background_task with a wrapper.
                                            # Actually, since we are already in a background task, maybe we can just call a wrapper that runs the async function.

                                            def _run_proactive_check():
                                                # Use asyncio.run() to execute the async task in a fresh event loop.
                                                # This is safe because OllamaProvider creates a new aiohttp.ClientSession for each request,
                                                # so it is not bound to a specific event loop from initialization.
                                                try:
                                                    response = asyncio.run(
                                                        orchestrator.action_executor.handle_telemetry_update(project_name, data)
                                                    )
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

if __name__ == '__main__':
    socketio.start_background_task(watch_telemetry)
    logger.info("Starting Web App on port 5000...")
    socketio.run(app, debug=True, use_reloader=False, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
