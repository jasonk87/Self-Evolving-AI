import os
import sys
import subprocess
import time
import json
from typing import List, Dict, Optional
from ai_assistant.core.change_policy import GovernanceTier, decide_governance
from ai_assistant.core.tool_lifecycle import mark_tool_registered, record_tool_candidate
from ai_assistant.core.agent_manager import AgentManager
from ai_assistant.core.notification_manager import NotificationManager, NotificationType
agent_manager = AgentManager()

def _normalize_scope_type(scope_type: str) -> str:
    normalized = str(scope_type or "session").strip().casefold()
    if normalized in {"persistent", "persist", "user_scoped", "user-scoped", "user"}:
        return "user"
    return "session"

def create_agent_workspace(task_description: str, scope_type: str = "session") -> Dict[str, str]:
    """
    Creates a new ephemeral or persistent agent workspace without queueing work.

    Args:
        task_description (str): A description of the task the agent is intended to perform.
        scope_type (str, optional): 'session' (ephemeral) or 'user' (persistent).

    Returns:
        dict: Contains 'agent_id' and 'workspace_path'.
    """
    scope_type = _normalize_scope_type(scope_type)
    agent_id = agent_manager.create_workspace(task_description, scope_type=scope_type)
    workspace_path = agent_manager.get_workspace_path(agent_id)
    return {'agent_id': agent_id, 'workspace_path': workspace_path, 'scope': scope_type}

def spawn_ephemeral_agent(task_description: str, scope_type: str = "session", session_id: str = None, queue_task: bool = True) -> Dict[str, str]:
    """
    Spawns an agent workspace and, by default, queues real background work for it.

    A workspace alone is not an active agent. Chat-facing calls should keep
    queue_task=True so the background service actually executes the task and
    reports back to the originating conversation when finished.
    """
    workspace = create_agent_workspace(task_description, scope_type=scope_type)
    if not queue_task:
        return workspace

    from ai_assistant.goals.goal_management import create_goal, list_goals

    normalized_description = task_description.strip().casefold()
    for existing_goal in list_goals():
        metadata = existing_goal.get("metadata", {})
        if (
            str(existing_goal.get("description", "")).strip().casefold() == normalized_description
            and metadata.get("type") == "background_agent"
            and existing_goal.get("status") in {"PENDING_APPROVAL", "pending", "in_progress"}
        ):
            return {
                **workspace,
                "goal_id": existing_goal.get("id"),
                "goal_status": existing_goal.get("status"),
                "queued": "false",
                "message": (
                    f"Agent workspace exists, and a matching background task is already queued. "
                    f"Goal ID: {existing_goal.get('id')}. Status: {existing_goal.get('status')}."
                ),
            }

    metadata = {
        "type": "background_agent",
        "routed_agent_id": workspace["agent_id"],
        "source_session_id": session_id,
        "created_at": time.time(),
        "execution_mode": "one_shot",
    }
    goal = create_goal(
        title=f"Agent Task for {workspace['agent_id']}: {task_description[:40]}...",
        description=task_description,
        priority="high",
        status="pending",
        metadata=metadata,
    )
    return {
        **workspace,
        "goal_id": goal["id"],
        "goal_status": goal["status"],
        "queued": "true",
        "message": (
            f"Agent {workspace['agent_id']} was created and assigned a real background task. "
            f"Goal ID: {goal['id']}. Status: {goal['status']}. It will report back when finished."
        ),
    }

