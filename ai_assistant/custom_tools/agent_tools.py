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

def spawn_ephemeral_agent(task_description: str) -> Dict[str, str]:
    """
    Spawns a new ephemeral agent with a dedicated workspace.

    Args:
        task_description (str): A description of the task the agent is intended to perform.

    Returns:
        dict: Contains 'agent_id' and 'workspace_path'.
    """
    agent_id = agent_manager.create_workspace(task_description)
    workspace_path = agent_manager.get_workspace_path(agent_id)
    return {'agent_id': agent_id, 'workspace_path': workspace_path}

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
    workspace_path = agent_manager.get_workspace_path(agent_id)
    if not os.path.exists(workspace_path):
        return {'error': f'Workspace for agent {agent_id} does not exist.'}
    file_path = os.path.join(workspace_path, filename)
    try:
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
        return f'Report submitted and agent {agent_id} terminated successfully.'
    except Exception as e:
        return f'Report submitted, but failed to terminate agent {agent_id}: {e}'

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