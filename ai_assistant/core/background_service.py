### START FILE: core/background_service.py ###
# ai_assistant/core/background_service.py
import asyncio
import time
import json
import os # Added
import re
import logging
from typing import Optional, List

from ai_assistant.core.autonomous_reflection import run_self_reflection_cycle
from ai_assistant.core.reflection import global_reflection_log # Import global log for timestamp check
from ai_assistant.tools import tool_system # To get available tools
# Modified: Import the specific curation function and config for interval
from ai_assistant.custom_tools.knowledge_tools import run_periodic_fact_store_curation_async
from ai_assistant.memory.persistent_memory import LEARNED_FACTS_FILEPATH # Import constant for dirty check
from ai_assistant.config import is_debug_mode, FACT_CURATION_INTERVAL_SECONDS
# Added for self-healing
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager, NotificationType

# Added for Evolutionary Architect
from ai_assistant.learning.evolutionary_architect import perform_architectural_audit
from ai_assistant.config import get_data_dir

# Configure logger for this module
logger = logging.getLogger(__name__)

# Import for project execution task
try:
    from ai_assistant.custom_tools.file_system_tools import BASE_PROJECTS_DIR
    from ai_assistant.custom_tools.project_execution_tools import execute_project_coding_plan
    PROJECT_TOOLS_AVAILABLE = True
except ImportError as e: # pragma: no cover
    print(f"BackgroundService: Warning - Could not import project execution tools. Autonomous project work will be disabled. Error: {e}")
    PROJECT_TOOLS_AVAILABLE = False
    # Define placeholders if imports fail, so the rest of the module doesn't break
    BASE_PROJECTS_DIR = "ai_generated_projects" 
    def read_text_from_file(filepath: str) -> str: return f"Error: Tool not available due to import failure for {filepath}"
    def sanitize_project_name(name: str) -> str: return name
    async def execute_project_coding_plan(project_name: str, base_projects_dir_override: Optional[str] = None) -> str:
        return "Error: execute_project_coding_plan tool not available due to import failure."


# Fallback for config if not defined
try:
    from ai_assistant.config import PROJECT_EXECUTION_INTERVAL_SECONDS
except ImportError: # pragma: no cover
    PROJECT_EXECUTION_INTERVAL_SECONDS = 720 # Default to 12 minutes if not in config

# --- Service State ---
_background_service_active = False
_background_task: Optional[asyncio.Task] = None
_polling_interval_seconds = 300  # For self-reflection # FACT_CURATION_INTERVAL_SECONDS will be used from config
_last_fact_curation_time: float = 0.0
_last_project_execution_scan_time: float = 0.0 # New state for project execution
_last_self_healing_time: float = 0.0 # State for self-healing
_self_healing_interval_seconds = 600 # Check every 10 minutes
_last_architect_audit_timestamp: float = 0.0
_architect_audit_interval_seconds = 43200 # 12 hours
ARCHITECT_STATE_FILE = "architect_state.json"

def sanitize_project_name(name: str) -> str:
    """
    Sanitizes a project name to create a safe directory name.
    - Converts to lowercase.
    - Replaces spaces and multiple hyphens with a single underscore.
    - Removes characters that are not alphanumeric, underscores, or hyphens.
    - Ensures it's not empty (defaults to "unnamed_project").
    - Limits length to a maximum of 50 characters.
    Args:
        name: The raw project name string.
    Returns:
        A sanitized string suitable for use as a directory name.
    """
    if not name or not name.strip():
        return "unnamed_project"

    s_name = name.lower()
    s_name = re.sub(r'\s+', '_', s_name)
    s_name = re.sub(r'-+', '_', s_name)
    s_name = re.sub(r'[^\w-]', '', s_name)
    s_name = re.sub(r'_+', '_', s_name)

    if not s_name:
        return "unnamed_project"
    
    return s_name[:50]

