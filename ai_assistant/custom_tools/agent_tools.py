import os
import sys
import subprocess
import time
import json
from typing import List, Dict, Any, Optional
from ai_assistant.core.agent_manager import AgentManager
from ai_assistant.core.notification_manager import NotificationManager, NotificationType
agent_manager = AgentManager()

def spawn_ephemeral_agent(task_description: str, scope_type: str = "session") -> Dict[str, str]:
    """
    Spawns a new ephemeral or persistent agent with a dedicated workspace.

    Args:
        task_description (str): A description of the task the agent is intended to perform.
        scope_type (str, optional): 'session' (ephemeral) or 'user' (persistent).

    Returns:
        dict: Contains 'agent_id' and 'workspace_path'.
    """
    agent_id = agent_manager.create_workspace(task_description, scope_type=scope_type)
    workspace_path = agent_manager.get_workspace_path(agent_id)
    return {'agent_id': agent_id, 'workspace_path': workspace_path, 'scope': scope_type}

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
    message = f'Agent {agent_id} Report:\n{report_content}'
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
        return f"Successfully created dynamic specialist '{name}' at {filename}. Remember to restart the ToolSystem or app to load it."
    except Exception as e:
        return f"Failed to create specialist '{name}': {e}"

from pydantic import BaseModel, Field

class SpawnEphemeralAgentSchema(BaseModel):
    task_description: str = Field(..., description="The task description.")
    scope_type: Optional[str] = Field("session", description="'session' (ephemeral) or 'user' (persistent).")

class RunAgentCodeSchema(BaseModel):
    agent_id: str
    filename: str
    code: str
    cmd_args: Optional[List[str]] = None

class SubmitAgentReportSchema(BaseModel):
    agent_id: str
    report_content: str

class CreateDynamicSpecialistSchema(BaseModel):
    name: str = Field(..., description="The name of the specialist.")
    description: str = Field(..., description="What the specialist does.")
    logic_code: str = Field(..., description="The python code defining the tool logic (must include SCHEMA).")
    retirement_policy: str = Field(..., description="Mandatory defined conditions for retiring this specialist.")
    rollback_instructions: str = Field(..., description="Mandatory steps on how to clean up after this specialist.")

class SpawnBackgroundAgentSchema(BaseModel):
    task_description: str
    session_id: Optional[str] = None

class ListActiveAgentsSchema(BaseModel):
    pass

class WakeAgentSchema(BaseModel):
    agent_id: str = Field(..., description="The ID of the existing persistent agent.")
    new_task: str = Field(..., description="The new task description for the agent to execute.")


SCHEMA = {
    "spawn_ephemeral_agent": {
        "description": "Spawns a new ephemeral or persistent agent with a dedicated workspace.",
        "parameters": SpawnEphemeralAgentSchema.model_json_schema()
    },
    "run_agent_code": {
        "description": "Writes code to a file in the agent's workspace and executes it.",
        "parameters": RunAgentCodeSchema.model_json_schema()
    },
    "submit_agent_report": {
        "description": "Submits a report from the agent and terminates it.",
        "parameters": SubmitAgentReportSchema.model_json_schema()
    },
    "create_dynamic_specialist": {
        "description": "Create a dynamic specialist agent on the fly by writing a tool plugin. Mandates defined retirement policies and rollback paths.",
        "parameters": CreateDynamicSpecialistSchema.model_json_schema()
    },
    "spawn_background_agent": {
        "description": "Spawns a background agent to perform a time-consuming task.",
        "parameters": SpawnBackgroundAgentSchema.model_json_schema()
    },
    "list_active_agents": {
        "description": "Scans the temp_agents directory to return a list of currently active sub-agents and their metadata.",
        "parameters": ListActiveAgentsSchema.model_json_schema()
    },
    "wake_agent": {
        "description": "Wakes an existing persistent agent and assigns it a new task to handle autonomously.",
        "parameters": WakeAgentSchema.model_json_schema()
    }
}

def list_active_agents() -> str:
    """
    Scans the temp_agents directory to find and list all active agent workspaces.
    Returns their metadata configuration to help Weebo know its "Swarm Roster".
    """
    base_path = agent_manager.base_path
    if not os.path.exists(base_path):
        return "No active agents found. The temp_agents directory does not exist."

    agents = []
    try:
        for entry in os.listdir(base_path):
            agent_path = os.path.join(base_path, entry)
            if os.path.isdir(agent_path):
                meta_file = os.path.join(agent_path, "metadata.json")
                if os.path.exists(meta_file):
                    try:
                        with open(meta_file, 'r') as f:
                            meta = json.load(f)
                            agents.append(f"- ID: {entry} | Scope: {meta.get('scope_type', 'unknown')} | Purpose: {meta.get('purpose', 'unknown')}")
                    except json.JSONDecodeError:
                        agents.append(f"- ID: {entry} | Error reading metadata.")
                else:
                    # Legacy or missing metadata
                    agents.append(f"- ID: {entry} | No metadata available.")

        if not agents:
            return "No active agents found in the roster."

        return "Currently Active Agents:\n" + "\n".join(agents)
    except Exception as e:
        return f"Failed to list active agents: {e}"

def wake_agent(agent_id: str, new_task: str) -> str:
    """
    Wakes up an existing persistent agent by creating a background goal routed directly to it.
    """
    workspace_path = agent_manager.get_workspace_path(agent_id)
    if not os.path.exists(workspace_path):
        return f"Error: Agent '{agent_id}' does not exist or has been terminated."

    from ai_assistant.goals.goal_management import create_goal

    metadata = {
        "type": "background_agent",
        "routed_agent_id": agent_id,
        "created_at": time.time()
    }

    goal_id = create_goal(
        title=f"Routed Task for {agent_id}: {new_task[:30]}...",
        description=new_task,
        priority="high",
        metadata=metadata
    )

    return f"Successfully woke agent '{agent_id}' and assigned the task. Goal ID: {goal_id}. It will run in the background."

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
    from ai_assistant.goals.goal_management import create_goal
    
    # Identify source
    metadata = {
        "type": "background_agent",
        "source_session_id": session_id,
        "created_at": time.time()
    }
    
    goal_id = create_goal(
        title=f"Background Agent: {task_description[:50]}...",
        description=task_description,
        priority="high", # Prioritize agent requests
        metadata=metadata
    )
    
    # 2. Trigger Background Service (Optional - it polls)
    # But for responsiveness, maybe we should indicate it will be picked up.
    
    return f"Background Agent assigned to task: '{task_description}'.\nGoal ID: {goal_id}\nI will notify you in this chat when the agent completes the work."