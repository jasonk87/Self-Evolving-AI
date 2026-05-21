import os
import sys
import json
import unittest

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__))))

from ai_assistant.core.config_manager import ConfigManager
import ai_assistant.config as config_module

class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.manager = ConfigManager()
        # Backup existing config.json if needed, or just mock? 
        # Using real file for integration verification as requested.
        
    def test_update_and_load(self):
        original_mode = config_module.DEFAULT_EXECUTION_MODE
        test_mode = "DIRECT" if original_mode != "DIRECT" else "FAST_REACT"
        
        print(f"Original Mode: {original_mode}")
        
        # Update
        success = self.manager.update_setting("DEFAULT_EXECUTION_MODE", test_mode)
        self.assertTrue(success)
        self.assertEqual(config_module.DEFAULT_EXECUTION_MODE, test_mode)
        
        # Verify persistence
        with open(self.manager.config_path, 'r') as f:
            data = json.load(f)
        self.assertEqual(data["DEFAULT_EXECUTION_MODE"], test_mode)
        print(f"Update verified on disk: {test_mode}")
        
        # Revert
        self.manager.update_setting("DEFAULT_EXECUTION_MODE", original_mode)
        self.assertEqual(config_module.DEFAULT_EXECUTION_MODE, original_mode)
        print("Reverted successfully.")

if __name__ == '__main__':
    unittest.main()
