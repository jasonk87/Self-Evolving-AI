### START FILE: core/background_service.py ###
# ai_assistant/core/background_service.py
import asyncio
import time
import json
import os # Added
import sys # Added for subprocess execution

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
from ai_assistant.core.approval_manager import approval_manager
from ai_assistant.learning.learning import InsightType

# Added for Evolutionary Architect
from ai_assistant.learning.evolutionary_architect import perform_architectural_audit
from ai_assistant.config import get_data_dir, AUTO_APPROVE_DELAY_SECONDS
from ai_assistant.core.reviewer import ReviewerAgent

# Import goal management for autonomous goal processing
from ai_assistant.goals import goal_management
# Fix: Import NotificationType to avoid NameError in autonomous goal processor
from ai_assistant.core.notification_manager import NotificationType

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
_architect_audit_interval_seconds = 900 # 15 minutes for debugging
ARCHITECT_STATE_FILE = "architect_state.json"
_last_auto_approve_check_time: float = 0.0
_auto_approve_check_interval_seconds = 60 # Check frequently, but action depends on request age

# Vision Service State
_last_visual_audit_time: float = 0.0
_visual_audit_interval_seconds = 900 # 15 minutes

# Orchestrator Injection
_orchestrator = None

# Autonomous Goal Processing State
_last_autonomous_goal_check_time: float = 0.0
_autonomous_goal_check_interval_seconds = 30 # Check every 30 seconds

def set_orchestrator(orchestrator_instance):
    """Sets the orchestrator instance for autonomous goal processing."""
    global _orchestrator
    _orchestrator = orchestrator_instance
    logger.info("BackgroundService: Orchestrator instance set.")

