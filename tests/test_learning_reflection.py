import sys
import os
import asyncio

# Add project root to sys.path
# The script is expected to be in /app/, so Self-Evolving-Agent... is one level down.
project_root_parts = ["Self-Evolving-Agent-feat-learning-module", "Self-Evolving-Agent-feat-chat-history-context"]
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), *project_root_parts))

if project_root not in sys.path:
    sys.path.insert(0, project_root)

print(f"Python version: {sys.version}")
print(f"Project root: {project_root}")
print(f"sys.path: {sys.path}")

# Imports
try:
    from ai_assistant.learning.autonomous_learning import learn_facts_from_interaction
    from ai_assistant.core.autonomous_reflection import run_self_reflection_cycle
    from ai_assistant.tools.tool_system import tool_system_instance # Use the global instance
    from ai_assistant.core.notification_manager import NotificationManager
    # SuggestionManager is not directly passed to run_self_reflection_cycle,
    # but it is imported and used by the CLI's /review_insights command which calls
    # run_self_reflection_cycle and then select_suggestion_for_autonomous_action.
    # For this test, if run_self_reflection_cycle itself doesn't use it, we might not need it.
    # However, select_suggestion_for_autonomous_action (called in __main__ of autonomous_reflection.py)
    # might be implicitly tested if run_self_reflection_cycle saves suggestions that __main__ then picks up.
    # For now, let's assume SuggestionManager is not needed for the direct call to run_self_reflection_cycle.
    print("Successfully imported test components.")
except Exception as import_e:
    print(f"Error importing components: {type(import_e).__name__} - {import_e}")
    sys.exit(1)

# Test Learning
print("\n--- Testing Fact Learning ---")
sample_user_input = "The sky is blue during the day."
sample_ai_response = "Yes, that's correct. The sky's blue color is a result of Rayleigh scattering of sunlight in the Earth's atmosphere."
learned_facts_result = []
try:
    # learn_facts_from_interaction is async
    learned_facts_result = asyncio.run(learn_facts_from_interaction(sample_user_input, sample_ai_response, True))
    print(f"Learned facts: {learned_facts_result!r}")
except Exception as e_learn:
    print(f"Error during fact learning: {type(e_learn).__name__} - {e_learn}")
    import traceback
    traceback.print_exc()

# Test Reflection
print("\n--- Testing Self-Reflection Cycle ---")
suggestions_result = []
try:
    notification_manager = NotificationManager()
    print("NotificationManager for reflection test initialized.")

    print("Using global tool_system_instance for available tools.")
    available_tools_data = tool_system_instance.list_tools()
    if not available_tools_data:
        print("Warning: No tools available from tool_system_instance for reflection cycle.")

    print(f"Number of tools available for reflection: {len(available_tools_data)}")

    # Corrected keyword argument: available_tools_for_reflection -> available_tools
    suggestions_result = run_self_reflection_cycle(
        available_tools=available_tools_data,
        notification_manager=notification_manager
    )
    print(f"Reflection suggestions: {suggestions_result!r}")
except Exception as e_reflect:
    print(f"Error during self-reflection cycle: {type(e_reflect).__name__} - {e_reflect}")
    import traceback
    traceback.print_exc()

print("\n--- Test Script Finished ---")
