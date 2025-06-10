# Self-Evolving-Agent-feat-chat-history-context/ai_assistant/custom_tools/conversational_tools.py
from typing import Optional, List, Dict, Any

# For simplicity in tool execution context, using basic print and input.
# If advanced CLI styling is needed here, robust path handling for display_utils would be required.
# from ...utils.display_utils import color_text, CLIColors, format_message, format_input_prompt # Example path

def request_user_clarification(question_text: str, options: Optional[List[str]] = None) -> str:
    """
    Asks the user a clarifying question and returns their textual response.
    This tool is intended to be called by the AI planner when it needs more information
    or needs to resolve ambiguity to proceed with a task.

    Args:
        question_text: The question to ask the user.
        options: Optional. A list of suggested options for the user to choose from.

    Returns:
        The user's textual reply.
    """
    # Basic print statements for interaction within the tool's execution thread.
    # These will not use the main CLI's prompt_toolkit styling directly.
    print(f"\n--- AI Assistant Needs Clarification ---")
    print(question_text)

    if options and isinstance(options, list) and len(options) > 0:
        print("Options:")
        for i, opt in enumerate(options):
            print(f"  {i+1}. {opt}")
        # Future enhancement: could add logic to map numeric choice back to option text if desired.

    prompt_message = "Your response: "
    try:
        # Standard input() will block the current thread, which is expected for this tool.
        # The main CLI runs in asyncio loop, ToolSystem runs sync tools in threads.
        user_response = input(prompt_message)
    except EOFError: # pragma: no cover
        # Handle cases where input might be unexpectedly closed (e.g., non-interactive script execution)
        user_response = "User did not provide a response (EOF)."
    except KeyboardInterrupt: # pragma: no cover
        # Handle cases where user might Ctrl+C during input
        user_response = "User cancelled input."

    print(f"--- End of Clarification ---\n")
    return user_response.strip()

# Conceptual Schema for ToolSystem registration
REQUEST_USER_CLARIFICATION_SCHEMA = {
    "name": "request_user_clarification",
    "description": "Asks the user a clarifying question to resolve ambiguities or gather missing information needed to complete a task. Returns the user's textual response.",
    "parameters": [
        {"name": "question_text", "type": "str", "description": "The question to ask the user."},
        {"name": "options", "type": "list", "description": "Optional. A list of suggested string options for the user to choose from or consider."}
    ],
    "returns": {
        "type": "str",
        "description": "The user's textual reply to the clarification question."
    }
}

if __name__ == '__main__': # pragma: no cover
    from unittest.mock import patch
    import datetime # Imported for the example in _print_notifications_list, though not directly used here.

    print("--- Testing conversational_tools.py ---")

    # Test request_user_clarification
    print("\n--- Test 1: Question with no options ---")
    with patch('builtins.input', return_value="User says yes, proceed."):
        response1 = request_user_clarification("Are you sure you want to format the drive?")
        print(f"Response 1: {response1}")
        assert response1 == "User says yes, proceed."

    print("\n--- Test 2: Question with options, user chooses number ---")
    with patch('builtins.input', return_value="2"): # User chooses option 2
        response2 = request_user_clarification(
            "Which project do you mean?",
            options=["Project Alpha", "Project Beta", "Project Gamma (new)"]
        )
        print(f"Response 2: {response2}")
        assert response2 == "2" # Tool currently returns the raw input

    print("\n--- Test 3: Question with options, user types full option (simulated) ---")
    with patch('builtins.input', return_value="Project Beta"):
        response3 = request_user_clarification(
            "Which project do you mean?",
            options=["Project Alpha", "Project Beta", "Project Gamma (new)"]
        )
        print(f"Response 3: {response3}")
        assert response3 == "Project Beta"

    print("\n--- Conversational Tools Test Finished ---")
