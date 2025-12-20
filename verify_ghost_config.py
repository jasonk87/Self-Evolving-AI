
import os
import sys
import json
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from ai_assistant.core.config_manager import ConfigManager
import ai_assistant.config as config_module

# Setup basic logging
logging.basicConfig(level=logging.INFO)

def verify_ghost_mode_config():
    print("--- Verifying ConfigManager Ghost Mode Support ---")
    
    cm = ConfigManager()
    
    # 1. Check if GHOST_MODE is in get_all_settings
    settings = cm.get_all_settings()
    if 'GHOST_MODE' not in settings:
        print("FAIL: GHOST_MODE not found in get_all_settings()")
        return False
    else:
        print(f"PASS: GHOST_MODE found in settings (Current Value: {settings['GHOST_MODE']})")

    # 2. Test updating GHOST_MODE
    original_value = config_module.GHOST_MODE
    new_value = not original_value
    
    print(f"Attempting to update GHOST_MODE from {original_value} to {new_value}...")
    success = cm.update_setting('GHOST_MODE', new_value)
    
    if not success:
        print("FAIL: update_setting returned False")
        return False
        
    # Verify logical update
    if config_module.GHOST_MODE != new_value:
        print(f"FAIL: config_module.GHOST_MODE was not updated in memory. Got {config_module.GHOST_MODE}")
        return False
    else:
        print("PASS: In-memory update successful")

    # Verify persistence (read from file manually)
    try:
        with open(cm.config_path, 'r') as f:
            saved_data = json.load(f)
            if saved_data.get('GHOST_MODE') != new_value:
                 print(f"FAIL: config.json was not updated. Got {saved_data.get('GHOST_MODE')}")
                 return False
            else:
                 print("PASS: config.json persistence successful")
    except Exception as e:
        print(f"FAIL: Could not read config.json: {e}")
        return False

    # Restore original value
    cm.update_setting('GHOST_MODE', original_value)
    print(f"Restored GHOST_MODE to {original_value}")
    
    print("--- Verification Complete: SUCCESS ---")
    return True

if __name__ == "__main__":
    if verify_ghost_mode_config():
        sys.exit(0)
    else:
        sys.exit(1)
