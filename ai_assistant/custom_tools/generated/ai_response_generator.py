from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ai_assistant.core.action_executor import ActionExecutor

async def generate_two_responses(action_executor: "ActionExecutor", prompt: str) -> str:
    """
    Generates two back-to-back responses from the AI for a given prompt.

    Args:
        action_executor (ActionExecutor): The action executor used to interact with the AI.
        prompt (str): The prompt to send to the AI.

    Returns:
        str: A string containing two responses from the AI, separated by '---RESPONSE_SEPARATOR---'.
             Returns an error message if the responses could not be generated.
    """
    try:
        response1 = await action_executor.run_prompt(prompt)
        response2 = await action_executor.run_prompt(prompt)

        if response1 is None or response2 is None:
            return "Error: Could not generate both responses."

        return f"{response1}\n---RESPONSE_SEPARATOR---\n{response2}"
    except Exception as e:
        return f"Error: {e}"