# ai_assistant/core/internal_prompts.py
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# In-memory store for internal prompts.
# In a real system, these might be loaded from a configuration file or database
# and updates might be persisted. For now, this is a simple in-memory dictionary.
INTERNAL_PROMPTS: Dict[str, str] = {
    "conversational_acknowledgement_style": "When a user provides a command or information, acknowledge it briefly and positively. Examples: 'Got it!', 'Okay, I can do that.', 'Understood.', 'Sounds good.', 'Sure thing.' Avoid overly verbose acknowledgements unless more detail is natural.",
    "tool_failure_explanation_tone": "When a tool execution results in an error that needs to be conveyed to the user, explain the core issue clearly and concisely without excessive technical jargon. If possible and appropriate, suggest that you can try an alternative or ask the user for clarification. Maintain a helpful and problem-solving tone.",
    "example_operational_guideline": "Default to using the 'search_web' tool for general knowledge questions unless a more specialized information retrieval tool is clearly a better fit based on the query context."
}

def get_internal_prompt(identifier: str) -> Optional[str]:
    """
    Retrieves an internal prompt string by its identifier.

    Args:
        identifier: The unique key for the prompt.

    Returns:
        The prompt string if found, otherwise None.
    """
    prompt_content = INTERNAL_PROMPTS.get(identifier)
    if prompt_content is None:
        logger.warning(f"Internal prompt with identifier '{identifier}' not found.")
    return prompt_content

def update_internal_prompt(identifier: str, new_content: str) -> bool:
    """
    Updates an internal prompt string in memory for the current session.
    Note: This does not persist changes beyond the current session in this basic version.

    Args:
        identifier: The unique key for the prompt to update.
        new_content: The new string content for the prompt.

    Returns:
        True if the prompt was found and updated, False otherwise.
    """
    if identifier in INTERNAL_PROMPTS:
        old_content = INTERNAL_PROMPTS[identifier]
        INTERNAL_PROMPTS[identifier] = new_content
        logger.info(f"Internal prompt '{identifier}' updated in memory for the current session.")
        logger.debug(f"Old content for '{identifier}': '{old_content[:100]}...'")
        logger.debug(f"New content for '{identifier}': '{new_content[:100]}...'")
        # TODO: In a future enhancement, this change should be persisted (e.g., to a config file or DB)
        # and potentially versioned or require approval before becoming active in new sessions.
        return True
    else:
        logger.warning(f"Attempted to update non-existent internal prompt with identifier '{identifier}'. No changes made.")
        return False

def list_internal_prompt_identifiers() -> list[str]:
    """Returns a list of available internal prompt identifiers."""
    return list(INTERNAL_PROMPTS.keys())

if __name__ == '__main__': # pragma: no cover
    print("--- Testing Internal Prompts Registry ---")

    # Test get
    ack_prompt = get_internal_prompt("conversational_acknowledgement_style")
    print(f"Acknowledgement Style Prompt: {ack_prompt[:50]}...")
    assert ack_prompt is not None

    non_existent = get_internal_prompt("non_existent_prompt")
    print(f"Non-existent prompt: {non_existent}")
    assert non_existent is None

    # Test update
    print("\n--- Updating 'conversational_acknowledgement_style' ---")
    original_ack = get_internal_prompt("conversational_acknowledgement_style")
    update_success = update_internal_prompt("conversational_acknowledgement_style", "Always say 'Roger that!'")
    print(f"Update success: {update_success}")
    assert update_success

    new_ack_prompt = get_internal_prompt("conversational_acknowledgement_style")
    print(f"New Acknowledgement Style Prompt: {new_ack_prompt}")
    assert new_ack_prompt == "Always say 'Roger that!'"

    print("\n--- Attempting to update non-existent prompt ---")
    update_fail = update_internal_prompt("fake_prompt_id", "new content")
    print(f"Update failure (expected): {update_fail}")
    assert not update_fail

    print("\n--- Listing identifiers ---")
    identifiers = list_internal_prompt_identifiers()
    print(f"Available identifiers: {identifiers}")
    assert "conversational_acknowledgement_style" in identifiers
    assert "tool_failure_explanation_tone" in identifiers

    # Restore original for other potential tests if this module were imported
    if original_ack:
         update_internal_prompt("conversational_acknowledgement_style", original_ack)

    print("\n--- Internal Prompts Registry Tests Finished ---")