def run_agent_code(agent_id: str, filename: str, code: str, cmd_args: List[str]=None) -> Dict[str, str]:
    """
    Writes code to a file in the agent's workspace and executes it.

    Args:
        agent_id (str): The ID of the agent.
        filename (str): The name of the file to create (e.g., 'script.py').
        code (str): The source code to write to the file.
        cmd_args (list, optional): Additional command line arguments to pass to the script.

    Returns:
        dict: Contains 'stdout', 'stderr', and 'return_code'.
    """
    workspace_path = os.path.abspath(agent_manager.get_workspace_path(agent_id))
    if not os.path.exists(workspace_path):
        return {'error': f'Workspace for agent {agent_id} does not exist.'}

    file_path = os.path.abspath(os.path.join(workspace_path, filename))
    if not file_path.startswith(workspace_path + os.sep) and file_path != workspace_path:
        return {'error': f'Policy Violation: Agent attempted to write outside its designated workspace ({filename})'}

    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(code)
    except IOError as e:
        return {'error': f'Failed to write code to file: {e}'}
    command = [sys.executable, filename]
    if cmd_args:
        command.extend(cmd_args)
    try:
        result = subprocess.run(command, cwd=workspace_path, capture_output=True, text=True, timeout=60)
        return {'stdout': result.stdout, 'stderr': result.stderr, 'return_code': str(result.returncode)}
    except subprocess.TimeoutExpired:
        return {'error': 'Execution timed out.'}
    except Exception as e:
        return {'error': f'Execution failed: {e}'}

def submit_agent_report(agent_id: str, report_content: str, notification_manager: Optional[NotificationManager]=None) -> str:
    """
    Submits a report from the agent and terminates the agent (deletes workspace).

    Args:
        agent_id (str): The ID of the agent.
        report_content (str): The final report or result from the agent.
        notification_manager (NotificationManager, optional): Injected notification manager to deliver the report.

    Returns:
        str: Confirmation message.
    """
    _message = f'Agent {agent_id} Report:\n{report_content}'
    if notification_manager:
        notification_manager.add_notification(event_type=NotificationType.TASK_COMPLETED_SUCCESSFULLY, summary_message=f'Ephemeral Agent {agent_id} finished.', details_payload={'report': report_content})
    else:
        print(f'--- [Ephemeral Agent {agent_id} Report] ---\n{report_content}\n---------------------------------------')
    try:
        agent_manager.terminate_agent(agent_id)
        return f'Report submitted and agent {agent_id} operation concluded successfully.'
    except Exception as e:
        return f'Report submitted, but failed during agent {agent_id} teardown: {e}'

def create_dynamic_specialist(name: str, description: str, logic_code: str, retirement_policy: str, rollback_instructions: str) -> str:
    """
    Operationalize Phase R3: Create a dynamic specialist agent on the fly by writing a tool plugin.
    This is review-gated and mandates defined retirement policies and rollback paths.

    Args:
        name (str): The name of the specialist (e.g., 'ops_diagnostics_worker').
        description (str): What the specialist does.
        logic_code (str): The python code defining the tool logic (must include SCHEMA).
        retirement_policy (str): Mandatory defined conditions for retiring this specialist.
        rollback_instructions (str): Mandatory steps on how to clean up after this specialist.

    Returns:
        str: Result of creation request.
    """
    if not retirement_policy or not rollback_instructions:
         return "Error: Dynamic specialists MUST include an explicit retirement_policy and rollback_instructions."

    from ai_assistant.config import get_data_dir

    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    tool_dir = os.path.join(app_root, "ai_assistant", "custom_tools")

    filename = f"dynamic_specialist_{name.lower().replace(' ', '_')}.py"
    filepath = os.path.join(tool_dir, filename)

    governance = decide_governance(filepath, action="create", project_root=app_root)
    if governance.tier != GovernanceTier.AUTONOMOUS:
        return f"Error: Dynamic specialist creation blocked by governance policy: {governance.reason}"

    # Save the governance logic to a metadata log
    metadata_path = os.path.join(get_data_dir(), "specialist_governance.log")
    with open(metadata_path, 'a') as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Created Specialist: {name}\n")
        f.write(f"Retirement Policy: {retirement_policy}\n")
        f.write(f"Rollback Instructions: {rollback_instructions}\n")
        f.write(f"File: {filename}\n---\n")

    try:
        with open(filepath, 'w') as f:
            f.write(f'# DYNAMIC SPECIALIST: {name}\n')
            f.write(f'# RETIREMENT: {retirement_policy}\n')
            f.write(f'# ROLLBACK: {rollback_instructions}\n\n')
            f.write(logic_code)
        module_name = os.path.splitext(filename)[0]
        module_path = f"ai_assistant.custom_tools.{module_name}"
        record_tool_candidate(
            tool_name=module_name,
            module_path=module_path,
            function_name=module_name,
            file_path=filepath,
            tool_type="dynamic_specialist",
            source="create_dynamic_specialist",
            metadata={
                "specialist_name": name,
                "description": description,
                "retirement_policy": retirement_policy,
                "rollback_instructions": rollback_instructions,
                "governance": {
                    "zone": governance.zone.value,
                    "tier": governance.tier.value,
                    "required_gates": list(governance.required_gates),
                },
            },
        )
        mark_tool_registered(
            tool_name=module_name,
            module_path=module_path,
            function_name=module_name,
            file_path=filepath,
            tool_type="dynamic_specialist",
            metadata={"registration_note": "Specialist file created; runtime refresh may be required."},
        )
        return f"Successfully created dynamic specialist '{name}' at {filename}. Remember to restart the ToolSystem or app to load it."
    except Exception as e:
        return f"Failed to create specialist '{name}': {e}"

