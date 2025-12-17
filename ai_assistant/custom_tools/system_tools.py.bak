
import ai_assistant.config as config

def toggle_ghost_mode(enabled: bool) -> str:
    """
    Toggles 'Ghost Mode' (visible browser execution) for DeepResearcher and VisionService.

    Args:
        enabled (bool): True to enable visible browser (Ghost Mode), False for headless (invisible).

    Returns:
        str: Confirmation message.
    """
    config.GHOST_MODE = enabled

    status = "ENABLED" if config.GHOST_MODE else "DISABLED"
    message = f"Ghost Mode {status} - Browser will now be {'visible' if config.GHOST_MODE else 'invisible'}."

    return message
