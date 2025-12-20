import sys
import os
import time
import threading
import unittest

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from ai_assistant.core.shutdown_handler import GracefulShutdownManager

class TestGracefulShutdown(unittest.TestCase):
    def setUp(self):
        self.manager = GracefulShutdownManager()
        # Mock os._exit to prevent actual exit during test
        self.original_exit = os._exit
        self.exit_code = None
        os._exit = self.mock_exit

    def tearDown(self):
        os._exit = self.original_exit

    def mock_exit(self, code):
        self.exit_code = code
        print(f"Mock exit called with code {code}")
        # In a real app this stops execution, here we just return to specificy it was called
        # But wait, request_shutdown is a loop. We need to raise StopIteration or similar to break it if it doesn't return
        raise SystemExit(code)

    def test_shutdown_waits_for_task(self):
        print("\nTesting graceful shutdown wait...")
        # 1. Register a task
        task_id = self.manager.register_task("Test Task")
        
        # 2. Start shutdown in a thread
        shutdown_thread = threading.Thread(target=self.run_shutdown)
        shutdown_thread.start()
        
        # 3. verify it hasn't exited immediately (give it 0.5s)
        time.sleep(0.5)
        self.assertTrue(shutdown_thread.is_alive())
        print("Shutdown is waiting (correct).")

        # 4. Complete task
        print("Completing task...")
        self.manager.complete_task(task_id)
        
        # 5. Wait for shutdown to finish
        shutdown_thread.join(timeout=2.0)
        self.assertFalse(shutdown_thread.is_alive())
        print("Shutdown completed successfully.")

    def run_shutdown(self):
        try:
            self.manager.request_shutdown(timeout_seconds=5)
        except SystemExit:
            pass

if __name__ == '__main__':
    unittest.main()
