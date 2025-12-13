import uuid
import time
import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable

logger = logging.getLogger(__name__)

class ApprovalManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ApprovalManager, cls).__new__(cls)
            cls._instance.pending_requests = {}
            cls._instance.callbacks = {} # request_id -> callable
        return cls._instance

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
            
        logger.info(f"Added approval request {req_id} of type {req_type}")
        return req_id

    def get_pending_requests(self) -> List[Dict]:
        """Returns list of pending requests sorted by newest first."""
        return sorted(list(self.pending_requests.values()), key=lambda x: x['timestamp'], reverse=True)

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
                    if asyncio.iscoroutinefunction(func):
                        await func()
                    else:
                        func()
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

    def _cleanup(self, req_id: str):
        if req_id in self.pending_requests:
            del self.pending_requests[req_id]
        if req_id in self.callbacks:
            del self.callbacks[req_id]

# Singleton instance
approval_manager = ApprovalManager()
