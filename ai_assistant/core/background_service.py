### START FILE: core/background_service.py ###
# ai_assistant/core/background_service.py
import asyncio
import time
import json
import os # Added
import sys # Added for subprocess execution

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

from ai_assistant.core.autonomous_reflection import run_self_reflection_cycle
from ai_assistant.core.reflection import global_reflection_log # Import global log for timestamp check
from ai_assistant.tools import tool_system # To get available tools
# Modified: Import the specific curation function and config for interval
from ai_assistant.custom_tools.knowledge_tools import run_periodic_fact_store_curation_async
from ai_assistant.memory.persistent_memory import LEARNED_FACTS_FILEPATH # Import constant for dirty check
from ai_assistant.config import is_debug_mode, FACT_CURATION_INTERVAL_SECONDS
import ai_assistant.config as runtime_config
# Added for self-healing
from ai_assistant.learning.learning import LearningAgent
from ai_assistant.core.task_manager import TaskManager
from ai_assistant.core.notification_manager import NotificationManager
from ai_assistant.core.approval_manager import approval_manager
from ai_assistant.core.memory_maintenance_service import MemoryMaintenanceService # Added


# Added for Evolutionary Architect
from ai_assistant.learning.evolutionary_architect import perform_architectural_audit
from ai_assistant.config import get_data_dir, AUTO_APPROVE_DELAY_SECONDS
from ai_assistant.core.reviewer import ReviewerAgent

# Import goal management for autonomous goal processing
from ai_assistant.goals import goal_management
# Fix: Import NotificationType to avoid NameError in autonomous goal processor
from ai_assistant.core.notification_manager import NotificationType

# Import Reminder Tool
try:
    from ai_assistant.custom_tools.reminder_tool import check_due_reminders
except ImportError:
    check_due_reminders = None
    logger.warning("BackgroundService: Could not import reminder_tool.")

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
_polling_interval_seconds = 3600  # Self-reflection: 1 hour
_last_fact_curation_time: float = 0.0
_last_project_execution_scan_time: float = 0.0
_last_reflection_analyzed_timestamp: float = 0.0
_last_self_healing_time: float = 0.0
_self_healing_interval_seconds = 3600 # Self-healing: 1 hour (was 10 mins)
_last_architect_audit_timestamp: float = 0.0
_architect_audit_interval_seconds = 7200 # Architect: 2 hours (was 15 mins)
ARCHITECT_STATE_FILE = "architect_state.json"

_last_auto_approve_check_time: float = 0.0
_auto_approve_check_interval_seconds = 60 # Keep frequent for responsiveness

# Memory Maintenance State
_last_memory_maintenance_time: float = 0.0
_memory_maintenance_interval_seconds = 3600 # 1 hour (was 30 mins)

# Learning & Conversation Scan State
_last_conversation_scan_time: float = 0.0
_conversation_scan_interval_seconds = 1800 # 30 mins (was 10 mins)


def _format_exception_details(error: Exception) -> str:
    detail = str(error).strip()
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__


