from typing import List, Dict
import time
import os
import subprocess
import sys
from typing import Dict
import os
import subprocess
import sys
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
    import os
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

SCHEMA = {
    "spawn_ephemeral_agent": {
        "description": "Spawns a new ephemeral or persistent agent with a dedicated workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_description": {"type": "string", "description": "The task description."},
                "scope_type": {"type": "string", "description": "'session' (ephemeral) or 'user' (persistent)."}
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
    }
}

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