def write_text_to_file(filepath: str, content: str) -> str:
    """
    Writes the given text content to the specified file.
    Ensures the directory for the file exists before writing.

    Args:
        filepath: The absolute or relative path to the file.
        content: The string content to write to the file.

    Returns:
        A string indicating success or an error message.
    """
    if not filepath or not isinstance(filepath, str):
        return "Error: Filepath must be a non-empty string."
    # ... (ensure all internal uses of 'full_filepath' are changed to 'filepath')
    try:
        dir_path = os.path.dirname(filepath)
        if dir_path: 
            os.makedirs(dir_path, exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Success: Content written to '{filepath}'."
    except IOError as e:
        return f"Error writing to file '{filepath}': {e} (IOError)"
    # ... (and so on for other error messages) ...

def read_text_from_file(filepath: str) -> str:
    """
    Reads and returns the text content from the specified file.

    Args:
        filepath: The absolute or relative path to the file.

    Returns:
        The content of the file as a string, or an error message string if reading fails.
    """
    if not filepath or not isinstance(filepath, str):
        return "Error: Filepath must be a non-empty string."

    if not os.path.exists(filepath):
         return f"Error: File '{filepath}' not found."
    
    if not os.path.isfile(filepath):
        return f"Error: Path '{filepath}' is not a file."

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except IOError as e:
        return f"Error reading file '{filepath}': {e} (IOError)"

# --- Asyncio Version ---
def _load_architect_state():
    global _last_architect_audit_timestamp
    state_file = os.path.join(get_data_dir(), ARCHITECT_STATE_FILE)
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                data = json.load(f)
                _last_architect_audit_timestamp = data.get("last_audit_timestamp", 0.0)
                logger.info(f"BackgroundService: Loaded architect state. Last audit: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_last_architect_audit_timestamp))}")
        except Exception as e:
            logger.error(f"BackgroundService: Failed to load architect state: {e}")
            _last_architect_audit_timestamp = 0.0
    else:
        _last_architect_audit_timestamp = 0.0

def _save_architect_state():
    state_file = os.path.join(get_data_dir(), ARCHITECT_STATE_FILE)
    try:
        with open(state_file, 'w') as f:
            json.dump({"last_audit_timestamp": _last_architect_audit_timestamp}, f)
    except Exception as e:
        logger.error(f"BackgroundService: Failed to save architect state: {e}")

async def _background_loop_async():
    global _last_fact_curation_time, _last_project_execution_scan_time, _last_self_healing_time, _last_architect_audit_timestamp
    print("BackgroundService: Async loop started.")
    _last_fact_curation_time = time.time()
    _last_project_execution_scan_time = time.time()
    _last_self_healing_time = time.time()
    
    _load_architect_state()

    # Track limits to avoid processing
    _last_reflection_analyzed_timestamp = global_reflection_log.get_last_entry_timestamp()
    
    # Track modification time of learned facts file to avoid redundant curation
    _last_facts_file_mtime = 0.0
    if os.path.exists(LEARNED_FACTS_FILEPATH):
        _last_facts_file_mtime = os.path.getmtime(LEARNED_FACTS_FILEPATH)
    
    if is_debug_mode():
        logger.info(f"BackgroundService: Initialized last analyzed reflection timestamp to {_last_reflection_analyzed_timestamp}")
        logger.info(f"BackgroundService: Initialized facts file mtime to {_last_facts_file_mtime}")

    next_reflection_run_time = time.time() + _polling_interval_seconds
    next_fact_curation_run_time = time.time() + FACT_CURATION_INTERVAL_SECONDS # Use config value
    next_project_execution_run_time = time.time() + PROJECT_EXECUTION_INTERVAL_SECONDS
    next_self_healing_run_time = time.time() + _self_healing_interval_seconds

    # Logic to run architect audit immediately if overdue
    if time.time() - _last_architect_audit_timestamp > _architect_audit_interval_seconds:
         next_architect_audit_run_time = time.time()
    else:
         next_architect_audit_run_time = _last_architect_audit_timestamp + _architect_audit_interval_seconds

    # Initialize LearningAgent for self-healing
    # We create local instances as this service might run independently or alongside web_app
    try:
        nm = NotificationManager()
        tm = TaskManager(notification_manager=nm)
        learning_agent = LearningAgent(task_manager=tm, notification_manager=nm)
        logger.info("BackgroundService: LearningAgent initialized for self-healing.")
    except Exception as e: # pragma: no cover
        logger.error(f"BackgroundService: Failed to initialize LearningAgent: {e}")
        learning_agent = None

    while _background_service_active:
        current_loop_time = time.time()
        
        # --- Self-Reflection Task ---
        if current_loop_time >= next_reflection_run_time:
            current_time_str_reflection = time.strftime('%Y-%m-%d %H:%M:%S')
            
            # Optimization: Check if there are new logs since last analysis
            latest_log_timestamp = global_reflection_log.get_last_entry_timestamp()
            
            if latest_log_timestamp <= _last_reflection_analyzed_timestamp:
                if is_debug_mode():
                    logger.debug(f"BackgroundService: Skipping self-reflection. No new logs since {_last_reflection_analyzed_timestamp} (Current latest: {latest_log_timestamp}).")
                # Even if skipped, we schedule the next check
                next_reflection_run_time = time.time() + _polling_interval_seconds
            else:
                logger.info(f"BackgroundService: Running self-reflection cycle (current time: {current_time_str_reflection}, entries updated)...")
                try:
                    available_tools = await asyncio.to_thread(tool_system.tool_system_instance.list_tools)
                    if not available_tools: # pragma: no cover
                        logger.info("BackgroundService: No tools available for reflection cycle. Skipping self-reflection.")
                    else:
                        suggestions = await asyncio.to_thread(run_self_reflection_cycle, available_tools=available_tools)
                        
                        # Update the timestamp only after a successful run attempt (even if no suggestions)
                        # We use the timestamp we fetched before the run to be safe, or fetch again?
                        # Fetching again is safer in case logs were added *during* the run.
                        _last_reflection_analyzed_timestamp = global_reflection_log.get_last_entry_timestamp()

                        if suggestions: # pragma: no cover
                            logger.info(f"BackgroundService: Self-reflection cycle generated {len(suggestions)} suggestions.")
                            if learning_agent:
                                ingested_count = learning_agent.ingest_reflection_suggestions(suggestions)
                                if ingested_count > 0:
                                    logger.info(f"BackgroundService: Passed {ingested_count} approved suggestions to Learning Agent for autonomous action.")
                        elif suggestions == []: # pragma: no cover
                            logger.info("BackgroundService: Self-reflection cycle generated no suggestions.")
                        else: 
                            logger.info("BackgroundService: Self-reflection cycle did not complete normally.")
                except Exception as e: # pragma: no cover
                    logger.error(f"BackgroundService: Error during self-reflection cycle: {e}", exc_info=True)
                
                next_reflection_run_time = time.time() + _polling_interval_seconds

        # --- LLM-Powered Fact Curation Task ---
        if current_loop_time >= next_fact_curation_run_time:
            current_time_str_curation = time.strftime('%Y-%m-%d %H:%M:%S')
            
            # Optimization: Check if facts file has been modified
            current_facts_mtime = 0.0
            if os.path.exists(LEARNED_FACTS_FILEPATH):
                current_facts_mtime = os.path.getmtime(LEARNED_FACTS_FILEPATH)
            
            # We add a small buffer (e.g. 1 sec) or just strict inequality. 
            # If the file hasn't changed since we last looked/updated, skip.
            if current_facts_mtime <= _last_facts_file_mtime:
                if is_debug_mode():
                    logger.debug(f"BackgroundService: Skipping fact curation. File not modified since {_last_facts_file_mtime}.")
                next_fact_curation_run_time = time.time() + FACT_CURATION_INTERVAL_SECONDS
            else:
                logger.info(f"BackgroundService: Running LLM fact curation (current time: {current_time_str_curation})...")
                try:
                    # Call the dedicated function from knowledge_tools
                    curation_success = await run_periodic_fact_store_curation_async()
                    
                    if curation_success: # pragma: no cover
                        logger.info("BackgroundService: LLM fact curation process completed successfully.")
                    else: # pragma: no cover
                        logger.warning("BackgroundService: LLM fact curation process encountered an issue or made no changes.")
                    
                    # Update our mtime tracker to NOW (or re-read file mtime)
                    # Re-reading is safer as curation writes to the file.
                    if os.path.exists(LEARNED_FACTS_FILEPATH):
                        _last_facts_file_mtime = os.path.getmtime(LEARNED_FACTS_FILEPATH)
                    else:
                        _last_facts_file_mtime = time.time()

                except Exception as e: # pragma: no cover
                    logger.error(f"BackgroundService: Error during LLM fact curation: {e}", exc_info=True)
                
                _last_fact_curation_time = time.time()
                next_fact_curation_run_time = time.time() + FACT_CURATION_INTERVAL_SECONDS # Use config value
        
        # --- Autonomous Project Coding Task ---
        if PROJECT_TOOLS_AVAILABLE and current_loop_time >= next_project_execution_run_time:
            current_time_str_project_exec = await asyncio.to_thread(time.strftime, '%Y-%m-%d %H:%M:%S')
            print(f"BackgroundService (Async): Scanning for projects with planned tasks (current time: {current_time_str_project_exec})...")
            projects_worked_on_this_cycle = 0
            try:
                if not os.path.isdir(BASE_PROJECTS_DIR): # pragma: no cover
                    logger.info(f"BackgroundService: Projects directory '{BASE_PROJECTS_DIR}' does not exist. Skipping project execution scan.")
                else:
                    for project_sanitized_name in os.listdir(BASE_PROJECTS_DIR):
                        project_dir_path = os.path.join(BASE_PROJECTS_DIR, project_sanitized_name)
                        if os.path.isdir(project_dir_path):
                            manifest_path = os.path.join(project_dir_path, "_ai_project_manifest.json")
                            if os.path.exists(manifest_path):
                                manifest_content_str = read_text_from_file(manifest_path)
                                if manifest_content_str.startswith("Error:"): # pragma: no cover
                                    logger.warning(f"BackgroundService: Error reading manifest for {project_sanitized_name}: {manifest_content_str}")
                                    continue
                                try:
                                    manifest_data = json.loads(manifest_content_str)
                                    # Ensure project_name is derived correctly, it might not be the sanitized name
                                    original_project_name = manifest_data.get("project_name", project_sanitized_name) 
                                    
                                    # Check for planned tasks more accurately
                                    # The manifest schema stores tasks in 'development_tasks'
                                    development_tasks = manifest_data.get("development_tasks", [])
                                    has_planned_tasks = False
                                    if isinstance(development_tasks, list):
                                        for task in development_tasks:
                                            if isinstance(task, dict) and task.get("status") == "planned":
                                                has_planned_tasks = True
                                                break
                                    
                                    if has_planned_tasks:
                                        logger.info(f"BackgroundService: Project '{original_project_name}' has planned tasks. Attempting to execute coding plan.")
                                        # Pass the BASE_PROJECTS_DIR to ensure execute_project_coding_plan uses the correct root
                                        # if it doesn't inherit it via its own imports of file_system_tools.
                                        exec_result = await execute_project_coding_plan(original_project_name, base_projects_dir_override=BASE_PROJECTS_DIR)
                                        logger.info(f"BackgroundService: Result for '{original_project_name}':\n{exec_result}")
                                        projects_worked_on_this_cycle += 1
                                    else:
                                        if is_debug_mode(): # pragma: no cover
                                            logger.debug(f"[DEBUG BACKGROUND_SERVICE] Project '{original_project_name}' has no 'planned' development tasks in its manifest.")
                                except json.JSONDecodeError: # pragma: no cover
                                    logger.error(f"BackgroundService: Error decoding manifest JSON for {project_sanitized_name}.", exc_info=True)
                                except Exception as e_proj_scan: # pragma: no cover
                                    logger.error(f"BackgroundService: Error processing project {project_sanitized_name}: {e_proj_scan}", exc_info=True)
                if projects_worked_on_this_cycle == 0 and is_debug_mode(): # pragma: no cover
                    logger.debug(f"[DEBUG BACKGROUND_SERVICE] No projects found with pending tasks in this scan.")

            except Exception as e: # pragma: no cover
                logger.error(f"BackgroundService: Error during autonomous project execution scan: {e}", exc_info=True)
            _last_project_execution_scan_time = time.time()
            next_project_execution_run_time = time.time() + PROJECT_EXECUTION_INTERVAL_SECONDS

        # --- Autonomous Self-Healing Task ---
        if learning_agent and current_loop_time >= next_self_healing_run_time:
            current_time_str_healing = time.strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"BackgroundService: Running self-healing cycle (current time: {current_time_str_healing})...")
            try:
                processed_count = await learning_agent.process_self_healing_insights()
                if processed_count > 0:
                    logger.info(f"BackgroundService: Self-healing processed {processed_count} insights.")
                else:
                    logger.info("BackgroundService: No insights found for self-healing.")
            except Exception as e:
                logger.error(f"BackgroundService: Error during self-healing cycle: {e}", exc_info=True)
            _last_self_healing_time = time.time()
            next_self_healing_run_time = time.time() + _self_healing_interval_seconds

        # --- Evolutionary Architect Audit Task ---
        if current_loop_time >= next_architect_audit_run_time:
             logger.info(f"BackgroundService: Running Evolutionary Architect Audit...")
             try:
                 proposal = await perform_architectural_audit()
                 if proposal and learning_agent and learning_agent.notification_manager:
                     summary = proposal.get('proposal', {}).get('summary', 'No summary provided')
                     target_file = proposal.get('target_file', 'unknown file')

                     learning_agent.notification_manager.add_notification(
                         event_type=NotificationType.EVOLUTION_PROPOSAL,
                         summary_message=f"Architect Proposal for {os.path.basename(target_file)}: {summary}",
                         details_payload=proposal
                     )
                     logger.info(f"BackgroundService: Generated evolution proposal for {target_file}")
                 elif not proposal:
                     logger.info("BackgroundService: No proposal generated during audit.")

                 _last_architect_audit_timestamp = time.time()
                 _save_architect_state()
                 next_architect_audit_run_time = time.time() + _architect_audit_interval_seconds

             except Exception as e:
                 logger.error(f"BackgroundService: Error during Evolutionary Architect audit: {e}", exc_info=True)
                 # Retry later to avoid rapid error loop
                 next_architect_audit_run_time = time.time() + 3600
        
        # Determine sleep time until the next event
        time_until_next_reflection = max(0, next_reflection_run_time - time.time())
        time_until_next_curation = max(0, next_fact_curation_run_time - time.time())
        time_until_next_project_exec = max(0, next_project_execution_run_time - time.time()) if PROJECT_TOOLS_AVAILABLE else float('inf')
        time_until_next_healing = max(0, next_self_healing_run_time - time.time()) if learning_agent else float('inf')
        time_until_next_audit = max(0, next_architect_audit_run_time - time.time())
        
        sleep_duration = min(time_until_next_reflection, time_until_next_curation, time_until_next_project_exec, time_until_next_healing, time_until_next_audit, 10)

        try:
            if is_debug_mode(): # pragma: no cover
                debug_msg_parts = [f"Sleeping for {sleep_duration:.2f}s."]
                debug_msg_parts.append(f"Next reflection in {time_until_next_reflection:.0f}s")
                debug_msg_parts.append(f"next curation in {time_until_next_curation:.0f}s")
                if PROJECT_TOOLS_AVAILABLE:
                    debug_msg_parts.append(f"next project exec scan in {time_until_next_project_exec:.0f}s")
                if learning_agent:
                    debug_msg_parts.append(f"next self-healing in {time_until_next_healing:.0f}s")
                debug_msg_parts.append(f"next audit in {time_until_next_audit:.0f}s")
                logger.debug(f"[DEBUG BACKGROUND_SERVICE] {', '.join(debug_msg_parts)}.")
            await asyncio.sleep(sleep_duration)
        except asyncio.CancelledError: # pragma: no cover
            logger.info("BackgroundService: Loop cancelled during sleep.")
            break 
            
    logger.info("BackgroundService: Async loop finished.")

