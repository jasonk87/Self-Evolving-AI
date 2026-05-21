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
            "DEFAULT_MODEL": config_module.DEFAULT_MODEL,
            "TASK_MODELS": config_module.TASK_MODELS,
            "REASONING_STRATEGIES": config_module.REASONING_STRATEGIES,
            "CONVERSATION_HISTORY_TURNS": config_module.CONVERSATION_HISTORY_TURNS,
            "AUTONOMOUS_LEARNING_ENABLED": config_module.AUTONOMOUS_LEARNING_ENABLED,
            "AUTO_APPROVE_DELAY_SECONDS": config_module.AUTO_APPROVE_DELAY_SECONDS,
            "GHOST_MODE": config_module.GHOST_MODE,
            "AUTO_WEB_PIP": config_module.AUTO_WEB_PIP,
            "REMINDER_CHECK_INTERVAL_SECONDS": config_module.REMINDER_CHECK_INTERVAL_SECONDS,
            "DREAM_INTERVAL_SECONDS": config_module.DREAM_INTERVAL_SECONDS,
            "ENABLE_DREAM_MODE": config_module.ENABLE_DREAM_MODE,
            "TASK_PROFILES": config_module.TASK_PROFILES,
            "DAILY_TOKEN_BUDGET": getattr(config_module, 'DAILY_TOKEN_BUDGET', 2000000),
            "AUTONOMOUS_BURN_RATE_LIMIT": getattr(config_module, 'AUTONOMOUS_BURN_RATE_LIMIT', 50000),
            "CATEGORY_BUDGETS": getattr(config_module, 'CATEGORY_BUDGETS', {}),
            "ALLOW_DREAMER": getattr(config_module, 'ALLOW_DREAMER', True),
            "ALLOW_MEMORY_LEARNING": getattr(config_module, 'ALLOW_MEMORY_LEARNING', True),
            "ALLOW_AUTO_FIXING": getattr(config_module, 'ALLOW_AUTO_FIXING', True)
        }
        self._write_json(data)

    def is_managed_setting(self, key: str) -> bool:
        """Returns True when key is part of managed runtime settings."""
        return key in self.get_all_settings()

    def coerce_setting_value(self, key: str, raw_value):
        """Coerces user/API-provided value using setting schema metadata."""
        schema = self.get_settings_schema().get(key)
        if not schema:
            raise ValueError(f"Unknown setting '{key}'")

        value_type = schema.get("type")
        value = raw_value

        if value_type == "boolean":
            if isinstance(raw_value, bool):
                value = raw_value
            elif isinstance(raw_value, str):
                normalized = raw_value.strip().lower()
                if normalized in {"true", "1", "yes", "on"}:
                    value = True
                elif normalized in {"false", "0", "no", "off"}:
                    value = False
                else:
                    raise ValueError("Expected boolean value (true/false)")
            else:
                value = bool(raw_value)

        elif value_type == "integer":
            value = int(raw_value)

        elif value_type == "number":
            value = float(raw_value)

        elif value_type == "object":
            if isinstance(raw_value, (dict, list)):
                value = raw_value
            elif isinstance(raw_value, str):
                value = json.loads(raw_value)
            else:
                raise ValueError("Expected JSON object/array value")

        elif value_type == "string":
            value = str(raw_value)

        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"Value must be one of: {', '.join(schema['enum'])}")

        return value

    def update_setting(self, key: str, value):
        """Updates a setting in memory and on disk."""
        if not self.is_managed_setting(key):
            logger.error(f"Attempted to update non-managed config key: {key}")
            return False

        setattr(config_module, key, value)

        current_data = self._read_json()
        current_data[key] = value
        self._write_json(current_data)

        logger.info(f"Config updated: {key} -> {value}")
        return True

    def get_all_settings(self):
        """Returns a dict of all managed settings."""
        # Check module state but also incorporate dynamic values that might be added to module
        # without explicitly declaring a property beforehand
        return {
            "DEFAULT_EXECUTION_MODE": getattr(config_module, 'DEFAULT_EXECUTION_MODE', "AUTO"),
            "DEFAULT_MODEL": getattr(config_module, 'DEFAULT_MODEL', "gemini-2.5-flash-lite"),
            "TASK_MODELS": getattr(config_module, 'TASK_MODELS', {}),
            "REASONING_STRATEGIES": getattr(config_module, 'REASONING_STRATEGIES', {}),
            "CONVERSATION_HISTORY_TURNS": getattr(config_module, 'CONVERSATION_HISTORY_TURNS', 5),
            "AUTONOMOUS_LEARNING_ENABLED": getattr(config_module, 'AUTONOMOUS_LEARNING_ENABLED', True),
            "AUTO_APPROVE_DELAY_SECONDS": getattr(config_module, 'AUTO_APPROVE_DELAY_SECONDS', 600),
            "GHOST_MODE": getattr(config_module, 'GHOST_MODE', False),
            "AUTO_WEB_PIP": getattr(config_module, 'AUTO_WEB_PIP', True),
            "REMINDER_CHECK_INTERVAL_SECONDS": getattr(config_module, 'REMINDER_CHECK_INTERVAL_SECONDS', 10),
            "DREAM_INTERVAL_SECONDS": getattr(config_module, 'DREAM_INTERVAL_SECONDS', 86400),
            "ENABLE_DREAM_MODE": getattr(config_module, 'ENABLE_DREAM_MODE', False),
            "TASK_PROFILES": getattr(config_module, 'TASK_PROFILES', {}),
            "DAILY_TOKEN_BUDGET": getattr(config_module, 'DAILY_TOKEN_BUDGET', 2000000),
            "AUTONOMOUS_BURN_RATE_LIMIT": getattr(config_module, 'AUTONOMOUS_BURN_RATE_LIMIT', 50000),
            "CATEGORY_BUDGETS": getattr(config_module, 'CATEGORY_BUDGETS', {}),
            "ALLOW_DREAMER": getattr(config_module, 'ALLOW_DREAMER', True),
            "ALLOW_MEMORY_LEARNING": getattr(config_module, 'ALLOW_MEMORY_LEARNING', True),
            "ALLOW_AUTO_FIXING": getattr(config_module, 'ALLOW_AUTO_FIXING', True)
        }

    def get_settings_schema(self):
        """Returns editable setting metadata for API/assistant-driven configuration."""
        return {
            "DEFAULT_EXECUTION_MODE": {
                "type": "string",
                "description": "Default execution policy for new tasks.",
                "enum": ["AUTO", "FAST_REACT", "DIRECT"],
            },
            "DEFAULT_MODEL": {
                "type": "string",
                "description": "Default model alias for general operations.",
            },
            "TASK_MODELS": {
                "type": "object",
                "description": "Per-task model overrides.",
            },
            "REASONING_STRATEGIES": {
                "type": "object",
                "description": "Per-task strategy mapping. Gemini calls are direct by default.",
            },
            "CONVERSATION_HISTORY_TURNS": {
                "type": "integer",
                "description": "How many recent turns to include in chat context.",
            },
            "AUTONOMOUS_LEARNING_ENABLED": {
                "type": "boolean",
                "description": "Enables background autonomous learning loops.",
            },
            "AUTO_APPROVE_DELAY_SECONDS": {
                "type": "number",
                "description": "Delay before auto-approval executes (seconds).",
            },
            "GHOST_MODE": {
                "type": "boolean",
                "description": "Use visible browser windows for automation where supported.",
            },
            "AUTO_WEB_PIP": {
                "type": "boolean",
                "description": "Automatically capture picture-in-picture screenshots during web searches.",
            },
            "REMINDER_CHECK_INTERVAL_SECONDS": {
                "type": "integer",
                "description": "Polling interval for due reminder checks in background service.",
            },
            "DREAM_INTERVAL_SECONDS": {
                "type": "integer",
                "description": "Interval between Dreamer simulation runs when enabled and idle.",
            },
            "ENABLE_DREAM_MODE": {
                "type": "boolean",
                "description": "Enable autonomous Dreamer simulation cycle.",
            },
            "TASK_PROFILES": {
                "type": "object",
                "description": "Advanced task-based execution profiles and routing.",
            },
            "DAILY_TOKEN_BUDGET": {
                "type": "integer",
                "description": "Maximum total tokens allowed globally per day.",
            },
            "AUTONOMOUS_BURN_RATE_LIMIT": {
                "type": "integer",
                "description": "Maximum token spend allowed per background autonomous cycle.",
            },
            "CATEGORY_BUDGETS": {
                "type": "object",
                "description": "Per-category budgets for tokens.",
            },
            "ALLOW_DREAMER": {
                "type": "boolean",
                "description": "Allow background Dreamer processes to run.",
            },
            "ALLOW_MEMORY_LEARNING": {
                "type": "boolean",
                "description": "Allow background memory insight learning to run.",
            },
            "ALLOW_AUTO_FIXING": {
                "type": "boolean",
                "description": "Allow background autonomous file fixing to run.",
            }
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
