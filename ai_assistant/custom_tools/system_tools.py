from typing import Any
from typing import Optional
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
    status = 'ENABLED' if config.GHOST_MODE else 'DISABLED'
    message = f"Ghost Mode {status} - Browser will now be {('visible' if config.GHOST_MODE else 'invisible')}."
    return message

def list_available_tools(category: str=None) -> str:
    """
    Lists all available tools registered in the system.

    Args:
        category (str, optional): Filter tools by category (e.g., 'system', 'custom').

    Returns:
        str: A formatted list of tools and their descriptions.
    """
    try:
        from ai_assistant.tools.tool_system import tool_system_instance
        tools = tool_system_instance.list_tools_with_sources()
    except ModuleNotFoundError as e:
        return f'Error loading tools: Module not found: {e}'
    except Exception as e:
        return f'Error loading tools: {e}'
    output_lines = ['Available Tools:']
    for name, details in sorted(tools.items()):
        tool_type = details.get('type', 'unknown')
        if category:
            if category.lower() not in tool_type.lower() and category.lower() not in details.get('module_path', '').lower():
                continue
        desc = details.get('description', 'No description.')
        output_lines.append(f'- {name}: {desc}')
    if len(output_lines) == 1:
        return 'No tools found.'
    return '\n'.join(output_lines)