async def run_autonomous_goal_processor():
    """
    Checks for pending goals in GoalManager and triggers the Orchestrator to execute them.
    This acts as a bridge between passive GoalManager and active DynamicOrchestrator.
    """
    global _orchestrator

    if not _orchestrator:
        return # Orchestrator not yet ready

    try:
        # Check for pending goals using direct module access
        # Since goal_management is synchronous, we wrap it if needed, but simple dict lookups are fast.
        pending_goals = goal_management.list_goals(status="pending")

        if pending_goals:
            logger.info(f"BackgroundService: Found {len(pending_goals)} pending goals.")

            for goal in pending_goals:
                goal_id = goal.get("id")
                goal_desc = goal.get("description")

                if not goal_id or not goal_desc:
                    logger.warning(f"BackgroundService: Skipping invalid goal structure: {goal}")
                    continue

                # Mark as in_progress immediately to prevent double processing
                # We use the new update_goal_status function that persists to disk
                success = goal_management.update_goal_status(goal_id, status="in_progress")

                if success:
                    logger.info(f"BackgroundService: Autonomous Mission Started: {goal_desc}")

                    # Trigger Execution
                    # We spawn this as a background task so we don't block the service loop
                    # waiting for the entire goal to complete.
                    # We pass a specific session_id to track this execution context.
                    session_id = f"autonomous_goal_{goal_id}"

                    # Log event
                    if hasattr(_orchestrator, 'learning_agent') and _orchestrator.learning_agent and _orchestrator.learning_agent.notification_manager:
                         _orchestrator.learning_agent.notification_manager.add_notification(
                             event_type=NotificationType.SYSTEM_ALERT, # or a new type for MISSION_STARTED
                             summary_message=f"Mission Started: {goal_desc}",
                             details_payload={
                                 "title": "Autonomous Goal Execution",
                                 "goal_id": goal_id,
                                 "description": goal_desc
                             }
                         )

                    # Create task for orchestrator processing
                    asyncio.create_task(
                        _orchestrator.process_prompt(
                            prompt=goal_desc,
                            session_id=session_id
                        )
                    )
                else:
                    logger.error(f"BackgroundService: Failed to update status for goal {goal_id}. Execution aborted to avoid loops.")

    except Exception as e:
        logger.error(f"BackgroundService: Error in autonomous goal processor: {e}", exc_info=True)


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
    global _last_fact_curation_time, _last_project_execution_scan_time, _last_self_healing_time, _last_architect_audit_timestamp, _last_auto_approve_check_time, _last_visual_audit_time, _last_autonomous_goal_check_time
    print("BackgroundService: Async loop started.")
    _last_fact_curation_time = time.time()
    _last_project_execution_scan_time = time.time()
    _last_self_healing_time = time.time()
    _last_auto_approve_check_time = time.time()
    _last_visual_audit_time = time.time()
    _last_autonomous_goal_check_time = time.time()
    
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
    next_auto_approve_check_time = time.time() + _auto_approve_check_interval_seconds
    next_visual_audit_run_time = time.time() + _visual_audit_interval_seconds
    next_autonomous_goal_check_time = time.time() + _autonomous_goal_check_interval_seconds

    # Logic to run architect audit immediately if overdue
    if time.time() - _last_architect_audit_timestamp > _architect_audit_interval_seconds:
         next_architect_audit_run_time = time.time()
    else:
         next_architect_audit_run_time = _last_architect_audit_timestamp + _architect_audit_interval_seconds

    # Initialize Vision Service (lazy loaded later if needed, but import here to ensure available)
    try:
        from ai_assistant.core.vision_service import VisionService
        vision_service = VisionService()
    except ImportError as e:
        logger.error(f"BackgroundService: Failed to import VisionService: {e}")
        vision_service = None

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
    
    # Initialize Reviewer for Auto-Approvals
    reviewer_agent = ReviewerAgent()

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

                        if suggestions:
                            logger.info(f"BackgroundService: Self-reflection cycle generated {len(suggestions)} suggestions. Queueing for approval.")
                            if learning_agent:
                                for suggestion in suggestions:
                                    # Create specific callback for this suggestion
                                    # We use default argument binding to capture the loop variable 'suggestion'
                                    async def _ingest_callback(s=suggestion):
                                        learning_agent.ingest_reflection_suggestions([s])
                                    
                                    approval_manager.add_request(
                                        req_type="suggestion",
                                        data=suggestion,
                                        description=suggestion.get("suggestion_text", "No description"),
                                        execute_func=_ingest_callback
                                    )
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
        
        # --- Autonomous Goal Processing Task ---
        if current_loop_time >= next_autonomous_goal_check_time:
            await run_autonomous_goal_processor()
            _last_autonomous_goal_check_time = time.time()
            next_autonomous_goal_check_time = time.time() + _autonomous_goal_check_interval_seconds

        # --- Visual Audit Task ---
        if vision_service and current_loop_time >= next_visual_audit_run_time:
            logger.info("BackgroundService: Running Visual Audit...")
            try:
                if os.path.isdir(BASE_PROJECTS_DIR):
                    for project_name in os.listdir(BASE_PROJECTS_DIR):
                        project_path = os.path.join(BASE_PROJECTS_DIR, project_name)
                        if os.path.isdir(project_path):
                            # Look for index.html or main entry points
                            entry_points = ["index.html", "main.html", "game.html", "dashboard.html"]
                            target_html = None
                            for ep in entry_points:
                                if os.path.exists(os.path.join(project_path, ep)):
                                    target_html = os.path.join(project_path, ep)
                                    break

                            if target_html:
                                logger.info(f"BackgroundService: Auditing visuals for {project_name} ({os.path.basename(target_html)})...")
                                screenshot_b64 = await vision_service.capture_page_screenshot(target_html)

                                if screenshot_b64:
                                    # Save screenshot for debug
                                    screenshots_dir = os.path.join(get_data_dir(), "screenshots")
                                    os.makedirs(screenshots_dir, exist_ok=True)
                                    # Save image file
                                    import base64
                                    img_data = base64.b64decode(screenshot_b64)
                                    screenshot_filename = f"{project_name}_{int(time.time())}.png"
                                    screenshot_path = os.path.join(screenshots_dir, screenshot_filename)
                                    with open(screenshot_path, "wb") as f:
                                        f.write(img_data)

                                    # Analyze
                                    analysis = await vision_service.analyze_visuals(
                                        image_data=screenshot_b64,
                                        context=f"Project: {project_name}. File: {os.path.basename(target_html)}"
                                    )

                                    if analysis.get("status") == "FAIL":
                                        logger.warning(f"Visual Audit Failed for {project_name}: {analysis.get('issues')}")

                                        # Create Insight
                                        if learning_agent:
                                            from ai_assistant.core.reflection import ActionableInsight, InsightType
                                            # Ensure NotificationType is available if needed, though it is imported globally
                                            from ai_assistant.core.notification_manager import NotificationType

                                            insight = ActionableInsight(
                                                type=InsightType.VISUAL_DEFECT_DETECTED,
                                                description=f"Visual Audit failed for project '{project_name}'.",
                                                source_reflection_entry_ids=[],
                                                related_tool_name="vision_service",
                                                priority=8,
                                                status="NEW",
                                                metadata={
                                                    "project_name": project_name,
                                                    "screenshot_path": screenshot_path,
                                                    "issues": analysis.get("issues", []),
                                                    "suggestion": analysis.get("suggestion")
                                                }
                                            )
                                            learning_agent.insights.append(insight)
                                            learning_agent._save_insights()

                                            # Notify
                                            if learning_agent.notification_manager:
                                                learning_agent.notification_manager.add_notification(
                                                    event_type=NotificationType.SYSTEM_ALERT,
                                                    summary_message=f"Visual Audit Alert: {project_name} has UI defects.",
                                                    details_payload={
                                                        "title": "Visual Audit Failed",
                                                        "issues": analysis.get("issues")
                                                    }
                                                )
                                else:
                                    logger.warning(f"BackgroundService: Failed to capture screenshot for {project_name}")
            except Exception as e:
                logger.error(f"BackgroundService: Error during Visual Audit: {e}", exc_info=True)

            _last_visual_audit_time = time.time()
            next_visual_audit_run_time = time.time() + _visual_audit_interval_seconds

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
            try:
                # Restore Autonomous Self-Healing
                # The user can still intervene via the UI because we expose 'NEW' insights
                # via the API. If the loop picks it up first, it just becomes 'ACTION_ATTEMPTED'.
                processed_count = await learning_agent.process_self_healing_insights()
                if processed_count > 0:
                    logger.info(f"BackgroundService: Autonomously processed {processed_count} self-healing insights.")
            except Exception as e:
                 logger.error(f"BackgroundService: Error during self-healing cycle: {e}", exc_info=True)
            
            next_self_healing_run_time = time.time() + _self_healing_interval_seconds

        # --- Evolutionary Architect Audit Task ---
        if current_loop_time >= next_architect_audit_run_time:
             logger.info(f"BackgroundService: Running Evolutionary Architect Audit...")
             try:
                 proposal = await perform_architectural_audit()
                 if proposal and learning_agent and learning_agent.notification_manager:
                     summary = proposal.get('proposal', {}).get('summary', 'No summary provided')
                     target_file = proposal.get('target_file', 'unknown file')

                     async def _apply_proposal(p=proposal):
                         await learning_agent.execute_architect_proposal(p)

                     approval_manager.add_request(
                         req_type="architect_proposal",
                         data=proposal,
                         description=f"Architect Proposal for {os.path.basename(target_file)}: {summary}",
                         execute_func=_apply_proposal
                     )
                     logger.info(f"BackgroundService: Queued evolution proposal for {target_file}")
                 elif not proposal:
                     logger.info("BackgroundService: No proposal generated during audit.")

                 _last_architect_audit_timestamp = time.time()
                 _save_architect_state()
                 next_architect_audit_run_time = time.time() + _architect_audit_interval_seconds

             except Exception as e:
                 logger.error(f"BackgroundService: Error during Evolutionary Architect audit: {e}", exc_info=True)
                 # Retry later to avoid rapid error loop
                 next_architect_audit_run_time = time.time() + 3600

        # --- DREAM MODE (Autonomous Deep Simulation) ---
        global _last_dream_time, _dream_interval_seconds
        if '_last_dream_time' not in globals(): _last_dream_time = 0.0
        if '_dream_interval_seconds' not in globals(): _dream_interval_seconds = 300 # 5 minutes

        if current_loop_time >= _last_dream_time + _dream_interval_seconds:
            logger.info("BackgroundService: Entering Dream Mode...")
            try:
                # Lazy init Dreamer
                if 'dreamer_agent' not in locals():
                    from ai_assistant.dreaming.dreamer import DreamerAgent
                    dreamer_agent = DreamerAgent()
                
                # Pick a random tool
                available_tools = tool_system.tool_system_instance.list_tools()
                if available_tools:
                    import random
                    target_tool = random.choice(list(available_tools.keys()))
                    
                    logger.info(f"BackgroundService: Dreaming about '{target_tool}'...")
                    dream_result = await dreamer_agent.realize_dream(target_tool)
                    
                    if dream_result and "verification_script" in dream_result:
                        # Execute the Dream
                        script_content = dream_result["verification_script"]
                        logger.info(f"BackgroundService: Running verification for dream '{dream_result.get('scenario_name')}'")
                        
                        # Save to temp file
                        import tempfile
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp_script:
                            tmp_script.write(script_content)
                            tmp_script_path = tmp_script.name
                        
                        # Run it
                        proc = await asyncio.create_subprocess_exec(
                            sys.executable, tmp_script_path,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        stdout, stderr = await proc.communicate()
                        stdout_str = stdout.decode().strip()
                        stderr_str = stderr.decode().strip()
                        
                        # Cleanup
                        try: os.unlink(tmp_script_path)
                        except: pass
                        
                        # Analyze Result
                        if "DREAM_CRASH_DETECTED" in stdout_str or proc.returncode != 0:
                            logger.warning(f"BackgroundService: Nightmare realized! Tool '{target_tool}' failed hypothetical scenario.")
                            
                            # ACTIVE IMMUNE SYSTEM: Attempt to fix
                            if learning_agent and learning_agent.action_executor and learning_agent.action_executor.code_service:
                                logger.info(f"BackgroundService: Initiating Autonomous Immune Response for '{target_tool}'...")

                                # 1. Generate Fix
                                tool_info = tool_system.tool_system_instance.get_tool(target_tool)
                                if tool_info:
                                    module_path = tool_info.get("module_path")
                                    function_name = tool_info.get("function_name")

                                    # Use CodeService with Parallel Mode
                                    fix_result = await learning_agent.action_executor.code_service.modify_code(
                                        context="SELF_FIX_TOOL",
                                        modification_instruction=f"Fix the following crash detected during dream simulation: {stdout_str}\nStderr: {stderr_str}",
                                        module_path=module_path,
                                        function_name=function_name,
                                        llm_config={"task_name": "code_generation"} # Parallel Thinking
                                    )

                                    if fix_result.get("status") == "SUCCESS_CODE_GENERATED":
                                        suggested_code = fix_result.get("modified_code_string")

                                        # 2. Verify Fix (Ephemeral Test)
                                        # We need to temporarily apply the code to the file on disk to run the verification script
                                        # This is risky, but we use a backup. Or we can write the modified code to a temp file?
                                        # The verification script imports the module. So we must modify the module.

                                        from ai_assistant.core import self_modification
                                        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

                                        # Backup original code
                                        original_code = self_modification.get_function_source_code(module_path, function_name)

                                        # Apply Fix
                                        apply_msg = await self_modification.edit_function_source_code(
                                            module_path=module_path,
                                            function_name=function_name,
                                            new_code_string=suggested_code,
                                            project_root_path=project_root,
                                            change_description="Temporary application for Immune System verification.",
                                            task_manager=tm, # Reuse task manager
                                        )

                                        if "success" in apply_msg.lower():
                                            # Re-run Verification
                                            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp_verify:
                                                tmp_verify.write(script_content)
                                                tmp_verify_path = tmp_verify.name

                                            proc_verify = await asyncio.create_subprocess_exec(
                                                sys.executable, tmp_verify_path,
                                                stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE
                                            )
                                            stdout_v, stderr_v = await proc_verify.communicate()
                                            stdout_str_v = stdout_v.decode().strip()

                                            try: os.unlink(tmp_verify_path)
                                            except: pass

                                            # Revert Code immediately
                                            await self_modification.edit_function_source_code(
                                                module_path=module_path,
                                                function_name=function_name,
                                                new_code_string=original_code,
                                                project_root_path=project_root,
                                                change_description="Reverting Immune System temporary fix.",
                                                task_manager=tm
                                            )

                                            # Check Verification Result
                                            if "DREAM_SURVIVED" in stdout_str_v:
                                                 logger.info(f"BackgroundService: Immune Response Successful! Fix verified for '{target_tool}'.")

                                                 # 3. Submit Proposal
                                                 approval_manager.add_request(
                                                     req_type="tool_modification", # or PROPOSE_TOOL_MODIFICATION if handled
                                                     data={
                                                         "module_path": module_path,
                                                         "function_name": function_name,
                                                         "tool_name": target_tool,
                                                         "suggested_code_change": suggested_code,
                                                         "suggested_change_description": f"Autonomous Immune Response: Detected crash in '{target_tool}' during dream simulation. Generated and VERIFIED a fix."
                                                     },
                                                     description=f"Autonomous Immune Response: Fix for '{target_tool}' (Verified)",
                                                     # We need a callback to actually apply it permanently if approved.
                                                     # ActionExecutor can handle this via PROPOSE_TOOL_MODIFICATION logic usually.
                                                     # For now, let's assume the approval manager or UI handles tool_modification requests.
                                                     # Or we can use the LearningAgent's way.
                                                     execute_func=lambda: learning_agent.action_executor.execute_action({
                                                         "action_type": "PROPOSE_TOOL_MODIFICATION",
                                                         "details": {
                                                             "module_path": module_path,
                                                             "function_name": function_name,
                                                             "tool_name": target_tool,
                                                             "suggested_code_change": suggested_code,
                                                             "suggested_change_description": f"Autonomous Immune Response: Detected crash in '{target_tool}' during dream simulation. Generated and VERIFIED a fix.",
                                                             "staging_mode": False # It's already verified
                                                         }
                                                     })
                                                 )
                                            else:
                                                 logger.warning(f"BackgroundService: Immune Response failed verification. Fix did not survive dream.")
                                        else:
                                             logger.error(f"BackgroundService: Failed to apply temporary fix: {apply_msg}")
                                    else:
                                         logger.error(f"BackgroundService: Failed to generate fix: {fix_result.get('error')}")

                            # Create Insight (Still log it as backup)
                            if learning_agent:
                                from ai_assistant.core.reflection import ActionableInsight, InsightType
                                new_insight = ActionableInsight(
                                    type=InsightType.HYPOTHETICAL_SCENARIO,
                                    description=f"Dream Scenario '{dream_result.get('scenario_name')}' failed for tool '{target_tool}'.\nFailure Output: {stdout_str}\nStderr: {stderr_str}",
                                    source_reflection_entry_ids=[],
                                    related_tool_name=target_tool,
                                    priority=5,
                                    status="NEW",
                                    metadata={
                                        "dream_scenario": dream_result,
                                        "failure_output": stdout_str
                                    }
                                )
                                learning_agent.insights.append(new_insight)
                                learning_agent._save_insights()
                                logger.info("BackgroundService: Saved HYPOTHETICAL_SCENARIO insight.")

                        else:
                            logger.info(f"BackgroundService: Tool '{target_tool}' survived the dream scenario.")
                    
            except Exception as e:
                logger.error(f"BackgroundService: Error during Dream Mode: {e}", exc_info=True)
                # Capture general dream mode errors as insights
                if learning_agent:
                    from ai_assistant.core.reflection import ActionableInsight, InsightType
                    new_insight = ActionableInsight(
                        type=InsightType.TOOL_BUG_SUSPECTED,
                        description=f"Error encountered during Dream Mode execution: {str(e)}",
                        source_reflection_entry_ids=[],
                        related_tool_name="dream_mode_runner",
                        priority=4,
                        status="NEW",
                        metadata={
                            "error_category": "DREAM_MODE_SYSTEM_ERROR",
                            "exception_details": str(e)
                        }
                    )
                    learning_agent.insights.append(new_insight)
                    learning_agent._save_insights()
                    logger.info("BackgroundService: Saved DREAM_MODE_SYSTEM_ERROR insight.")

            
            _last_dream_time = time.time()

        # --- Auto-Approval Task ---
        if current_loop_time >= next_auto_approve_check_time:
             try:
                 pending_requests = approval_manager.get_pending_requests()
                 if pending_requests:
                     logger.info(f"BackgroundService: Checking {len(pending_requests)} pending requests for auto-approval (Timeout: {AUTO_APPROVE_DELAY_SECONDS}s).")
                     
                     for req in pending_requests:
                         req_id = req['id']
                         req_time = req['timestamp']
                         age = current_loop_time - req_time
                         
                         if age >= AUTO_APPROVE_DELAY_SECONDS:
                             logger.info(f"BackgroundService: Evaluating request {req_id} for auto-approval (Age: {age:.1f}s).")
                             
                             # AI Review Step
                             eval_result = await reviewer_agent.evaluate_auto_approval_request(
                                 request_type=req.get('type'),
                                 description=req.get('description'),
                                 request_data=req.get('data')
                             )
                             
                             logger.info(f"BackgroundService: AI Gatekeeper decision for {req_id}: {eval_result['status'].upper()} (Safety: {eval_result['safety_score']}, Opt: {eval_result['optimization_score']})")

                             if eval_result['status'] == 'approved':
                                 # Use NotificationManager to inform user of autonomous action
                                 if learning_agent and learning_agent.notification_manager:
                                     learning_agent.notification_manager.add_notification(
                                        event_type=NotificationType.SYSTEM_ALERT,
                                        summary_message=f"Auto-Approved: I executed '{req.get('description')}' because it passed all Gatekeeper checks (Safety: {eval_result['safety_score']}/10).",
                                        details_payload={
                                            "title": "Auto-Approved Action",
                                            "priority": "normal",
                                            "gatekeeper_result": eval_result
                                        }
                                     )

                                 approval_success = await approval_manager.approve_request(req_id)
                                 if approval_success:
                                     logger.info(f"BackgroundService: Successfully auto-executed request {req_id}.")
                                 else:
                                     logger.error(f"BackgroundService: Failed to auto-execute request {req_id}.")
                             
                             else:
                                 # Auto-Deny
                                 if learning_agent and learning_agent.notification_manager:
                                     learning_agent.notification_manager.add_notification(
                                         event_type=NotificationType.SYSTEM_ALERT,
                                         summary_message=f"Auto-Denied: I rejected '{req.get('description')}'. Reason: {eval_result['reason']}",
                                         details_payload={
                                             "title": "Auto-Rejected Action",
                                             "priority": "normal",
                                             "reason": eval_result['reason']
                                         }
                                     )
                                 approval_manager.deny_request(req_id)
                                 logger.info(f"BackgroundService: Auto-denied request {req_id}. Reason: {eval_result['reason']}")
                         
             except Exception as e:
                 logger.error(f"BackgroundService: Error during Auto-Approval check: {e}", exc_info=True)
             
             next_auto_approve_check_time = time.time() + _auto_approve_check_interval_seconds
        
        # --- 6. Conversational Analysis ---
        global _last_conversation_analysis_time, _conversation_analysis_interval_seconds
        
        if '_last_conversation_analysis_time' not in globals():
             _last_conversation_analysis_time = 0.0
        if '_conversation_analysis_interval_seconds' not in globals():
             _conversation_analysis_interval_seconds = 1800 # 30 minutes

        if current_loop_time - _last_conversation_analysis_time >= _conversation_analysis_interval_seconds:
            _last_conversation_analysis_time = current_loop_time
            if learning_agent:
                logger.info("BackgroundService: Running Conversational Analysis...")
                try:
                    num_insights = await learning_agent.scan_recent_conversations()
                    if num_insights > 0:
                        logger.info(f"BackgroundService: Conversational Analysis found {num_insights} new insights.")
                except Exception as e:
                    logger.error(f"BackgroundService: Error during conversational analysis: {e}")

        # Determine sleep time until the next event

        time_until_next_reflection = max(0, next_reflection_run_time - time.time())
        time_until_next_curation = max(0, next_fact_curation_run_time - time.time())
        time_until_next_project_exec = max(0, next_project_execution_run_time - time.time()) if PROJECT_TOOLS_AVAILABLE else float('inf')
        time_until_next_healing = max(0, next_self_healing_run_time - time.time()) if learning_agent else float('inf')
        time_until_next_audit = max(0, next_architect_audit_run_time - time.time())
        time_until_next_auto_approve = max(0, next_auto_approve_check_time - time.time())
        time_until_next_visual_audit = max(0, next_visual_audit_run_time - time.time())
        time_until_next_goal_check = max(0, next_autonomous_goal_check_time - time.time())
        
        sleep_duration = min(time_until_next_reflection, time_until_next_curation, time_until_next_project_exec, time_until_next_healing, time_until_next_audit, time_until_next_auto_approve, time_until_next_visual_audit, time_until_next_goal_check, 10)

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