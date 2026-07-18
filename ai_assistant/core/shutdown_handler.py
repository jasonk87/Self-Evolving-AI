import threading
import time
import os
import uuid
import logging

logger = logging.getLogger(__name__)

class GracefulShutdownManager:
    """
    Manages the graceful shutdown of the application by tracking active tasks
    and waiting for them to complete before forcing an exit.
    """
    def __init__(self):
        self._active_tasks = {} # task_id -> description
        self._shutdown_requested = False
        self._lock = threading.Lock()

    def register_task(self, description="Unknown Task") -> str:
        """
        Registers a new task that should prevent immediate shutdown.
        Returns a task_id token that must be passed to complete_task.
        """
        if self._shutdown_requested:
            # Optionally refuse new tasks if shutdown is pending?
            # For now, we log a warning but allow it if it's critical,
            # though ideally the system should check is_shutdown_requested()
            logger.warning(f"Task '{description}' started during shutdown sequence.")

        task_id = str(uuid.uuid4())
        with self._lock:
            self._active_tasks[task_id] = description
            logger.debug(f"Registered graceful shutdown task: {description} ({task_id})")
        return task_id

    def complete_task(self, task_id: str):
        """Marks a registered task as complete."""
        with self._lock:
            if task_id in self._active_tasks:
                desc = self._active_tasks.pop(task_id)
                logger.debug(f"Completed graceful shutdown task: {desc} ({task_id})")
            else:
                logger.warning(f"Attempted to complete unknown task_id: {task_id}")

    def is_shutdown_requested(self) -> bool:
        return self._shutdown_requested

    def request_shutdown(self, timeout_seconds=10, exit_code=0):
        """
        Initiates the shutdown sequence. 
        Waits for active tasks to drain up to `timeout_seconds`.
        Then forces exit.
        """
        self._shutdown_requested = True
        logger.info(f"Shutdown requested. Waiting up to {timeout_seconds}s for {len(self._active_tasks)} active tasks...")

        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            with self._lock:
                if not self._active_tasks:
                    logger.info("All tasks completed. Shutting down gracefully now.")
                    break

            time.sleep(0.5)
            # Log periodic status
            with self._lock:
                logger.info(f"Waiting for tasks: {list(self._active_tasks.values())}")

        with self._lock:
            if self._active_tasks:
                logger.warning(f"Shutdown timeout reached! Forcing exit with {len(self._active_tasks)} active tasks: {list(self._active_tasks.values())}")
            else:
                logger.info("Graceful shutdown successful.")

        logger.info("Exiting application process.")
        os._exit(exit_code)

# Global instance
shutdown_manager = GracefulShutdownManager()
