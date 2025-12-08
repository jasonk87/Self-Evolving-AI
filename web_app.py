import os
import sys
import asyncio
import logging
import threading
from flask import Flask, render_template, request, jsonify

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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

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

if __name__ == '__main__':
    app.run(debug=True, port=5000)
