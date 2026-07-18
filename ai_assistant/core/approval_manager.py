import uuid
import time
import logging
import inspect
import json
import os
from typing import Dict, Any, List, Optional, Callable
from ai_assistant.config import get_data_dir

logger = logging.getLogger(__name__)

class ApprovalManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ApprovalManager, cls).__new__(cls)
            cls._instance.pending_requests = {}
            cls._instance.callbacks = {} # request_id -> callable
            cls._instance.filepath = os.path.join(get_data_dir(), "pending_approvals.json")
            cls._instance._load_pending_requests()
        return cls._instance

    def _load_pending_requests(self):
        try:
            if not os.path.exists(self.filepath):
                return
            with open(self.filepath, "r", encoding="utf-8") as f:
                requests = json.load(f)
            if isinstance(requests, list):
                self.pending_requests = {
                    req["id"]: req for req in requests
                    if isinstance(req, dict) and req.get("id")
                }
        except Exception as e:
            logger.error(f"Failed to load pending approvals: {e}")

    def _save_pending_requests(self):
        try:
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(list(self.pending_requests.values()), f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save pending approvals: {e}")

    def add_request(self, req_type: str, data: Any, description: str, execute_func: Callable = None) -> str:
        """
        Adds a new approval request.
        
        Args:
            req_type: The type of request (e.g., 'suggestion', 'insight', 'project_plan').
            data: structured data to display to the user.
            description: A human-readable summary.
            execute_func: A callable (sync or async) to execute if approved.
        """
        req_id = str(uuid.uuid4())

        # --- Deduplication Logic ---
        fingerprint = self._generate_fingerprint(req_type, data, description)

        # Check against pending requests
        for existing_id, existing_req in self.pending_requests.items():
            existing_fingerprint = self._generate_fingerprint(existing_req["type"], existing_req["data"], existing_req["description"])
            if fingerprint == existing_fingerprint:
                # Duplicate found!
                # Update the timestamp to bump it up? Or just return existing?
                # Let's keep the old one (FIFO) but maybe log it.
                logger.info(f"Duplicate approval request detected (Type: {req_type}). Fingerprint match. returning existing ID: {existing_id}")
                return existing_id

        self.pending_requests[req_id] = {
            "id": req_id,
            "type": req_type,
            "data": data,
            "description": description,
            "timestamp": time.time(),
            "status": "pending"
        }

        if execute_func:
            self.callbacks[req_id] = execute_func
        self._save_pending_requests()
        logger.info(f"Added approval request {req_id} of type {req_type}")
        return req_id

    def _generate_fingerprint(self, req_type: str, data: Any, description: str) -> str:
        """
        Generates a unique signature for a request to detect duplicates.
        """
        try:
            # 1. Tool Modification / Fix
            # Use module/function name as the core identity.
            # Description often varies slightly (different evidence quote), so we ignore it if we have hard data.
            if req_type == "tool_modification" or req_type == "tool_fix":
                if isinstance(data, dict):
                    mod_path = data.get("module_path")
                    func_name = data.get("function_name")
                    tool_name = data.get("tool_name")

                    # If we have specific code targets, that's a strong identity
                    if mod_path and func_name:
                         return f"{req_type}:{mod_path}:{func_name}"

                    # Fallback to tool name
                    if tool_name:
                         return f"{req_type}:{tool_name}"

            # 2. Learned Facts
            if req_type == "add_learned_fact":
                 if isinstance(data, dict):
                     # Unique by the fact text itself
                     fact = data.get("fact_to_learn", "").strip().lower()
                     return f"{req_type}:{fact}"

            # 3. Architect Proposals
            if req_type == "architect_proposal":
                 if isinstance(data, dict):
                     target_file = data.get("target_file", "").split("/")[-1] # basename
                     return f"{req_type}:{target_file}"

            # Default: Fallback to description (maybe hashed if too long)
            # Normalize description to catch small variations?
            # E.g. "ISSUE DETECTED: Foo bar (Evidence: ...)" -> "ISSUE DETECTED: Foo bar"

            # Simple normalization: First 50 chars of description + type
            clean_desc = description.strip().lower()[:100]
            return f"{req_type}:{clean_desc}"

        except Exception as e:
            logger.warning(f"Error generating fingerprint for approval request: {e}")
            # Fallback to random to avoid blocking if logic fails, but defeats dedup
            return str(uuid.uuid4())

    def get_pending_requests(self) -> List[Dict]:
        """Returns list of pending requests sorted by newest first."""
        requests = list(self.pending_requests.values())
        for req in requests:
            timestamp = req.get("timestamp", 0)
            if timestamp > 10_000_000_000:
                req["timestamp"] = timestamp / 1000
        return sorted(requests, key=lambda x: x['timestamp'], reverse=True)

    def get_request(self, req_id: str) -> Optional[Dict]:
        """Retrieves a specific request by ID."""
        return self.pending_requests.get(req_id)

    async def approve_request(self, req_id: str) -> bool:
        """Executes the callback associated with the request."""
        if req_id in self.pending_requests:
            logger.info(f"Approving request {req_id}")

            func = self.callbacks.get(req_id)
            success = True

            if func:
                try:
                    result = func()
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:
                    logger.error(f"Error executing approved request {req_id}: {e}")
                    success = False

            # Clean up
            self._cleanup(req_id)
            return success
        return False

    def deny_request(self, req_id: str) -> bool:
        """Removes the request without executing."""
        if req_id in self.pending_requests:
            logger.info(f"Denying request {req_id}")
            self._cleanup(req_id)
            return True
        return False

    def resolve_request_manually(self, req_id: str) -> bool:
        """
        Marks a request as 'resolved' or 'patched' without executing the callback.
        This is distinct from denial as it implies the underlying goal was achieved manually.
        """
        if req_id in self.pending_requests:
            logger.info(f"Marking request {req_id} as manually resolved/patched")

            # Logic to handle side-effects (like updating insights) if needed
            _req_data = self.pending_requests[req_id]

            # If we need to notify the source (like LearningAgent), we might need a separate callback or event.
            # For now, we assume the caller handles the insight update, or we add a 'on_resolve' callback support later.

            self._cleanup(req_id)
            return True
        return False

    def _cleanup(self, req_id: str):
        if req_id in self.pending_requests:
            del self.pending_requests[req_id]
        if req_id in self.callbacks:
            del self.callbacks[req_id]
        self._save_pending_requests()

# Singleton instance
approval_manager = ApprovalManager()