# Renamed and made synchronous as it just creates a task
def start_background_services():
    global _background_service_active, _background_task, _last_fact_curation_time, _last_project_execution_scan_time, _last_self_healing_time
    # Ensure is_debug_mode is available or imported if used here

    if _background_service_active and isinstance(_background_task, asyncio.Task) and not _background_task.done():
        logger.info("BackgroundService: Service is already running or starting.") # pragma: no cover
        return
        
    _background_service_active = True
    _last_fact_curation_time = 0.0 
    _last_project_execution_scan_time = 0.0 # Reset this too
    _last_self_healing_time = 0.0
    if is_debug_mode():
        logger.info("BackgroundService: Attempting to start service...")
    try:
        loop = asyncio.get_running_loop() 
        _background_task = loop.create_task(_background_loop_async())
        if is_debug_mode():
            logger.info("BackgroundService: Service task created.")
    except RuntimeError: # pragma: no cover
        logger.error("BackgroundService: Asyncio loop not running. Cannot start service this way.")
        _background_service_active = False 
        return
    except Exception as e: # pragma: no cover
        logger.error(f"BackgroundService: Failed to create service task: {e}", exc_info=True)
        _background_service_active = False
        return

# Renamed, remains async
async def stop_background_services():
    global _background_service_active, _background_task
    
    if not _background_service_active or not isinstance(_background_task, asyncio.Task): # pragma: no cover
        logger.info("BackgroundService: Service is not running or task not found.")
        return

    logger.info("BackgroundService: Attempting to stop service...")
    _background_service_active = False 
    
    if _background_task and not _background_task.done(): # pragma: no branch
        _background_task.cancel()
        try:
            await _background_task 
            logger.info("BackgroundService: Service task successfully cancelled and awaited.") # pragma: no cover
        except asyncio.CancelledError: # pragma: no cover
            logger.info("BackgroundService: Service task explicitly cancelled.")
        except Exception as e: # pragma: no cover
            logger.error(f"BackgroundService: Error while awaiting cancelled task: {e}", exc_info=True)
            
    _background_task = None
    logger.info("BackgroundService: Service stop procedure completed.")

