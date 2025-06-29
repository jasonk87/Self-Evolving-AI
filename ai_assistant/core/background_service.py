### START FILE: core/background_service.py ###
# ai_assistant/core/background_service.py
import asyncio
import time
import json
import os # Added
import re
import logging
from typing import Optional, List

from .notification_manager import NotificationManager # Added import
from ai_assistant.core.autonomous_reflection import run_self_reflection_cycle
from ai_assistant.tools import tool_system # To get available tools
# Modified: Import the specific curation function and config for interval
from ai_assistant.custom_tools.knowledge_tools import run_periodic_fact_store_curation_async
from ai_assistant.config import is_debug_mode, FACT_CURATION_INTERVAL_SECONDS

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
_nm_instance_for_bg_service: Optional[NotificationManager] = None # To hold NM instance
_tm_instance_for_bg_service: Optional['TaskManager'] = None # To hold TaskManager instance
_polling_interval_seconds = 300  # For self-reflection (5 minutes)
_long_task_check_interval_seconds = 60 # Check for long tasks every 1 minute
_last_fact_curation_time: float = 0.0
_last_project_execution_scan_time: float = 0.0
_last_long_task_check_time: float = 0.0
_task_last_checkin_time: Dict[str, float] = {} # Stores task_id: timestamp of last check-in

# Define these constants based on Step 1 of the plan
LONG_RUNNING_TASK_THRESHOLD_SECONDS = 5 * 60  # 5 minutes
TASK_CHECKIN_COOLDOWN_SECONDS = 15 * 60    # 15 minutes
MONITORED_TASK_TYPES_FOR_CHECKIN: List['ActiveTaskType'] = [] # Will be populated after ActiveTaskType is available
ACTIVE_STATUSES_FOR_MONITORING: List['ActiveTaskStatus'] = [] # Will be populated

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

from ai_assistant.core.task_manager import ActiveTaskType, ActiveTaskStatus # For monitored types/statuses
from datetime import datetime, timezone # For duration calculation

