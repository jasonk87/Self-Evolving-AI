import os
import shutil
import uuid
import logging
from typing import Optional

try:
    from ai_assistant.config import get_data_dir
except ImportError:
    # Fallback if config is not available (e.g. testing)
    def get_data_dir():
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))

class AgentManager:
    """
    Manages ephemeral agent workspaces.
    """
    def __init__(self, base_path: Optional[str] = None):
        if base_path:
            self.base_path = base_path
        else:
            self.base_path = os.path.join(get_data_dir(), "temp_agents")

        if not os.path.exists(self.base_path):
            os.makedirs(self.base_path, exist_ok=True)
            logging.info(f"Created base agent directory at {self.base_path}")

    def create_workspace(self, purpose: str) -> str:
        """
        Generates a unique ID and creates a directory in data/temp_agents/{unique_id}.
        Returns the agent_id.
        """
        agent_id = str(uuid.uuid4())
        workspace_path = os.path.join(self.base_path, agent_id)

        try:
            os.makedirs(workspace_path, exist_ok=True)
            # Create a metadata file to track purpose (optional but useful)
            with open(os.path.join(workspace_path, "metadata.txt"), "w") as f:
                f.write(f"Purpose: {purpose}\n")

            logging.info(f"Created workspace for agent {agent_id} at {workspace_path}")
            return agent_id
        except OSError as e:
            logging.error(f"Failed to create workspace for agent {agent_id}: {e}")
            raise

    def get_workspace_path(self, agent_id: str) -> str:
        """
        Returns the absolute path to that agent's folder.
        """
        return os.path.abspath(os.path.join(self.base_path, agent_id))

    def terminate_agent(self, agent_id: str):
        """
        Recursively deletes the agent's workspace directory to clean up.
        """
        workspace_path = self.get_workspace_path(agent_id)

        if os.path.exists(workspace_path):
            try:
                shutil.rmtree(workspace_path)
                logging.info(f"Terminated agent {agent_id} and removed workspace {workspace_path}")
            except OSError as e:
                logging.error(f"Failed to terminate agent {agent_id}: {e}")
                raise
        else:
            logging.warning(f"Agent {agent_id} workspace not found at {workspace_path}")
