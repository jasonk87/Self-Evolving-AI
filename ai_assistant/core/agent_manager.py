import os
import shutil
import uuid
import logging
import hashlib
import json
import time
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
                # Create a determinist ID for user scoped agents based on purpose/name
                hasher = hashlib.md5(purpose.encode('utf-8')).hexdigest()
                agent_id = f"persistent_{hasher[:12]}"
            else:
                agent_id = str(uuid.uuid4())

        workspace_path = self.get_workspace_path(agent_id)

        try:
            os.makedirs(workspace_path, exist_ok=True)
            # Create a metadata file to track purpose and scope
            metadata = {
                "purpose": purpose,
                "scope_type": scope_type,
                "agent_id": agent_id,
                "created_at": time.time()
            }
            with open(os.path.join(workspace_path, "metadata.json"), "w", encoding='utf-8') as f:
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
        # Normalize Windows-style separators for cross-platform checking
        normalized_agent_id = agent_id.replace('\\', '/')
        workspace_path = os.path.abspath(os.path.join(self.base_path, normalized_agent_id))
        base_path = os.path.abspath(self.base_path)
        if os.path.commonpath([base_path, workspace_path]) != base_path:
            raise ValueError(f"Agent workspace '{agent_id}' resolves outside the configured base directory.")
        return workspace_path

    def cleanup_stale_session_agents(self, max_age_seconds: int = 3600) -> list[str]:
        """
        Removes expired session-scoped workspaces left behind by interrupted runs.
        User-scoped workspaces are intentionally preserved.
        """
        removed_agent_ids = []
        now = time.time()

        if not os.path.isdir(self.base_path):
            return removed_agent_ids

        for agent_id in os.listdir(self.base_path):
            try:
                workspace_path = self.get_workspace_path(agent_id)
                if not os.path.isdir(workspace_path):
                    continue

                metadata_path = os.path.join(workspace_path, "metadata.json")
                if not os.path.isfile(metadata_path):
                    continue

                with open(metadata_path, "r", encoding="utf-8") as metadata_file:
                    metadata = json.load(metadata_file)

                if metadata.get("scope_type") != "session":
                    continue

                created_at = float(metadata.get("created_at", os.path.getmtime(workspace_path)))
                if now - created_at <= max_age_seconds:
                    continue

                self.terminate_agent(agent_id)
                removed_agent_ids.append(agent_id)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logging.warning(f"Failed to evaluate stale agent workspace {agent_id}: {exc}")

        if removed_agent_ids:
            logging.info(f"Removed {len(removed_agent_ids)} stale session agent workspace(s).")
        return removed_agent_ids

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
                    with open(meta_path, 'r', encoding='utf-8') as f:
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
