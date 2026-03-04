import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_assistant.core.action_executor import ActionExecutor


async def _call_run_prompt(action_executor: "ActionExecutor", prompt: str):
    """Call run_prompt supporting both sync and async implementations."""
    result = action_executor.run_prompt(prompt)
    if inspect.isawaitable(result):
        return await result
    return result


async def generate_two_responses(action_executor: "ActionExecutor", prompt: str) -> str:
    """Generate two responses separated by a fixed delimiter."""
    if action_executor is None:
        raise AttributeError("action_executor is required")

    try:
        response1 = await _call_run_prompt(action_executor, prompt)
        response2 = await _call_run_prompt(action_executor, prompt)

        if response1 is None or response2 is None:
            return "Error: Could not generate both responses."

        return f"{response1}\n---RESPONSE_SEPARATOR---\n{response2}"
    except Exception as e:
        return f"Error: {e}"