SCHEMA = {
    "spawn_ephemeral_agent": {
        "description": "Spawns a new ephemeral or persistent agent and queues real background work that reports back when finished.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_description": {"type": "string", "description": "The task description."},
                "scope_type": {"type": "string", "description": "'session' (ephemeral) or 'user'/'persistent'."},
                "session_id": {"type": "string", "description": "Optional originating chat session ID for completion delivery."},
                "queue_task": {"type": "boolean", "description": "Internal use only. Defaults true; false creates only a workspace."}
            },
            "required": ["task_description"]
        }
    },
    "run_agent_code": {
        "description": "Writes code to a file in the agent's workspace and executes it.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "filename": {"type": "string"},
                "code": {"type": "string"},
                "cmd_args": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["agent_id", "filename", "code"]
        }
    },
    "submit_agent_report": {
        "description": "Submits a report from the agent and terminates it.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "report_content": {"type": "string"}
            },
            "required": ["agent_id", "report_content"]
        }
    },
    "create_dynamic_specialist": {
        "description": "Create a dynamic specialist agent on the fly by writing a tool plugin. Mandates defined retirement policies and rollback paths.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The name of the specialist."},
                "description": {"type": "string", "description": "What the specialist does."},
                "logic_code": {"type": "string", "description": "The python code defining the tool logic (must include SCHEMA)."},
                "retirement_policy": {"type": "string", "description": "Mandatory defined conditions for retiring this specialist."},
                "rollback_instructions": {"type": "string", "description": "Mandatory steps on how to clean up after this specialist."}
            },
            "required": ["name", "description", "logic_code", "retirement_policy", "rollback_instructions"]
        }
    },
    "spawn_background_agent": {
        "description": "Spawns a background agent to perform a time-consuming task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_description": {"type": "string"},
                "session_id": {"type": "string"}
            },
            "required": ["task_description"]
        }
    },
    "list_pending_background_goals": {
        "description": "Lists background goals that are waiting for explicit approval.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    "approve_background_goal": {
        "description": "Approves a pending background goal so the background worker can execute it.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "The pending background goal ID."}
            },
            "required": ["goal_id"]
        }
    },
    "list_pending_source_change_proposals": {
        "description": "Lists architect source-change proposals waiting for explicit user approval.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    "list_active_agents": {
        "description": "Lists durable agent workspaces and clearly distinguishes available workers from agents with queued or running goals.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    "wake_agent": {
        "description": "Wakes an existing persistent agent and assigns it a new task to handle autonomously.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "The ID of the existing persistent agent."},
                "new_task": {"type": "string", "description": "The new task description for the agent to execute."},
                "session_id": {"type": "string", "description": "Optional originating chat session ID for completion delivery."}
            },
            "required": ["agent_id", "new_task"]
        }
    }
}

