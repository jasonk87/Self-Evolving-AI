# Code for contextual awareness, including tool-goal associations.
from typing import Dict, List, Set

# _tool_goal_links: Maps tool_name to a SET of goal_descriptions for which it was successfully used.
# Using a set for goal_descriptions to automatically handle duplicates.
_tool_goal_links: Dict[str, Set[str]] = {}

def record_tool_goal_association(tool_name: str, goal_description: str) -> None:
    """
    Records that a tool was successfully used to achieve a given goal.
    Ensures that goal_description is unique for the given tool_name.

    Args:
        tool_name: The name of the tool.
        goal_description: The description of the goal achieved.
    """
    if tool_name not in _tool_goal_links:
        _tool_goal_links[tool_name] = set()
    
    # Add the goal description to the set for this tool.
    # Sets automatically handle uniqueness.
    _tool_goal_links[tool_name].add(goal_description)
    print(f"Awareness: Recorded association - Tool: '{tool_name}', Goal: '{goal_description}'")


def get_tool_associations(tool_name: str) -> List[str]:
    """
    Retrieves a list of goal descriptions associated with a tool.

    Args:
        tool_name: The name of the tool.

    Returns:
        A list of unique goal descriptions, or an empty list if none found.
    """
    # Convert set to list for the return type.
    return list(_tool_goal_links.get(tool_name, set()))

if __name__ == '__main__':
    print("--- Testing Tool-Goal Association ---")

    # Test recording
    record_tool_goal_association("add_numbers", "Calculate sum of 5 and 7")
    record_tool_goal_association("add_numbers", "Find total of 10 and 20")
    record_tool_goal_association("add_numbers", "Calculate sum of 5 and 7") # Duplicate, should be ignored by set

    record_tool_goal_association("greet_user", "Greet Alice")

    # Test retrieval
    print("\nAssociations for 'add_numbers':")
    add_assoc = get_tool_associations("add_numbers")
    print(add_assoc)
    assert len(add_assoc) == 2, "Should have 2 unique goals for add_numbers"
    assert "Calculate sum of 5 and 7" in add_assoc
    assert "Find total of 10 and 20" in add_assoc

    print("\nAssociations for 'greet_user':")
    greet_assoc = get_tool_associations("greet_user")
    print(greet_assoc)
    assert len(greet_assoc) == 1
    assert "Greet Alice" in greet_assoc

    print("\nAssociations for 'unknown_tool':")
    unknown_assoc = get_tool_associations("unknown_tool")
    print(unknown_assoc)
    assert len(unknown_assoc) == 0

    # Verify internal state
    print("\nInternal _tool_goal_links state:")
    print(_tool_goal_links)
    assert len(_tool_goal_links["add_numbers"]) == 2

    print("\n--- Tool-Goal Association Tests Finished ---")
