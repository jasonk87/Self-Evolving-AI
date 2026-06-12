from ai_assistant.core.agency.architect import SystemArchitect

# Create a tool-compatible wrapper
async def run_architect_cycle() -> str:
    """
    Triggers the System Architect's self-diagnosis and goal proposal cycle.
    Analyzes system logs and episodic memory to identify issues and create maintenance goals.

    Returns:
        A status message summarizing the result of the cycle.
    """
    architect = SystemArchitect()
    result = await architect.run_cycle()
    return result

# Explicitly export the function to be picked up by ToolSystem
__all__ = ['run_architect_cycle']