def list_active_agents() -> str:
    """
    Lists agent workspaces and their durable queued or running goal state.
    A workspace alone is available capacity, not evidence of active work.
    """
    base_path = agent_manager.base_path
    if not os.path.exists(base_path):
        return "No active agents found. The temp_agents directory does not exist."

    try:
        removed_agent_ids = agent_manager.cleanup_stale_session_agents()
        from ai_assistant.goals.goal_management import list_goals

        active_goals_by_agent = {}
        for goal in list_goals():
            if goal.get("status") not in {"pending", "in_progress"}:
                continue
            routed_agent_id = goal.get("metadata", {}).get("routed_agent_id")
            if routed_agent_id:
                active_goals_by_agent[routed_agent_id] = goal

        running_agents = []
        available_agents = []
        for entry in os.listdir(base_path):
            agent_path = os.path.join(base_path, entry)
            if os.path.isdir(agent_path):
                meta_file = os.path.join(agent_path, "metadata.json")
                if os.path.exists(meta_file):
                    try:
                        with open(meta_file, 'r') as f:
                            meta = json.load(f)
                            line = f"- ID: {entry} | Scope: {meta.get('scope_type', 'unknown')} | Purpose: {meta.get('purpose', 'unknown')}"
                            goal = active_goals_by_agent.get(entry)
                            if goal:
                                running_agents.append(f"{line} | Goal status: {goal.get('status')} | Goal ID: {goal.get('id')}")
                            else:
                                available_agents.append(f"{line} | Goal status: available")
                    except json.JSONDecodeError:
                        available_agents.append(f"- ID: {entry} | Error reading metadata.")
                else:
                    # Legacy or missing metadata
                    available_agents.append(f"- ID: {entry} | No metadata available.")

        if not running_agents and not available_agents:
            result = "No agent workspaces are available and no agents are currently running a task."
            if removed_agent_ids:
                result += f" Removed {len(removed_agent_ids)} stale session workspace(s)."
            return result

        sections = []
        if running_agents:
            sections.append("Agents with queued or running goals:\n" + "\n".join(running_agents))
        else:
            sections.append("No agents are currently running or queued for a task.")
        if available_agents:
            sections.append("Available agent workspaces (not currently working):\n" + "\n".join(available_agents))
        result = "\n".join(sections)
        if removed_agent_ids:
            result += f"\nRemoved {len(removed_agent_ids)} stale session workspace(s)."
        return result
    except Exception as e:
        return f"Failed to list active agents: {e}"

def wake_agent(agent_id: str, new_task: str, session_id: str = None) -> str:
    """
    Wakes up an existing persistent agent by creating a background goal routed directly to it.
    """
    if not isinstance(agent_id, str) or not agent_id.strip():
        return "Error: Agent ID must be a non-empty string."
    if not isinstance(new_task, str) or not new_task.strip():
        return "Error: New task must be a non-empty string."

    agent_id = agent_id.strip()
    new_task = new_task.strip()
    workspace_path = agent_manager.get_workspace_path(agent_id)
    if not os.path.exists(workspace_path):
        return f"Error: Agent '{agent_id}' does not exist or has been terminated."

    from ai_assistant.goals.goal_management import create_goal

    metadata = {
        "type": "background_agent",
        "routed_agent_id": agent_id,
        "source_session_id": session_id,
        "created_at": time.time(),
        "execution_mode": "one_shot",
    }

    goal_id = create_goal(
        title=f"Routed Task for {agent_id}: {new_task[:30]}...",
        description=new_task,
        priority="high",
        status="pending",
        metadata=metadata
    )

    return f"Successfully assigned agent '{agent_id}' a background goal. Goal ID: {goal_id['id']}. Status: {goal_id['status']}. It is queued for execution."