# --- Asyncio Version ---
async def _background_loop_async():
    global _last_fact_curation_time, _last_project_execution_scan_time, _last_long_task_check_time, _task_last_checkin_time
    logger.info("--- BACKGROUND SERVICE: Main _background_loop_async started. ---")
    current_time_init = time.time()
    _last_fact_curation_time = current_time_init
    _last_project_execution_scan_time = current_time_init
    _last_long_task_check_time = current_time_init
    _task_last_checkin_time = {} # Ensure it's reset if service restarts

    next_reflection_run_time = current_time_init + _polling_interval_seconds
    next_fact_curation_run_time = current_time_init + FACT_CURATION_INTERVAL_SECONDS
    next_project_execution_run_time = current_time_init + PROJECT_EXECUTION_INTERVAL_SECONDS
    next_long_task_check_run_time = current_time_init + _long_task_check_interval_seconds


    while _background_service_active:
        current_loop_time = time.time()
        
        # --- Self-Reflection Task ---
        if current_loop_time >= next_reflection_run_time:
            logger.info(f"--- BACKGROUND SERVICE: Starting self-reflection cycle (Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
            try:
                available_tools = await asyncio.to_thread(tool_system.tool_system_instance.list_tools)
                if not available_tools: # pragma: no cover
                    logger.info("--- BACKGROUND SERVICE: No tools available for reflection cycle. Skipping self-reflection. ---")
                else:
                    # Pass the notification manager instance to the reflection cycle
                    suggestions = await asyncio.to_thread(
                        run_self_reflection_cycle,
                        available_tools=available_tools,
                        notification_manager=_nm_instance_for_bg_service # Pass stored NM instance
                    )
                    if suggestions:
                        logger.info(f"--- BACKGROUND SERVICE: Self-reflection cycle generated {len(suggestions)} suggestions. Attempting to save them. ---")
                        # Import suggestion_manager_module here or at the top of the file
                        # For now, assuming it's available (e.g., if background_service is part of a package where it can be imported)
                        # This might require `import ai_assistant.core.suggestion_manager as suggestion_manager_module` at the top
                        import ai_assistant.core.suggestion_manager as suggestion_manager_module

                        saved_count = 0
                        for sug_data in suggestions:
                            if isinstance(sug_data, dict):
                                try:
                                    # Adapt field names if necessary from what run_self_reflection_cycle returns
                                    # vs what add_new_suggestion expects.
                                    # add_new_suggestion expects: type, description, source_reflection_id, notification_manager
                                    sug_type = sug_data.get("action_type", "self_improvement_idea")
                                    sug_desc = sug_data.get("suggestion_text", "No description provided by reflection.")
                                    sug_source_id = sug_data.get("suggestion_id") # Use the ID generated by reflection cycle as a source_reflection_id

                                    added_sug = suggestion_manager_module.add_new_suggestion(
                                        type=sug_type,
                                        description=sug_desc,
                                        source_reflection_id=sug_source_id,
                                        notification_manager=_nm_instance_for_bg_service # Pass the NM instance
                                    )
                                    if added_sug:
                                        saved_count += 1
                                except Exception as e_add_sug:
                                    logger.error(f"--- BACKGROUND SERVICE: Error adding suggestion '{sug_data.get('suggestion_id')}': {e_add_sug} ---", exc_info=True)
                        logger.info(f"--- BACKGROUND SERVICE: Successfully saved {saved_count} out of {len(suggestions)} generated suggestions. ---")

                    elif suggestions == []:
                        logger.info("--- BACKGROUND SERVICE: Self-reflection cycle generated no suggestions. ---")
                    else: 
                        logger.info("--- BACKGROUND SERVICE: Self-reflection cycle did not complete or was aborted (e.g. not enough log data). ---")
            except Exception as e: # pragma: no cover
                logger.error(f"--- BACKGROUND SERVICE: Error during self-reflection cycle: {e} ---", exc_info=True)
            next_reflection_run_time = time.time() + _polling_interval_seconds
            logger.info(f"--- BACKGROUND SERVICE: Self-reflection cycle finished. Next run in approx. {_polling_interval_seconds}s. ---")

        # --- LLM-Powered Fact Curation Task ---
        if current_loop_time >= next_fact_curation_run_time:
            logger.info(f"--- BACKGROUND SERVICE: Starting LLM fact curation (Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
            try:
                curation_success = await run_periodic_fact_store_curation_async()
                if curation_success: # pragma: no cover
                    logger.info("--- BACKGROUND SERVICE: LLM fact curation process completed successfully. ---")
                else: # pragma: no cover
                    logger.warning("--- BACKGROUND SERVICE: LLM fact curation process encountered an issue or made no changes. ---")
            except Exception as e: # pragma: no cover
                logger.error(f"--- BACKGROUND SERVICE: Error during LLM fact curation: {e} ---", exc_info=True)
            _last_fact_curation_time = time.time()
            next_fact_curation_run_time = time.time() + FACT_CURATION_INTERVAL_SECONDS
            logger.info(f"--- BACKGROUND SERVICE: LLM fact curation finished. Next run in approx. {FACT_CURATION_INTERVAL_SECONDS}s. ---")
        
        # --- Autonomous Project Coding Task ---
        if PROJECT_TOOLS_AVAILABLE and current_loop_time >= next_project_execution_run_time:
            logger.info(f"--- BACKGROUND SERVICE: Starting project execution scan (Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
            projects_worked_on_this_cycle = 0
            try:
                if not os.path.isdir(BASE_PROJECTS_DIR): # pragma: no cover
                    logger.info(f"--- BACKGROUND SERVICE: Projects directory '{BASE_PROJECTS_DIR}' does not exist. Skipping project execution scan. ---")
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
                logger.error(f"--- BACKGROUND SERVICE: Error during autonomous project execution scan: {e} ---", exc_info=True)
            _last_project_execution_scan_time = time.time()
            next_project_execution_run_time = time.time() + PROJECT_EXECUTION_INTERVAL_SECONDS
            logger.info(f"--- BACKGROUND SERVICE: Project execution scan finished. Next run in approx. {PROJECT_EXECUTION_INTERVAL_SECONDS}s. ---")
        
        # Determine sleep time until the next event
        time_until_next_reflection = max(0, next_reflection_run_time - time.time())
        time_until_next_curation = max(0, next_fact_curation_run_time - time.time())
        time_until_next_project_exec = max(0, next_project_execution_run_time - time.time()) if PROJECT_TOOLS_AVAILABLE else float('inf')
        time_until_next_long_task_check = max(0, next_long_task_check_run_time - time.time()) if _tm_instance_for_bg_service else float('inf')
        
        # --- Long-Running Task Check ---
        if _tm_instance_for_bg_service and current_loop_time >= next_long_task_check_run_time:
            logger.info(f"--- BACKGROUND SERVICE: Checking for long-running tasks (Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}) ---")
            try:
                active_tasks = await asyncio.to_thread(_tm_instance_for_bg_service.list_active_tasks)
                for task in active_tasks:
                    if task.task_type in MONITORED_TASK_TYPES_FOR_CHECKIN and \
                       task.status in ACTIVE_STATUSES_FOR_MONITORING:

                        # Use created_at for total duration since task started and is still active
                        task_start_time = task.created_at
                        if not task_start_time.tzinfo: # If naive, assume UTC (as per ActiveTask default factory)
                            task_start_time = task_start_time.replace(tzinfo=timezone.utc)

                        duration_seconds = (datetime.now(timezone.utc) - task_start_time).total_seconds()

                        if duration_seconds > LONG_RUNNING_TASK_THRESHOLD_SECONDS:
                            last_checkin = _task_last_checkin_time.get(task.task_id, 0)
                            if (current_loop_time - last_checkin) > TASK_CHECKIN_COOLDOWN_SECONDS:
                                duration_minutes = int(duration_seconds / 60)
                                # Select a message template (can be randomized later)
                                # For now, using the first template:
                                # "I'm still working on '[Task Description]' (Task ID: [Task ID]). It's been about [Duration] minutes. Would you like an update on the current step, or should I continue as planned?"
                                message_template = "I'm still working on '{task_description}' (Task ID: {task_id}). It's been about {duration_minutes} minutes. Would you like an update on the current step, or should I continue as planned?"
                                checkin_message = message_template.format(
                                    task_description=task.description[:70] + "..." if len(task.description) > 70 else task.description,
                                    task_id=task.task_id,
                                    duration_minutes=duration_minutes
                                )

                                if _nm_instance_for_bg_service:
                                    # This NotificationType needs to be defined in notification_manager.py
                                    # For now, using a placeholder string, assuming it will be mapped to an Enum.
                                    # The frontend will need to know how to handle this type.
                                    from ai_assistant.core.notification_manager import NotificationType # Import here

                                    _nm_instance_for_bg_service.add_notification(
                                        event_type=getattr(NotificationType, "PROACTIVE_TASK_CHECKIN", NotificationType.SYSTEM_ALERT), # Use getattr for safety
                                        summary_message=f"Task Check-in: {task.description[:50]}...",
                                        details_payload={"proactive_chat_message": checkin_message, "task_id": task.task_id},
                                        related_item_id=task.task_id,
                                        related_item_type="task_checkin"
                                    )
                                    _task_last_checkin_time[task.task_id] = current_loop_time
                                    logger.info(f"--- BACKGROUND SERVICE: Sent PROACTIVE_TASK_CHECKIN for task {task.task_id} ('{task.description[:30]}...'). Message: {checkin_message}")
                                else:
                                    logger.warning("--- BACKGROUND SERVICE: NotificationManager not available. Cannot send proactive task check-in.")
                            # else: logger.debug(f"Task {task.task_id} is long-running but in cooldown.")
                        # else: logger.debug(f"Task {task.task_id} duration {duration_seconds:.0f}s not over threshold {LONG_RUNNING_TASK_THRESHOLD_SECONDS}s.")
            except Exception as e_task_check:
                logger.error(f"--- BACKGROUND SERVICE: Error during long-running task check: {e_task_check} ---", exc_info=True)
            _last_long_task_check_time = current_loop_time # Update last check time
            next_long_task_check_run_time = current_loop_time + _long_task_check_interval_seconds
        # --- End Long-Running Task Check ---

        sleep_duration = min(time_until_next_reflection, time_until_next_curation, time_until_next_project_exec, time_until_next_long_task_check, 10)

        try:
            if is_debug_mode(): # pragma: no cover
                debug_msg_parts = [f"Sleeping for {sleep_duration:.2f}s."]
                debug_msg_parts.append(f"Next reflection in {time_until_next_reflection:.0f}s")
                debug_msg_parts.append(f"next curation in {time_until_next_curation:.0f}s")
                if PROJECT_TOOLS_AVAILABLE:
                    debug_msg_parts.append(f"next project exec scan in {time_until_next_project_exec:.0f}s")
                logger.debug(f"[DEBUG BACKGROUND_SERVICE] {', '.join(debug_msg_parts)}.")
            await asyncio.sleep(sleep_duration)
        except asyncio.CancelledError: # pragma: no cover
            logger.info("BackgroundService: Loop cancelled during sleep.")
            break 
            
    logger.info("BackgroundService: Async loop finished.")

def start_background_services(
    notification_manager_instance: Optional[NotificationManager] = None,
    task_manager_instance: Optional['TaskManager'] = None # Added TaskManager
):
    global _background_service_active, _background_task, _last_fact_curation_time
    global _last_project_execution_scan_time, _nm_instance_for_bg_service, _tm_instance_for_bg_service
    global MONITORED_TASK_TYPES_FOR_CHECKIN, ACTIVE_STATUSES_FOR_MONITORING, _last_long_task_check_time

    if notification_manager_instance:
        _nm_instance_for_bg_service = notification_manager_instance
        logger.info(f"--- BACKGROUND SERVICE: NotificationManager instance received: {_nm_instance_for_bg_service} ---")
    else:
        logger.warning("--- BACKGROUND SERVICE: Started without a NotificationManager instance. Some notifications may be disabled. ---")
        _nm_instance_for_bg_service = None

    if task_manager_instance: # Store TaskManager instance
        _tm_instance_for_bg_service = task_manager_instance
        logger.info(f"--- BACKGROUND SERVICE: TaskManager instance received: {_tm_instance_for_bg_service} ---")
    else:
        logger.warning("--- BACKGROUND SERVICE: Started without a TaskManager instance. Long-running task monitoring will be disabled. ---")
        _tm_instance_for_bg_service = None

    # Populate monitored types and statuses (idempotent)
    if not MONITORED_TASK_TYPES_FOR_CHECKIN: # Check if already populated
        MONITORED_TASK_TYPES_FOR_CHECKIN = [
            ActiveTaskType.USER_PROJECT_CREATION,
            ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION
        ]
        logger.info(f"--- BACKGROUND SERVICE: Populated MONITORED_TASK_TYPES_FOR_CHECKIN: {MONITORED_TASK_TYPES_FOR_CHECKIN} ---")

    if not ACTIVE_STATUSES_FOR_MONITORING: # Check if already populated
        ACTIVE_STATUSES_FOR_MONITORING = [
            ActiveTaskStatus.INITIALIZING,
            ActiveTaskStatus.PLANNING,
            ActiveTaskStatus.GENERATING_CODE,
            ActiveTaskStatus.AWAITING_CRITIC_REVIEW,
            ActiveTaskStatus.POST_MOD_TESTING,
            ActiveTaskStatus.APPLYING_CHANGES,
            ActiveTaskStatus.EXECUTING_PROJECT_PLAN # Important for hierarchical tasks
        ]
        logger.info(f"--- BACKGROUND SERVICE: Populated ACTIVE_STATUSES_FOR_MONITORING: {ACTIVE_STATUSES_FOR_MONITORING} ---")


    if _background_service_active and isinstance(_background_task, asyncio.Task) and not _background_task.done():
        logger.info("--- BACKGROUND SERVICE: Service is already running or starting. ---") # pragma: no cover
        return
        
    _background_service_active = True
    _last_fact_curation_time = 0.0 
    _last_project_execution_scan_time = 0.0
    _last_long_task_check_time = 0.0 # Initialize this too
    logger.info("--- BACKGROUND SERVICE: Attempting to start service... ---")
    try:
        loop = asyncio.get_running_loop() 
        _background_task = loop.create_task(_background_loop_async())
        logger.info(f"--- BACKGROUND SERVICE: Service task _background_loop_async created: {_background_task} ---")
    except RuntimeError: # pragma: no cover
        logger.error("--- BACKGROUND SERVICE: Asyncio loop not running. Cannot start service this way. ---")
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
        logger.info("--- BACKGROUND SERVICE: Service is not running or task not found. Cannot stop. ---")
        return

    logger.info("--- BACKGROUND SERVICE: Attempting to stop service... ---")
    _background_service_active = False 
    
    if _background_task and not _background_task.done(): # pragma: no branch
        logger.info(f"--- BACKGROUND SERVICE: Cancelling background task: {_background_task} ---")
        _background_task.cancel()
        try:
            await _background_task 
            logger.info("--- BACKGROUND SERVICE: Service task successfully cancelled and awaited. ---") # pragma: no cover
        except asyncio.CancelledError: # pragma: no cover
            logger.info("--- BACKGROUND SERVICE: Service task explicitly cancelled by await. ---")
        except Exception as e: # pragma: no cover
            logger.error(f"--- BACKGROUND SERVICE: Error while awaiting cancelled task: {e} ---", exc_info=True)
            
    _background_task = None
    logger.info("--- BACKGROUND SERVICE: Service stop procedure completed. ---")

def is_background_service_active() -> bool:
    """Checks if the background service is currently active."""
    return _background_service_active

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