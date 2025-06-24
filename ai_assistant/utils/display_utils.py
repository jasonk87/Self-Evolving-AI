from prompt_toolkit.formatted_text import ANSI

class CLIColors:
    # Base colors with better contrast
    USER_INPUT = '\033[38;5;111m'     # Soft blue for user input
    AI_RESPONSE = '\033[38;5;156m'    # Mint green for AI responses
    SYSTEM_MESSAGE = '\033[38;5;222m'  # Warm yellow for system messages
    ERROR_MESSAGE = '\033[38;5;203m'   # Coral red for errors
    DEBUG_MESSAGE = '\033[38;5;245m'   # Medium grey for debug
    TOOL_OUTPUT = '\033[38;5;123m'     # Bright cyan for tool output
    SUCCESS = '\033[38;5;149m'         # Soft green for success
    FAILURE = '\033[38;5;196m'         # Deep red for failures
    WARNING = '\033[38;5;214m'         # Orange for warnings
    
    # Special formatting
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'
    UNDERLINE = '\033[4m'
    
    # Status indicators
    THINKING = '\033[38;5;147m'        # Purple for thinking/processing
    INPUT_PROMPT = '\033[38;5;117m'    # Light blue for input prompt
    COMMAND = '\033[38;5;208m'         # Orange for commands
    BORDER = '\033[38;5;240m'          # Dark grey for borders/separators
    TOOL_NAME = "\033[94m"     # Bright Blue (same as SYSTEM_MESSAGE, can be distinct e.g. Light Blue \033[94m)
    TOOL_ARGS = "\033[36m"     # Cyan (regular, distinct from TOOL_OUTPUT if desired)
    
    BLUE = '\033[34m'          # Standard ANSI blue for the new prompt
    # Component-specific colors (for debug mode)
    PLANNER = '\033[38;5;105m'    # Purple for planner
    REVIEWER = '\033[38;5;208m'   # Orange for reviewer
    THINKER = '\033[38;5;39m'     # Blue for thinking process
    EXECUTOR = '\033[38;5;34m'    # Green for executor
    ACTION_PLAN = '\033[38;5;226m' # Yellow for final action plan
    
    END_COLOR = '\033[0m'         # Resets the color

def color_text(text: str, color_code: str) -> str:
    """Add color to text with proper reset"""
    return f"{color_code}{text}{CLIColors.END_COLOR}"

def format_header(text: str) -> ANSI:
    """Format a section header with borders"""
    width = 60
    border = color_text('─' * width, CLIColors.BORDER)
    padded_text = text.center(width - 2)
    final_str = f"\n{border}\n{color_text(f' {padded_text} ', CLIColors.BOLD + CLIColors.SYSTEM_MESSAGE)}\n{border}"
    return ANSI(final_str)

def format_message(prefix: str, message: str, color: str, show_prefix: bool = True) -> ANSI:
    """Format a message with proper indentation and optional prefix"""
    prefix_str = f"{color_text(prefix, color)} " if show_prefix else ""
    indented_message = message.replace('\n', f'\n{"  " if show_prefix else ""}')
    final_str = f"{prefix_str}{color_text(indented_message, color)}"
    return ANSI(final_str)

def format_input_prompt() -> ANSI:
    """Format the input prompt with a visually appealing indicator"""
    # Original prompt:
    # arrow = color_text("→", CLIColors.INPUT_PROMPT + CLIColors.BOLD)
    # return f"\n{arrow} {color_text('', CLIColors.USER_INPUT)}"
    # New prompt: A blue ">" followed by a space
    return ANSI(f"{CLIColors.BLUE}>{CLIColors.END_COLOR} ")

def format_thinking() -> ANSI:
    """Format the 'thinking' indicator"""
    final_str = color_text("⋯ thinking", CLIColors.THINKING + CLIColors.ITALIC)
    return ANSI(final_str)

def format_tool_execution(tool_name: str) -> ANSI:
    """Format tool execution message"""
    final_str = color_text(f"[{tool_name}]", CLIColors.TOOL_OUTPUT + CLIColors.BOLD)
    return ANSI(final_str)

def format_status(status: str, success: bool = True) -> ANSI:
    """Format a status message"""
    icon = "✓" if success else "✗"
    color = CLIColors.SUCCESS if success else CLIColors.FAILURE
    final_str = color_text(f"{icon} {status}", color + CLIColors.BOLD)
    return ANSI(final_str)

def draw_separator() -> ANSI:
    """Draw a subtle separator line"""
    final_str = color_text("─" * 60, CLIColors.BORDER + CLIColors.DIM)
    return ANSI(final_str)

def format_component_output(component: str, message: str, is_thinking: bool = False) -> ANSI:
    """Format output from different AI components (planner, reviewer, etc)
    
    Args:
        component: The name of the component (planner, reviewer, etc)
        message: The message to format
        is_thinking: Whether this is part of the thinking process
    """
    from ai_assistant.config import is_debug_mode, THINKING_CONFIG
    
    if not is_debug_mode():
        if is_thinking and not THINKING_CONFIG["display"]["show_in_release"]:
            return ANSI("")
        return ANSI(message)
        
    component_colors = {
        "planner": CLIColors.PLANNER,
        "reviewer": CLIColors.REVIEWER,
        "executor": CLIColors.EXECUTOR,
        "thinker": CLIColors.THINKER
    }
    
    prefix = THINKING_CONFIG["components"].get(component.lower(), "")
    color = component_colors.get(component.lower(), CLIColors.AI_RESPONSE)
    
    # Special handling for thinking process
    if is_thinking:
        if not THINKING_CONFIG["display"]["show_working"]:
            return ANSI("")
        steps = message.split("\n")
        formatted_steps = []
        for step in steps:
            formatted_steps.append(color_text(f"{THINKING_CONFIG['display']['step_prefix']}{step}", color))
        final_str = "\n".join(formatted_steps)
        return ANSI(final_str)
    
    final_str = color_text(f"{prefix}{message}", color)
    return ANSI(final_str)