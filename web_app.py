# Removed nest_asyncio, enforcing proper async loop bounds


import os
import sys

# Configure standard streams to avoid UnicodeEncodeError on Windows
if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr is not None and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import asyncio
import logging
import threading
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin

# Add the project root to sys.path
project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import Globals and Config
import app_globals
from ai_assistant import config
from ai_assistant.core.notification_manager import NotificationManager
from ai_assistant.core.config_manager import ConfigManager
from ai_assistant.core.chat_manager import ChatSessionManager
from ai_assistant.core.memory_manager import MemoryManager
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.startup_services import resume_interrupted_tasks
from ai_assistant.llm_interface.ollama_client import OllamaProvider
from ai_assistant.planning.hierarchical_planner import HierarchicalPlanner
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.planning.execution import ExecutionAgent
from ai_assistant.planning.planning import PlannerAgent
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.core.controller import SystemController
from ai_assistant.core.background_service import start_background_services_on_loop, set_orchestrator
from ai_assistant.core.shutdown_manager import shutdown_manager, register_signal_handlers

# Import Routes and socket events
from routes import api_bp, views_bp, chat, live
from socket_events import register_socket_events, watch_telemetry

# Import Telemetry Tracker
from ai_assistant.core.telemetry import telemetry_tracker

# Configure logging
from ai_assistant.core.logging_config import setup_logging_and_tracing
tracer = setup_logging_and_tracing()
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = config.SECRET_KEY

# Initialize SocketIO
# Allow all origins for development to fix 400/CORS issues when accessing via local IP
cors_origins = os.environ.get('CORS_ALLOWED_ORIGINS', '*').split(',')
if '*' in cors_origins:
    cors_origins = '*'
app_globals.socketio.init_app(app, cors_allowed_origins=cors_origins, async_mode='threading')

# --- Custom Log Handler for SocketIO ---
class SocketIOLogHandler(logging.Handler):
    def emit(self, record):
        try:
            # Filter out noisy logs
            if record.name in ['werkzeug', 'engineio.server', 'socketio.server', 'urllib3.connectionpool']:
                return

            _log_entry = self.format(record)
            
            # Send structured data for better UI handling
            app_globals.socketio.emit('log_event', {
                'message': record.getMessage(),
                'logger': record.name,
                'level': record.levelname,
                'timestamp': record.created
            })
        except Exception:
            self.handleError(record)

# Attach handler to root logger
socketio_handler = SocketIOLogHandler()
socketio_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(socketio_handler)

# Suppress noisy werkzeug logs for polling endpoints
def filter_polling_logs(record):
    if "GET /api/approvals" in record.getMessage() and " 200 " in record.getMessage():
        return False
    if "GET /api/memory/all" in record.getMessage() and " 200 " in record.getMessage():
        return False
    return True

logging.getLogger("werkzeug").addFilter(filter_polling_logs)

# Initialize Login Manager (Placeholder)
login_manager = LoginManager()
login_manager.init_app(app)

@app.route('/api/telemetry/tokens', methods=['GET'])
def get_token_usage():
    """Returns estimated token usage and cost."""
    return jsonify({
        "success": True,
        "usage": telemetry_tracker.get_usage()
    })

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    """Returns token telemetry in the legacy shape expected by the modal."""
    return jsonify(telemetry_tracker.get_usage())

class User(UserMixin):
    def __init__(self, id):
        self.id = id

@login_manager.user_loader
def load_user(user_id):
    return User(user_id)

# Initialize Global Managers
app_globals.config_manager = ConfigManager()
app_globals.chat_manager = ChatSessionManager(os.path.join(project_root, "_memory_", "chat_sessions"))
app_globals.memory_manager = MemoryManager()

# --- Async Initialization ---
async def init_orchestrator():
    # Instantiate NotificationManager
    app_globals.notification_manager = NotificationManager()
    
    # Instantiate TaskManager
    app_globals.task_manager = TaskManager(notification_manager=app_globals.notification_manager)

    # Resume interrupted tasks
    try:
        await resume_interrupted_tasks(app_globals.task_manager, app_globals.notification_manager)
    except Exception as e:
        logger.error(f"Failed to resume interrupted tasks: {e}")

    # Instantiate LLM Provider
    llm_provider = None
    try:
        logger.info(f"Initializing LLM Provider ({config.LLM_PROVIDER})...")
        llm_provider = OllamaProvider() 
    except Exception as e:
        logger.error(f"Failed to initialize OllamaProvider: {e}")

    hierarchical_planner = None
    if llm_provider:
        hierarchical_planner = HierarchicalPlanner(llm_provider=llm_provider)

    # Insights path
    insights_file_path = os.path.join(project_root, "ai_assistant", "core", "data", "actionable_insights.json")
    os.makedirs(os.path.dirname(insights_file_path), exist_ok=True)

    # Instantiate Agents
    learning_agent = LearningAgent(
        insights_filepath=insights_file_path,
        task_manager=app_globals.task_manager,
        notification_manager=app_globals.notification_manager,
        memory_manager=app_globals.memory_manager
    )

    action_executor = ActionExecutor(
        learning_agent=learning_agent,
        task_manager=app_globals.task_manager,
        notification_manager=app_globals.notification_manager
    )

    execution_agent = ExecutionAgent()
    planner_agent = PlannerAgent()

    app_globals.orchestrator = DynamicOrchestrator(
        planner=planner_agent,
        executor=execution_agent,
        learning_agent=learning_agent,
        action_executor=action_executor,
        task_manager=app_globals.task_manager,
        notification_manager=app_globals.notification_manager,
        hierarchical_planner=hierarchical_planner,
        memory_manager=app_globals.memory_manager
    )

    app_globals.controller = SystemController(app_globals.orchestrator)
    logger.info("SystemController initialized successfully.")

    # Connect orchestrator to background service
    set_orchestrator(app_globals.orchestrator)

# --- Dedicated AI Event Loop ---
app_globals.ai_loop = asyncio.new_event_loop()

def _run_ai_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

# Start the dedicated loop thread immediately
_ai_thread = threading.Thread(target=_run_ai_loop, args=(app_globals.ai_loop,), daemon=True)
_ai_thread.start()

def run_init():
    # Submit initialization to the dedicated AI loop
    future = asyncio.run_coroutine_threadsafe(init_orchestrator(), app_globals.ai_loop)
    future.result()  # Block until initialization is complete

# Register Blueprints
app.register_blueprint(views_bp)
app.register_blueprint(api_bp)
app.register_blueprint(chat.chat_bp)
app.register_blueprint(live.live_bp)

# Register Socket Events
register_socket_events(app_globals.socketio)

if __name__ == '__main__':
    # Initialize Core Systems
    run_init()
    
    # Register Signal Handlers
    register_signal_handlers()
    
    # Register Live Mode cleanup (optional dependency safe)
    try:
        import ai_live_link
    except Exception:
        ai_live_link = None

    if ai_live_link is not None:
        def cleanup_live_mode():
            logger.info("Shutdown: Stopping Live Mode...")
            ai_live_link.stop_live_mode()

        shutdown_manager.register_handler(cleanup_live_mode)

    # Start Background Threads
    t1 = threading.Thread(target=watch_telemetry, args=(shutdown_manager,), daemon=True)
    t1.start()
    
    # Run the background services on the dedicated AI loop
    start_background_services_on_loop(app_globals.ai_loop)

    port = int(os.environ.get('PORT', 5000))
    # use_reloader=False is important when using threads to avoid spawning twice
    app_globals.socketio.run(app, debug=True, use_reloader=False, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
