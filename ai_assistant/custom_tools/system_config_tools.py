from typing import Any, Dict, Optional, Union
import logging
from ai_assistant.core.config_manager import ConfigManager

# Initialize ConfigManager instance
config_manager = ConfigManager()

def set_ghost_mode(enabled: bool) -> str:
    """
    Enables or disables "Ghost Mode" (visible browser automation).
    
    Args:
        enabled (bool): True to enable Ghost Mode (visible browser), False to disable (headless).
        
    Returns:
        str: Status message.
    """
    try:
        success = config_manager.update_setting("GHOST_MODE", enabled)
        if success:
            state = "ENABLED" if enabled else "DISABLED"
            return f"Ghost Mode has been {state}. Browser sessions will now be {'visible' if enabled else 'hidden'}."
        else:
            return "Error: Failed to update Ghost Mode setting."
    except Exception as e:
        return f"Error setting Ghost Mode: {e}"

def update_system_config(key: str, value: Any) -> str:
    """
    Updates a system configuration setting.
    Restricted to safe keys to prevent system instability.
    
    Args:
        key (str): The configuration key to update (e.g., "ENABLE_THINKING").
        value (Any): The new value.
        
    Returns:
        str: Status message.
    """
    # Whitelist of modifiable settings
    ALLOWED_KEYS = {
        "GHOST_MODE",
        "ENABLE_THINKING",
        "ENABLE_CHAIN_OF_THOUGHT",
        "DEFAULT_EXECUTION_MODE",
        "AUTONOMOUS_LEARNING_ENABLED",
        "AUTO_APPROVE_DELAY_SECONDS",
        "VERBOSE_LLM_LOGGING",
        "ENABLE_DREAM_MODE",
        "DREAM_INTERVAL_SECONDS",
        "CIRCUIT_BREAKER_THRESHOLD"
    }

    if key not in ALLOWED_KEYS:
        return f"Error: Configuration key '{key}' is not modifiable via tools. Allowed keys: {', '.join(sorted(ALLOWED_KEYS))}"

    try:
        # Determine value type if string is passed but boolean/int needed
        # (This tool receives arguments likely as typed by the LLM, but let's be safe)
        if isinstance(value, str):
            if value.lower() == 'true': value = True
            elif value.lower() == 'false': value = False
            elif value.isdigit(): value = int(value)

        success = config_manager.update_setting(key, value)
        if success:
            return f"Successfully updated '{key}' to {value}."
        else:
            return f"Error: Failed to update '{key}'."
    except Exception as e:
        return f"Error updating config: {e}"

def get_system_config(key: Optional[str] = None) -> Union[str, Dict[str, Any]]:
    """
    Retrieves the current system configuration.
    
    Args:
        key (Optional[str]): Specific key to retrieve. If None, returns all modifiable settings.
        
    Returns:
        str or dict: The configuration value(s).
    """
    settings = config_manager.get_all_settings()
    
    if key:
        val = settings.get(key)
        if val is None:
            return f"Setting '{key}' not found."
        return str(val)
    return settings
