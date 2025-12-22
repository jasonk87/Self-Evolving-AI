
import threading
import signal
import logging
import sys
import time

logger = logging.getLogger(__name__)

class ShutdownManager:
    def __init__(self):
        self.stop_event = threading.Event()
        self._shutdown_handlers = []
        self._is_shutting_down = False
    
    def register_handler(self, handler):
        """Register a callable to be executed during shutdown."""
        self._shutdown_handlers.append(handler)

    def should_continue(self):
        return not self.stop_event.is_set()

    def request_shutdown(self, signum=None, frame=None, timeout_seconds=5):
        if self._is_shutting_down:
            return
        
        self._is_shutting_down = True
        logger.info(f"Shutdown requested (Signal: {signum}). Signaling threads to stop...")
        self.stop_event.set()
        
        # Execute handlers
        for handler in self._shutdown_handlers:
            try:
                handler()
            except Exception as e:
                logger.error(f"Error in shutdown handler: {e}")
        
        logger.info("Shutdown handlers executed. Waiting for graceful exit...")
        # We generally rely on the main thread loop or joined threads to exit now.
        # If called from a signal, we typically let the signal handler return so the main thread checks flags.

shutdown_manager = ShutdownManager()

def register_signal_handlers():
    def signal_wrapper(signum, frame):
        shutdown_manager.request_shutdown(signum, frame)
        # Do not force sys.exit immediately if we want threads to join, 
        # but Python signal handlers raise KeyboardInterrupt on SIGINT often.
        # We'll set the flag and simple let the app rely on check loops.
        
    signal.signal(signal.SIGINT, signal_wrapper)
    signal.signal(signal.SIGTERM, signal_wrapper)