def is_background_service_active() -> bool:
    """Checks if the background service is currently active."""
    return _background_service_active

def run_background_services_forever():
    """
    Synchronous entry point that sets up a new asyncio event loop and runs 
    the background services indefinitely. 
    Ideal for running in a separate thread (e.g., via socketio.start_background_task).
    """
    global _background_service_active
    
    if _background_service_active:
        logger.warning("BackgroundService: Service already active. Ignoring request to start forever loop.")
        return

    logger.info("BackgroundService: Starting standalone background loop...")
    _background_service_active = True
    
    # Create a new loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(_background_loop_async())
    except Exception as e:
        logger.error(f"BackgroundService: Standalone loop generated exception: {e}", exc_info=True)
    finally:
        _background_service_active = False
        try:
            # Cancel all tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            logger.info("BackgroundService: Standalone loop closed.")
        except Exception as e:
            logger.error(f"BackgroundService: Error closing standalone loop: {e}")

if __name__ == '__main__': # pragma: no cover
    # Minimal __main__ for testing the background service loop structure manually
    # Actual tool imports and functionality would require more setup or mocking
    
    # Mock necessary components if they are not available in this standalone run
    class MockToolSystemInstance:
        def list_tools(self): return {"mock_tool": "A mock tool for testing."}
    
    class MockReflectionModule:
        def run_self_reflection_cycle(self, available_tools):
            logger.info("--- MOCK run_self_reflection_cycle CALLED ---")
            time.sleep(0.1) # Simulate work
            return [{"suggestion_id": "mock_suggestion_main", "text": "Mock reflection suggestion"}]

    class MockKnowledgeToolsModule:
        async def run_periodic_fact_store_curation_async(self): # Matched name
            logger.info("--- MOCK run_periodic_fact_store_curation_async CALLED ---")
            await asyncio.sleep(0.1) # Simulate async work
            return True

    # Apply mocks
    tool_system.tool_system_instance = MockToolSystemInstance()
    run_self_reflection_cycle_orig = run_self_reflection_cycle
    run_periodic_fact_store_curation_async_orig = run_periodic_fact_store_curation_async

    globals()['run_self_reflection_cycle'] = MockReflectionModule().run_self_reflection_cycle
    globals()['run_periodic_fact_store_curation_async'] = MockKnowledgeToolsModule().run_periodic_fact_store_curation_async

    logger.info("--- Background Service Manual Test (via __main__) ---")
    
    async def test_run():
        global _polling_interval_seconds, _fact_curation_interval_seconds
        global PROJECT_EXECUTION_INTERVAL_SECONDS # Ensure this is accessible

        logger.info("Starting background service with very short intervals for testing...")
        _polling_interval_seconds = 3  # Short interval for reflection
        # FACT_CURATION_INTERVAL_SECONDS is now from config, so we'd mock config or set it high for manual test
        # For this manual test, let's assume config.FACT_CURATION_INTERVAL_SECONDS is also short or we override it locally
        # For simplicity, this __main__ test will use the config value.
        PROJECT_EXECUTION_INTERVAL_SECONDS = 5 # Short interval for project execution

        start_background_services() # Call the renamed sync function
        
        logger.info("Background service is running. Main test will sleep for 15 seconds.")
        await asyncio.sleep(15) 
        
        logger.info("\nStopping background service...")
        await stop_background_services() # Call the renamed async function
        logger.info("Background service stopped by test.")

    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_run())
    
    globals()['run_self_reflection_cycle'] = run_self_reflection_cycle_orig
    globals()['run_periodic_fact_store_curation_async'] = run_periodic_fact_store_curation_async_orig

    logger.info("--- Background Service Manual Test Finished ---")
### END FILE: core/background_service.py ###