def _is_dream_provider_failure(error: Exception) -> bool:
    """Return True for LLM/network failures that must not become tool-fix approvals."""
    current: Optional[BaseException] = error
    while current:
        class_name = type(current).__name__.casefold()
        module_name = type(current).__module__.casefold()
        detail = str(current).casefold()
        if (
            "deepseek" in class_name
            or "deepseek" in module_name
            or "gemini" in class_name
            or "timeout" in class_name
            or "timed out" in detail
            or "client error" in detail
            or "rate limit" in detail
            or "connection" in detail
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _record_dream_mode_failure(
    learning_agent: Optional[LearningAgent],
    target_tool: Optional[str],
    error: Exception,
) -> Optional[object]:
    """Record a failed dream run as diagnostics, never as an unverified tool repair."""
    details = _format_exception_details(error)
    provider_failure = _is_dream_provider_failure(error)
    failure_scope = "provider" if provider_failure else "dream_runner"
    selected_tool = str(target_tool or "").strip() or None

    if not learning_agent:
        return None

    from ai_assistant.core.reflection import ActionableInsight, InsightType
    insight = ActionableInsight(
        type=InsightType.DREAM_EXPERIMENT,
        description=(
            f"Dream Mode {failure_scope} failure"
            + (f" while evaluating '{selected_tool}'" if selected_tool else "")
            + f": {details}"
        ),
        source_reflection_entry_ids=[],
        related_tool_name=selected_tool,
        priority=5,
        status="ACTION_FAILED",
        metadata={
            "error_category": "DREAM_MODE_PROVIDER_ERROR" if provider_failure else "DREAM_MODE_RUNNER_ERROR",
            "exception_details": details,
            "failure_scope": failure_scope,
            "repairable_tool_failure": False,
            "selected_tool": selected_tool,
        },
    )
    learning_agent.insights.append(insight)
    learning_agent._save_insights()

    notification_manager = getattr(learning_agent, "notification_manager", None)
    if notification_manager:
        notification_manager.add_notification(
            NotificationType.WARNING,
            insight.description,
            related_item_id=insight.insight_id,
            related_item_type="dream_experiment",
            details_payload=insight.metadata,
        )
    return insight

# Vision Service State
_last_visual_audit_time: float = 0.0
_visual_audit_interval_seconds = 3600 # 1 hour (was 15 mins)

# Orchestrator Injection
_orchestrator = None

# Autonomous Goal Processing State
_last_autonomous_goal_check_time: float = 0.0
_autonomous_goal_check_interval_seconds = 30 # Check every 30 seconds
MAX_CONCURRENT_AUTONOMOUS_GOALS = 2
AUTONOMOUS_GOAL_CLAIM_TTL_SECONDS = 300
_last_agenda_briefing_date: Optional[str] = None # For Daily Briefing

# Reminder System State
_last_reminder_check_time: float = 0.0
_reminder_check_interval_seconds = getattr(runtime_config, "REMINDER_CHECK_INTERVAL_SECONDS", 10) # Check frequently

# Detailed Status Trackers
_last_dream_status: str = "No dreams realized yet."
_last_architect_status: str = "No architectural audits performed yet."

# --- User Activity Beacon ---
_last_user_activity_ts: float = 0.0
# Default threshold: 5 minutes (300 seconds)
BACKGROUND_IDLE_THRESHOLD_SECONDS = 300
# Deep Sleep threshold: 1 hour (3600 seconds)
DEEP_SLEEP_THRESHOLD_SECONDS = 3600

def report_user_activity():
    """
    Called by the UI/API to indicate the user is active.
    Resets the idle timer, pausing heavy background tasks.
    """
    global _last_user_activity_ts
    _last_user_activity_ts = time.time()

def is_user_active(threshold: int = BACKGROUND_IDLE_THRESHOLD_SECONDS) -> bool:
    """Checks if the user has been active recently."""
    return (time.time() - _last_user_activity_ts) < threshold

def is_deep_sleep_active() -> bool:
    """Checks if the system should be in deep sleep (no polling)."""
    return (time.time() - _last_user_activity_ts) > DEEP_SLEEP_THRESHOLD_SECONDS

def set_orchestrator(orchestrator_instance):
    """Sets the orchestrator instance for autonomous goal processing."""
    global _orchestrator
    _orchestrator = orchestrator_instance

    logger.info("BackgroundService: Orchestrator instance set.")

def _requires_manual_source_approval(request_type: str) -> bool:
    """Returns whether an approval-manager request must wait for a human action."""
    return request_type in {
        "architect_proposal",
    }

# Broadcaster for Chat Messages
_socket_broadcaster = None

def set_socket_broadcaster(broadcaster_func):
    """Sets the function to broadcast messages to the UI via WebSockets."""
    global _socket_broadcaster
    _socket_broadcaster = broadcaster_func
    logger.info("BackgroundService: Socket broadcaster set.")

async def broadcast_agent_message(session_id: str, message: str, title: str = "Agent Report"):
    """Sends an agent completion report to the latest active chat session."""
    try:
        import app_globals
        from ai_assistant.core.chat_manager import ChatSessionManager

        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        chat_storage = os.path.join(base_dir, "_memory_", "chat_sessions")
        cm = ChatSessionManager(chat_storage)
        source_session_id = session_id
        active_session_id = getattr(app_globals, "latest_active_chat_session_id", None)
        target_session_id = source_session_id

        if active_session_id:
            try:
                if cm.get_session(active_session_id):
                    target_session_id = active_session_id
            except Exception:
                logger.warning("BackgroundService: Could not validate active session %s", active_session_id)

        if not target_session_id:
            target_session_id = active_session_id or source_session_id

        if not target_session_id:
            sessions = cm.list_sessions()
            if sessions:
                target_session_id = sessions[0].get("id")
            else:
                target_session_id = cm.create_session(title="System Updates")

        body = message
        if source_session_id and target_session_id != source_session_id:
            body = (
                "Earlier background agent task completed. It started in another chat, "
                "so I am posting the result in your active chat.\n\n"
                f"{message}"
            )

        formatted_msg = f"**{title}**\n\n{body}"
        cm.add_message(target_session_id, "assistant", formatted_msg)

        if _socket_broadcaster:
            # 2. Emit to UI
            await _socket_broadcaster("agent_message", {
                "session_id": target_session_id,
                "source_session_id": source_session_id,
                "role": "assistant",
                "content": formatted_msg,
                "timestamp": time.time()
            })
        logger.info(
            "BackgroundService: Delivered agent message to %s (source session: %s)",
            target_session_id,
            source_session_id,
        )
    except Exception as e:
        logger.error(f"BackgroundService: Failed to deliver agent message: {e}")

async def broadcast_scheduled_message(message: str, title: str = "Scheduled Task"):
    """Sends a scheduled job/reminder report to the active chat."""
    await broadcast_agent_message(None, message, title=title)

async def check_and_broadcast_due_reminders(notification_manager=None) -> int:
    """Checks due reminders and reports them through notifications and active chat."""
    if not check_due_reminders:
        return 0

    due_reminders = check_due_reminders()
    if not due_reminders:
        return 0

    logger.info(f"BackgroundService: Found {len(due_reminders)} due reminders.")
    for rem in due_reminders:
        message = rem.get("message", "")
        target_time = rem.get("target_time", "")
        logger.info(f"BackgroundService: Firing reminder: {message}")
        if notification_manager:
            notification_manager.add_notification(
                event_type=NotificationType.SYSTEM_ALERT,
                summary_message=f"REMINDER: {message}",
                details_payload={
                    "title": "Scheduled Reminder",
                    "message": message,
                    "scheduled_for": target_time,
                },
            )
        await broadcast_scheduled_message(
            f"Reminder due: {message}\n\nScheduled for: {target_time}",
            title="Scheduled Reminder",
        )

    return len(due_reminders)

def get_service_status():
    """Returns the current status of the background service."""
    _uptime_seconds = 0
    if _background_service_active: # We don't track start time explicitly yet, but could.
        # Estimate from loop
        pass

    return {
        "is_active": _background_service_active,
        "has_orchestrator": _orchestrator is not None,
        "has_broadcaster": _socket_broadcaster is not None,
        "last_reflection_timestamp": _last_reflection_analyzed_timestamp if '_last_reflection_analyzed_timestamp' in globals() else 0,
        "last_fact_curation_timestamp": _last_fact_curation_time,
        "last_project_execution_scan_timestamp": _last_project_execution_scan_time,
        "last_self_healing_timestamp": _last_self_healing_time,
        "last_architect_audit_timestamp": _last_architect_audit_timestamp,
        "last_dream_timestamp": _last_dream_time if '_last_dream_time' in globals() else 0,
        "last_visual_audit_timestamp": _last_visual_audit_time,
        "autonomous_learning_enabled": globals().get('AUTONOMOUS_LEARNING_ENABLED', False)
    }

def get_background_activity_report() -> str:
    """
    Returns a human-readable report of the background service's status and recent activities.
    """
    if not _background_service_active:
        return "Background Service: INACTIVE (Processes are not running)"

    now = time.time()
    report = ["Background Service: ACTIVE (Running autonomous loops)"]

    def fmt_time(t):
        if t == 0: return "Never"
        diff = int(now - t)
        if diff < 60: return f"{diff}s ago"
        if diff < 3600: return f"{diff//60}m ago"
        return f"{diff//3600}h ago"

    # Dream Mode
    dream_time = globals().get('_last_dream_time', 0)
    dream_stat = globals().get('_last_dream_status', 'No data')
    report.append(f"- Dreamer (Simulation): Last run {fmt_time(dream_time)}. Status: {dream_stat}")

    # Evolutionary Architect
    arch_time = globals().get('_last_architect_audit_timestamp', 0)
    arch_stat = globals().get('_last_architect_status', 'No data')
    report.append(f"- Evolutionary Architect: Last audit {fmt_time(arch_time)}. Status: {arch_stat}")

    # Self-Healing
    heal_time = globals().get('_last_self_healing_time', 0)
    report.append(f"- Self-Healing (Immune System): Last scan {fmt_time(heal_time)}")

    # Visual Audit
    vis_time = globals().get('_last_visual_audit_time', 0)
    report.append(f"- Visual Audit: Last scan {fmt_time(vis_time)}")

    # Auto-Approval
    auto_time = globals().get('_last_auto_approve_check_time', 0)
    report.append(f"- Auto-Approval Gatekeeper: Last check {fmt_time(auto_time)}")

    # Autonomous Goals
    goal_time = globals().get('_last_autonomous_goal_check_time', 0)
    report.append(f"- Autonomous Goal Processor: Last check {fmt_time(goal_time)}")

    # User Activity
    last_act = globals().get('_last_user_activity_ts', 0)
    is_active = (now - last_act) < BACKGROUND_IDLE_THRESHOLD_SECONDS
    status_str = "ACTIVE (Pausing heavy tasks)" if is_active else "IDLE (Heavy tasks enabled)"
    report.append(f"- User Activity: Last detected {fmt_time(last_act)}. Status: {status_str}")

    return "\n".join(report)

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
        running_count = len(goal_management.list_goals(status="in_progress"))
        available_slots = max(0, MAX_CONCURRENT_AUTONOMOUS_GOALS - running_count)

        if pending_goals and available_slots:
            logger.info(f"BackgroundService: Found {len(pending_goals)} pending goals. Starting up to {available_slots}.")

            # Explicit agent launches should not wait behind maintenance proposals.
            pending_goals.sort(key=lambda goal: goal.get("metadata", {}).get("type") != "background_agent")
            for goal in pending_goals[:available_slots]:
                goal_id = goal.get("id")
                goal_desc = goal.get("description")

                if not goal_id or not goal_desc:
                    logger.warning(f"BackgroundService: Skipping invalid goal structure: {goal}")
                    continue

                if not _claim_autonomous_goal(goal_id):
                    logger.info(f"BackgroundService: Goal {goal_id} is already claimed by another worker.")
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

                    # POST-EXECUTION REPORTING
                    # We need to know the result. orchestrator.process_prompt returns (success, response, images)
                    # Use a callback or wait?
                    # create_task wraps it. We can define a wrapper.
                    async def _run_and_report(gid=goal_id, sess_id=session_id, desc=goal_desc, source_sess=goal.get("metadata", {}).get("source_session_id")):
                        try:
                            from ai_assistant.core.models.state import ExecutionState
                            state = ExecutionState(original_user_prompt=desc, context_limits={"max_tokens": 100000})
                            state = await _orchestrator.process_prompt(
                                state=state,
                                session_id=sess_id,
                                context_source="SYSTEM" # Use SYSTEM personas
                            )

                            success = state.current_status == "completed"
                            result_text = ""
                            if state.tool_results and len(state.tool_results) > 0:
                                last_result = state.tool_results[-1]
                                if last_result.get("action_name") == "orchestrator_final_answer":
                                    result_text = last_result.get("result", "")

                            if not success and not result_text:
                                result_text = "Task encountered errors:\n" + "\n".join(state.errors)

                            # Update Goal Status and preserve the truthful terminal result.
                            new_status = "completed" if success else "failed"
                            goal_management.record_goal_result(gid, status=new_status, result_summary=result_text)

                            # Report back to source session if exists
                            if source_sess:
                                report_title = f"Agent Report: {desc[:30]}..."
                                report_body = result_text if success else f"Agent failed to complete task: {result_text}"
                                await broadcast_agent_message(source_sess, report_body, title=report_title)

                            # NEW: Trigger Learning from this experience
                            # If successful, extract facts from the result
                            if success and hasattr(_orchestrator, 'learning_agent') and _orchestrator.learning_agent:
                                logger.info(f"BackgroundService: Triggering learning extraction for goal {gid}...")
                                # Fire and forget (or await if we want to ensure it completes before goal update?
                                # goal is already updated. Awaiting is safer to managing loop).
                                await _orchestrator.learning_agent.extract_and_save_facts(
                                    text=result_text,
                                    source_desc=f"Background Agent Task: {desc}"
                                )

                        except Exception as e:
                            logger.error(f"Error in autonomous wrapper for goal {gid}: {e}")
                            result_text = f"Background agent failed unexpectedly: {e}"
                            goal_management.record_goal_result(gid, status="failed", result_summary=result_text)
                            if source_sess:
                                await broadcast_agent_message(
                                    source_sess,
                                    result_text,
                                    title=f"Agent Report: {desc[:30]}..."
                                )
                        finally:
                            _release_autonomous_goal_claim(gid)

                    asyncio.create_task(_run_and_report())

                else:
                    _release_autonomous_goal_claim(goal_id)
                    logger.error(f"BackgroundService: Failed to update status for goal {goal_id}. Execution aborted to avoid loops.")

    except Exception as e:
        logger.error(f"BackgroundService: Error in autonomous goal processor: {e}", exc_info=True)

def should_run_autonomous_goal_processor(current_loop_time: float, next_check_time: float) -> bool:
    """Runs queued user work promptly even when background maintenance is sleeping."""
    return bool(goal_management.list_goals(status="pending")) or current_loop_time >= next_check_time

def _goal_claim_path(goal_id: str) -> str:
    claim_dir = os.path.join(get_data_dir(), "autonomous_goal_claims")
    os.makedirs(claim_dir, exist_ok=True)
    return os.path.join(claim_dir, f"{goal_id}.lock")

def _claim_autonomous_goal(goal_id: str) -> bool:
    """Claims a queued goal across app processes using an atomic lock-file create."""
    claim_path = _goal_claim_path(goal_id)
    try:
        fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(claim_path) <= AUTONOMOUS_GOAL_CLAIM_TTL_SECONDS:
                return False
            os.remove(claim_path)
        except FileNotFoundError:
            pass
        return _claim_autonomous_goal(goal_id)

    with os.fdopen(fd, "w", encoding="utf-8") as claim_file:
        claim_file.write(str(os.getpid()))
    return True

def _release_autonomous_goal_claim(goal_id: str) -> None:
    """Releases an autonomous goal claim after terminal reporting."""
    try:
        os.remove(_goal_claim_path(goal_id))
    except FileNotFoundError:
        pass


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
            with open(state_file, 'r', encoding='utf-8') as f:
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
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump({"last_audit_timestamp": _last_architect_audit_timestamp}, f)
    except Exception as e:
        logger.error(f"BackgroundService: Failed to save architect state: {e}")

async def _background_loop_async():
    global _last_fact_curation_time, _last_project_execution_scan_time, _last_self_healing_time, _last_architect_audit_timestamp, _last_auto_approve_check_time, _last_visual_audit_time, _last_autonomous_goal_check_time, _last_reminder_check_time, _last_memory_maintenance_time, _last_conversation_scan_time
    print("BackgroundService: Async loop started.")
    _last_fact_curation_time = time.time()
    _last_project_execution_scan_time = time.time()
    _last_self_healing_time = time.time()
    _last_auto_approve_check_time = time.time()
    _last_visual_audit_time = time.time()
    _last_autonomous_goal_check_time = time.time()
    _last_reminder_check_time = time.time()
    _last_memory_maintenance_time = time.time()
    _last_conversation_scan_time = time.time()

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

    # Initialize Memory Maintenance Service
    memory_maintenance_service = MemoryMaintenanceService()

    # Pre-fetch modules to avoid repeated instantiation/imports inside the tight loop
    from ai_assistant.core.config_manager import ConfigManager
    cm = ConfigManager()

    # Try getting telemetry_tracker just once, log warning and set flag if it fails
    has_telemetry_tracker = False
    telemetry_tracker_ref = None
    try:
        from ai_assistant.core.telemetry import telemetry_tracker
        telemetry_tracker_ref = telemetry_tracker
        has_telemetry_tracker = True
    except ImportError:
        logger.warning("BackgroundService: ai_assistant.core.telemetry not found. Token budget checks disabled.")

    while _background_service_active:
        current_loop_time = time.time()

        # Refresh runtime-configurable background intervals.
        globals()['_reminder_check_interval_seconds'] = max(
            5,
            int(getattr(runtime_config, "REMINDER_CHECK_INTERVAL_SECONDS", globals().get('_reminder_check_interval_seconds', 10)))
        )
        globals()['_dream_interval_seconds'] = max(
            300,
            int(getattr(runtime_config, "DREAM_INTERVAL_SECONDS", globals().get('_dream_interval_seconds', 86400)))
        )

        # --- Autonomous Goal Processing Task ---
        # User-assigned work is not maintenance and must run before idle throttling.
        if should_run_autonomous_goal_processor(current_loop_time, next_autonomous_goal_check_time):
            await run_autonomous_goal_processor()
            _last_autonomous_goal_check_time = time.time()
            next_autonomous_goal_check_time = time.time() + _autonomous_goal_check_interval_seconds

        # --- Reminder System Task ---
        # User-scheduled reminders must still fire even when maintenance is in deep sleep.
        if check_due_reminders and current_loop_time >= _last_reminder_check_time + _reminder_check_interval_seconds:
            try:
                await check_and_broadcast_due_reminders(nm if 'nm' in locals() else None)
            except Exception as e:
                logger.error(f"BackgroundService: Error checking reminders: {e}")

            _last_reminder_check_time = time.time()

        # --- Deep Sleep Check ---
        if is_deep_sleep_active():
            if is_debug_mode():
                 logger.debug("BackgroundService: Deep Sleep Mode active. Skipping all background checks.")
            await asyncio.sleep(60) # Sleep for a minute before checking again
            continue

        # --- Memory Maintenance Task ---
        # NOW MOVED TO IDLE GATED SECTION
        pass

        # --- Daily Briefing (Agenda Check) ---
        current_date_str = time.strftime('%Y-%m-%d')
        global _last_agenda_briefing_date

        # Load last briefing date from file if not in memory (on first run)
        if _last_agenda_briefing_date is None:
            briefing_file_path = os.path.join(get_data_dir(), "last_agenda_briefing.txt")
            if os.path.exists(briefing_file_path):
                _last_agenda_briefing_date = read_text_from_file(briefing_file_path).strip()

        if _last_agenda_briefing_date != current_date_str:
            # It's a new day (or first run of the day)
            logger.info("BackgroundService: First run of the day detected. Attempting Daily Agenda Briefing...")
            try:
                # Import here to avoid circular dependencies if any
                from ai_assistant.integrations.google_calendar import CalendarManager
                from ai_assistant.core.reflection import ActionableInsight, InsightType

                cal_manager = CalendarManager()
                # Only proceed if authenticated (or can authenticate silently)
                # We don't want to pop up a browser in background thread unexpectedly,
                # but if tokens exist, it works.
                if os.path.exists(cal_manager.token_path):
                    agenda_text = cal_manager.get_day_agenda('today')

                    if agenda_text and "Error" not in agenda_text:
                        # Create Insight
                        if learning_agent:
                            briefing_insight = ActionableInsight(
                                type=InsightType.USER_FEEDBACK, # Using FEEDBACK as closest proxy for "System Info" regarding user
                                description=f"Daily Agenda Loaded: {agenda_text}",
                                source_reflection_entry_ids=[],
                                related_tool_name="google_calendar",
                                priority=5,
                                status="NEW",
                                metadata={
                                    "source": "daily_briefing",
                                    "date": current_date_str
                                }
                            )
                            learning_agent.insights.append(briefing_insight)
                            learning_agent._save_insights()

                            # Log success
                            logger.info(f"BackgroundService: Daily Briefing insight created for {current_date_str}.")

                            # Notify
                            if learning_agent.notification_manager:
                                learning_agent.notification_manager.add_notification(
                                    event_type=NotificationType.SYSTEM_ALERT,
                                    summary_message="Daily Briefing: Agenda loaded into context.",
                                    details_payload={"agenda": agenda_text}
                                )
                            await broadcast_scheduled_message(
                                agenda_text,
                                title="Daily Agenda Briefing",
                            )
                    else:
                        logger.info("BackgroundService: Daily Briefing - No agenda retrieved or error (likely not auth).")
                else:
                    logger.info("BackgroundService: Skipping Daily Briefing - Calendar not authenticated.")

            except ImportError:
                 logger.warning("BackgroundService: Could not import CalendarManager for Daily Briefing.")
            except Exception as e:
                logger.error(f"BackgroundService: Error during Daily Briefing: {e}")

            # Update state
            _last_agenda_briefing_date = current_date_str
            write_text_to_file(os.path.join(get_data_dir(), "last_agenda_briefing.txt"), current_date_str)

        # Check global daily token budget from telemetry
        if has_telemetry_tracker and telemetry_tracker_ref:
            try:
                usage = telemetry_tracker_ref.get_usage()
                # Fetch from pre-instantiated ConfigManager to ensure dynamic updates without loop instantiations
                all_settings = cm.get_all_settings()
                daily_limit = all_settings.get("DAILY_TOKEN_BUDGET", 2000000)

                if usage.get("total_tokens", 0) > daily_limit:
                    logger.warning(f"BackgroundService: Daily Token Budget ({daily_limit}) exceeded! Pausing all autonomous loops.")
                    await asyncio.sleep(60)
                    continue
            except Exception as e:
                logger.error(f"BackgroundService: Failed to check token budget: {e}")

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
                            logger.info(f"BackgroundService: Self-reflection cycle generated {len(suggestions)} suggestions. Routing to Weebo.")

                            from ai_assistant.core.suggestion_manager import add_new_suggestion, _update_suggestion_status

                            prompt_text = "SYSTEM: A background self-reflection cycle generated the following insights/suggestions for you to evaluate and handle autonomously:\n\n"

                            if learning_agent:
                                for i, suggestion in enumerate(suggestions):
                                    suggestion_desc = suggestion.get("suggestion_text", "No description")
                                    action_type = suggestion.get("action_type", "UNKNOWN")

                                    sugg_record = add_new_suggestion(
                                        type=action_type,
                                        description=suggestion_desc,
                                        action_details=suggestion.get("action_details", {})
                                    )

                                    if sugg_record:
                                        _update_suggestion_status(sugg_record['suggestion_id'], "ROUTED_TO_WEEBO", "Routed to autonomous loop.")

                                    prompt_text += f"{i+1}. [{action_type}] {suggestion_desc}\n"

                            prompt_text += "\nPlease use your tools to apply these changes or modifications if you determine they are beneficial. You do not need to ask for user permission."

                            if _orchestrator:
                                async def _weebo_process(prompt=prompt_text):
                                    try:
                                        from ai_assistant.core.models.state import ExecutionState
                                        import uuid
                                        session_id = f"autonomous_insight_{uuid.uuid4().hex[:8]}"
                                        state = ExecutionState(original_user_prompt=prompt, context_limits={"max_tokens": 100000})

                                        logger.info(f"BackgroundService: Triggering Orchestrator to process suggestions (Session: {session_id}).")
                                        await _orchestrator.process_prompt(
                                            state=state,
                                            session_id=session_id,
                                            context_source="SYSTEM"
                                        )
                                    except Exception as e:
                                        logger.error(f"BackgroundService: Error during autonomous insight processing: {e}", exc_info=True)

                                asyncio.create_task(_weebo_process())
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

        # --- Active Learning: Conversation Scan ---
        if learning_agent and current_loop_time >= _last_conversation_scan_time + _conversation_scan_interval_seconds:
            try:
                # 1. Scan for new insights
                new_insights = await learning_agent.scan_recent_conversations()

                # 2. Fast-track Facts
                if new_insights > 0:
                     processed = await learning_agent.process_learned_facts_immediately()
                     if processed > 0:
                         logger.info(f"BackgroundService: Fast-tracked {processed} learned facts from conversation.")
            except Exception as e:
                logger.error(f"BackgroundService: Error during conversation scan: {e}")

            _last_conversation_scan_time = time.time()

        # === IDLE GATED TASKS ===
        # The following tasks are "Heavy" and should pause if the user is active.
        user_is_idle = not is_user_active()

        ALLOW_DREAMER = getattr(runtime_config, "ALLOW_DREAMER", True)
        ALLOW_MEMORY_LEARNING = getattr(runtime_config, "ALLOW_MEMORY_LEARNING", True)
        ALLOW_AUTO_FIXING = getattr(runtime_config, "ALLOW_AUTO_FIXING", True)
        ALLOW_ARCHITECT = getattr(runtime_config, "ALLOW_ARCHITECT", False)

        # --- Visual Audit Task (Heavy) ---
        if ALLOW_AUTO_FIXING and user_is_idle and vision_service and current_loop_time >= next_visual_audit_run_time:
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
                                            # from ai_assistant.core.notification_manager import NotificationType

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

        # --- Memory Maintenance Task (Idle Gated) ---
        if ALLOW_MEMORY_LEARNING and user_is_idle and current_loop_time >= _last_memory_maintenance_time + _memory_maintenance_interval_seconds:
             try:
                logger.info("BackgroundService: User is idle. Running Memory Maintenance Cycle...")
                await memory_maintenance_service.run_maintenance_cycle()
             except Exception as e:
                logger.error(f"BackgroundService: Error during memory maintenance cycle: {e}", exc_info=True)

             _last_memory_maintenance_time = time.time()

        # --- Autonomous Project Coding Task (Heavy) ---
        if user_is_idle and PROJECT_TOOLS_AVAILABLE and current_loop_time >= next_project_execution_run_time:
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
                    logger.debug("[DEBUG BACKGROUND_SERVICE] No projects found with pending tasks in this scan.")

            except Exception as e: # pragma: no cover
                logger.error(f"BackgroundService: Error during autonomous project execution scan: {e}", exc_info=True)
            _last_project_execution_scan_time = time.time()
            next_project_execution_run_time = time.time() + PROJECT_EXECUTION_INTERVAL_SECONDS

        # --- Autonomous Self-Healing Task (Heavy) ---
        if user_is_idle and learning_agent and current_loop_time >= next_self_healing_run_time:
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

        # --- General Insight Processing (Learning - Always Run) ---
        # Checks for NEW insights (Frustrations, Preferences) and proposes actions
        if learning_agent and current_loop_time >= next_self_healing_run_time + 5: # Offset slightly from self-healing
             try:
                 # Process one insight per cycle to avoid flooding
                 await learning_agent.review_and_propose_next_action()
             except Exception as e:
                 logger.error(f"BackgroundService: Error during general insight processing: {e}", exc_info=True)


        # --- Evolutionary Architect Audit Task (Heavy) ---
        if ALLOW_ARCHITECT and user_is_idle and current_loop_time >= next_architect_audit_run_time:
             logger.info("BackgroundService: Running Evolutionary Architect Audit...")
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
                     globals()['_last_architect_status'] = f"Proposed changes for {os.path.basename(target_file)}: {summary}"
                 elif not proposal:
                     logger.info("BackgroundService: No proposal generated during audit.")
                     globals()['_last_architect_status'] = "Audit completed. No improvements proposed."

                 _last_architect_audit_timestamp = time.time()
                 _save_architect_state()
                 next_architect_audit_run_time = time.time() + _architect_audit_interval_seconds

             except Exception as e:
                 logger.error(f"BackgroundService: Error during Evolutionary Architect audit: {e}", exc_info=True)
                 # Retry later to avoid rapid error loop
                 next_architect_audit_run_time = time.time() + 3600

        # --- DREAM MODE (Autonomous Deep Simulation - Heavy) ---
        global _last_dream_time, _dream_interval_seconds
        if '_last_dream_time' not in globals(): _last_dream_time = 0.0
        if '_dream_interval_seconds' not in globals(): _dream_interval_seconds = getattr(runtime_config, 'DREAM_INTERVAL_SECONDS', 86400)

        # Check for explicit DREAM_MODE enable via config or env if needed
        ENABLE_DREAM_MODE = bool(getattr(runtime_config, "ENABLE_DREAM_MODE", False) or os.environ.get("ENABLE_DREAM_MODE", "False").lower() == "true")

        if ALLOW_DREAMER and ENABLE_DREAM_MODE and user_is_idle and current_loop_time >= _last_dream_time + _dream_interval_seconds:
            logger.info("BackgroundService: Entering Dream Mode...")
            target_tool = None
            dream_retry_delay_seconds = _dream_interval_seconds
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
                        except Exception: pass

                        # Analyze Result
                        if "DREAM_CRASH_DETECTED" in stdout_str or proc.returncode != 0:
                            logger.warning(f"BackgroundService: Nightmare realized! Tool '{target_tool}' failed hypothetical scenario.")
                            globals()['_last_dream_status'] = f"Nightmare realized! Tool '{target_tool}' failed simulation."

                            # A dream is synthetic evidence, not a confirmed runtime failure. Record it
                            # for human review, but never generate or temporarily apply source changes.
                            if learning_agent:
                                from ai_assistant.core.reflection import ActionableInsight, InsightType
                                new_insight = ActionableInsight(
                                    type=InsightType.HYPOTHETICAL_SCENARIO,
                                    description=f"Dream Scenario '{dream_result.get('scenario_name')}' failed for tool '{target_tool}'.\nFailure Output: {stdout_str}\nStderr: {stderr_str}",
                                    source_reflection_entry_ids=[],
                                    related_tool_name=target_tool,
                                    priority=5,
                                    status="PENDING_MANUAL_REVIEW",
                                    metadata={
                                        "dream_scenario": dream_result,
                                        "failure_output": stdout_str,
                                        "stderr": stderr_str,
                                        "source": "dream_mode",
                                        "synthetic_evidence": True,
                                        "requires_explicit_approval": True,
                                        "review_reason": "Dream scenarios are hypothetical and cannot authorize source changes.",
                                    }
                                )
                                if learning_agent.add_insight(new_insight):
                                    logger.info("BackgroundService: Saved HYPOTHETICAL_SCENARIO for manual review.")

                        else:
                            logger.info(f"BackgroundService: Tool '{target_tool}' survived the dream scenario.")
                            globals()['_last_dream_status'] = f"Tool '{target_tool}' survived dream scenario '{dream_result.get('scenario_name')}'."

            except Exception as e:
                details = _format_exception_details(e)
                provider_failure = _is_dream_provider_failure(e)
                logger.error(
                    "BackgroundService: Dream Mode failed while evaluating %s: %s",
                    target_tool or "no selected tool",
                    details,
                    exc_info=True,
                )
                _record_dream_mode_failure(learning_agent, target_tool, e)
                globals()['_last_dream_status'] = (
                    f"Dream Mode provider failure while evaluating '{target_tool}': {details}"
                    if provider_failure and target_tool
                    else f"Dream Mode runner failure: {details}"
                )
                if provider_failure:
                    # Retry transient provider failures after 15 minutes instead
                    # of waiting for the full daily Dream Mode interval.
                    dream_retry_delay_seconds = min(900, _dream_interval_seconds)


            _last_dream_time = time.time() - max(
                0,
                _dream_interval_seconds - dream_retry_delay_seconds,
            )

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

                         if _requires_manual_source_approval(req.get("type")):
                             logger.info(f"BackgroundService: Request {req_id} requires manual source-change approval.")
                             continue

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

def start_background_services_on_loop(loop):
    """
    Submits the background service loop to an existing, running asyncio event loop.
    This resolves concurrency issues by ensuring the background tasks share the same
    asyncio thread as the main AI orchestrator.
    """
    global _background_service_active, _background_task

    if _background_service_active:
        logger.warning("BackgroundService: Service already active. Ignoring request to start.")
        return

    logger.info("BackgroundService: Starting background loop on provided event loop...")
    _background_service_active = True

    try:
        # submit to the provided loop
        _background_task = asyncio.run_coroutine_threadsafe(_background_loop_async(), loop)
    except Exception as e:
        logger.error(f"BackgroundService: Failed to start background task on loop: {e}", exc_info=True)
        _background_service_active = False

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