def spawn_background_agent(task_description: str, session_id: str = None) -> str:
    """
    Spawns a background agent to perform a time-consuming task.
    The agent will work asynchronously and report back to the chat when done.

    Args:
        task_description (str): The detailed task to perform (e.g., "Deep research on Quantum Computing").
        session_id (str, optional): The chat session ID to report back to. 
                                    (The system should automatically provide this if called from chat).

    Returns:
        str: A confirmation message with the Goal ID.
    """
    # 1. Create a Goal
    from ai_assistant.goals.goal_management import create_goal, list_goals

    normalized_description = task_description.strip().casefold()
    for existing_goal in list_goals():
        if (
            str(existing_goal.get("description", "")).strip().casefold() == normalized_description
            and existing_goal.get("status") in {"PENDING_APPROVAL", "pending", "in_progress"}
        ):
            return (
                f"Background task already exists. Goal ID: {existing_goal.get('id')}\n"
                f"Status: {existing_goal.get('status')}"
            )
    
    # Identify source
    metadata = {
        "type": "background_agent",
        "source_session_id": session_id,
        "created_at": time.time(),
        "execution_mode": "one_shot",
    }
    
    goal_id = create_goal(
        title=f"Background Agent: {task_description[:50]}...",
        description=task_description,
        priority="high", # Prioritize agent requests
        status="pending",
        metadata=metadata
    )
    
    # 2. Trigger Background Service (Optional - it polls)
    # But for responsiveness, maybe we should indicate it will be picked up.
    
    return f"Background Agent assigned to task: '{task_description}'.\nGoal ID: {goal_id['id']}\nStatus: {goal_id['status']}\nQueued for execution."

def list_pending_background_goals() -> str:
    """Lists background goals that still need a green light."""
    from ai_assistant.goals.goal_management import list_goals

    goals = [
        goal for goal in list_goals(status="PENDING_APPROVAL")
        if goal.get("metadata", {}).get("type") == "background_agent"
    ]
    if not goals:
        return "No background goals are waiting for approval."

    lines = [
        f"- ID: {goal.get('id')} | Priority: {goal.get('priority')} | Task: {goal.get('description') or goal.get('title')}"
        for goal in goals
    ]
    return "Background Goals Waiting for Approval:\n" + "\n".join(lines)

def approve_background_goal(goal_id: str) -> str:
    """Approves one pending background goal and moves it into the worker queue."""
    from ai_assistant.goals.goal_management import approve_goal, get_goal

    goal = get_goal(goal_id)
    if not goal:
        return f"Error: Background goal '{goal_id}' was not found."
    if goal.get("status") != "PENDING_APPROVAL":
        return f"Background goal '{goal_id}' is already in status '{goal.get('status')}'."
    if goal.get("metadata", {}).get("type") != "background_agent":
        return (
            f"Error: Goal '{goal_id}' is not a background-agent launch. "
            "Architect source changes require approval from the Approvals UI."
        )
    if approve_goal(goal_id):
        return f"Approved background goal '{goal_id}'. It is queued for execution."
    return f"Error: Failed to approve background goal '{goal_id}'."

def list_pending_source_change_proposals() -> str:
    """Lists architect source-change proposals waiting for explicit user approval."""
    from ai_assistant.goals.goal_management import list_goals

    goals = [
        goal for goal in list_goals(status="PENDING_APPROVAL")
        if goal.get("metadata", {}).get("type") == "architect_source_change"
        or not goal.get("metadata")
    ]
    if not goals:
        return "No source-change proposals are waiting for approval."

    lines = [
        f"- ID: {goal.get('id')} | Priority: {goal.get('priority')} | Proposal: {goal.get('description') or goal.get('title')}"
        for goal in goals
    ]
    return "Source-Change Proposals Waiting for Approval:\n" + "\n".join(lines)

def _approve_source_change_proposal_from_ui(goal_id: str) -> str:
    """Releases one architect source-change proposal after a human UI action."""
    from ai_assistant.goals.goal_management import approve_goal, get_goal

    goal = get_goal(goal_id)
    if not goal:
        return f"Error: Source-change proposal '{goal_id}' was not found."
    if goal.get("status") != "PENDING_APPROVAL":
        return f"Source-change proposal '{goal_id}' is already in status '{goal.get('status')}'."
    if goal.get("metadata", {}).get("type") != "architect_source_change":
        return f"Error: Goal '{goal_id}' is not an architect source-change proposal."
    if approve_goal(goal_id):
        return f"Approved source-change proposal '{goal_id}'. It is queued for execution."
    return f"Error: Failed to approve source-change proposal '{goal_id}'."
