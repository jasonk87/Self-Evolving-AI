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

    def create_workspace(self, purpose: str, agent_id: Optional[str] = None, scope_type: str = "session") -> str:
        """
        Creates a directory in data/temp_agents/{unique_id}.
        If scope_type is 'user', generates a persistent ID based on purpose if one isn't supplied.
        Returns the agent_id.
        """
        if not agent_id:
            if scope_type == "user":
                import hashlib
                # Create a determinist ID for user scoped agents based on purpose/name
                hasher = hashlib.md5(purpose.encode('utf-8')).hexdigest()
                agent_id = f"persistent_{hasher[:12]}"
            else:
                agent_id = str(uuid.uuid4())

        workspace_path = os.path.join(self.base_path, agent_id)

        try:
            os.makedirs(workspace_path, exist_ok=True)
            # Create a metadata file to track purpose and scope
            import json
            metadata = {
                "purpose": purpose,
                "scope_type": scope_type,
                "agent_id": agent_id,
                "created_at": __import__("time").time()
            }
            with open(os.path.join(workspace_path, "metadata.json"), "w") as f:
                json.dump(metadata, f)

            logging.info(f"Created/Accessed workspace for agent {agent_id} at {workspace_path}")
            return agent_id
        except OSError as e:
            logging.error(f"Failed to create workspace for agent {agent_id}: {e}")
            raise

    def get_workspace_path(self, agent_id: str) -> str:
        """
        Returns the absolute path to that agent's folder.
        """
        return os.path.abspath(os.path.join(self.base_path, agent_id))

    def terminate_agent(self, agent_id: str, force: bool = False):
        """
        Recursively deletes the agent's workspace directory to clean up.
        If the agent is persistent (user scope), it won't delete the folder unless force=True.
        """
        workspace_path = self.get_workspace_path(agent_id)

        if os.path.exists(workspace_path):
            # Check if this is a persistent agent
            is_persistent = False
            meta_path = os.path.join(workspace_path, "metadata.json")
            if os.path.exists(meta_path):
                try:
                    import json
                    with open(meta_path, 'r') as f:
                        meta = json.load(f)
                        if meta.get("scope_type") == "user":
                            is_persistent = True
                except:
                    pass

            if is_persistent and not force:
                logging.info(f"Agent {agent_id} is persistent. Skipping workspace removal at {workspace_path}")
                return

            try:
                shutil.rmtree(workspace_path)
                logging.info(f"Terminated agent {agent_id} and removed workspace {workspace_path}")
            except OSError as e:
                logging.error(f"Failed to terminate agent {agent_id}: {e}")
                raise
        else:
            logging.warning(f"Agent {agent_id} workspace not found at {workspace_path}")
