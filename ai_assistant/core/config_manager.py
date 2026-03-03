import json
import os
import logging
import ai_assistant.config as config_module

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "config.json"

class ConfigManager:
    """
    Manages loading, saving, and updating configuration settings.
    Ensures that changes are persisted to disk and reflected in the runtime `config` module.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance.config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), CONFIG_FILE_NAME)
            cls._instance.load_config()
        return cls._instance

    def load_config(self):
        """Loads config from JSON and updates the runtime config module."""
        if not os.path.exists(self.config_path):
            self.save_current_defaults() # Create initial file
            return

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                saved_config = json.load(f)
            
            # Update module variables
            for key, value in saved_config.items():
                if hasattr(config_module, key):
                    setattr(config_module, key, value)
                    logger.info(f"Config loaded: {key} = {value}")
                else:
                    logger.warning(f"Unknown config key in JSON: {key}")
                    
        except Exception as e:
            logger.error(f"Failed to load config.json: {e}")

    def save_current_defaults(self):
        """Saves the current values from config.py to config.json (initial seed)."""
        data = {
            "DEFAULT_EXECUTION_MODE": config_module.DEFAULT_EXECUTION_MODE,
            "ENABLE_THINKING": config_module.ENABLE_THINKING,
            "DEFAULT_MODEL": config_module.DEFAULT_MODEL,
            "TASK_MODELS": config_module.TASK_MODELS,
            "REASONING_STRATEGIES": config_module.REASONING_STRATEGIES,
            "CONVERSATION_HISTORY_TURNS": config_module.CONVERSATION_HISTORY_TURNS,
            "AUTONOMOUS_LEARNING_ENABLED": config_module.AUTONOMOUS_LEARNING_ENABLED,
            "AUTO_APPROVE_DELAY_SECONDS": config_module.AUTO_APPROVE_DELAY_SECONDS,
            "PARALLEL_THINKING_CONFIG": config_module.PARALLEL_THINKING_CONFIG,
            "GHOST_MODE": config_module.GHOST_MODE
        }
        self._write_json(data)

    def update_setting(self, key: str, value):
        """Updates a setting in memory and on disk."""
        if hasattr(config_module, key):
            # 1. Update In-Memory
            setattr(config_module, key, value)
            
            # 2. Update Disk
            current_data = self._read_json()
            current_data[key] = value
            self._write_json(current_data)
            
            logger.info(f"Config updated: {key} -> {value}")
            return True
        else:
            logger.error(f"Attempted to update non-existent config key: {key}")
            return False

    def get_all_settings(self):
        """Returns a dict of all managed settings."""
        # Refresh from module to ensure sync
        return {
            "DEFAULT_EXECUTION_MODE": config_module.DEFAULT_EXECUTION_MODE,
            "ENABLE_THINKING": config_module.ENABLE_THINKING,
            "DEFAULT_MODEL": config_module.DEFAULT_MODEL,
            "TASK_MODELS": config_module.TASK_MODELS,
            "REASONING_STRATEGIES": config_module.REASONING_STRATEGIES,
            "CONVERSATION_HISTORY_TURNS": config_module.CONVERSATION_HISTORY_TURNS,
            "AUTONOMOUS_LEARNING_ENABLED": config_module.AUTONOMOUS_LEARNING_ENABLED,
            "AUTO_APPROVE_DELAY_SECONDS": config_module.AUTO_APPROVE_DELAY_SECONDS,
            "PARALLEL_THINKING_CONFIG": config_module.PARALLEL_THINKING_CONFIG,
            "GHOST_MODE": config_module.GHOST_MODE
        }

    def _read_json(self):
        if os.path.exists(self.config_path):
             try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
             except:
                 return {}
        return {}

    def _write_json(self, data):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to write config.json: {e}")
