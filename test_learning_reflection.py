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
    # SuggestionManager class is not used directly by run_self_reflection_cycle based on its signature
    # from ai_assistant.core.suggestion_manager import SuggestionManager
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

    # SuggestionManager instance is not passed to run_self_reflection_cycle
    # suggestion_manager_instance = SuggestionManager()
    # print("SuggestionManager for reflection test initialized.")

    print("Using global tool_system_instance for available tools.")
    # ToolSystem is initialized when its module is first imported (due to global instance creation)
    # Ensure it's ready by listing tools, which also populates its registry from discovery if not already done.
    available_tools = tool_system_instance.list_tools()
    if not available_tools:
        print("Warning: No tools available from tool_system_instance for reflection cycle.")

    print(f"Number of tools available for reflection: {len(available_tools)}")

    # run_self_reflection_cycle is synchronous but calls async LLM functions internally using asyncio.run()
    # Corrected keyword argument for available_tools
    suggestions_result = run_self_reflection_cycle(
        available_tools=available_tools,
        notification_manager=notification_manager
        # suggestion_manager is not a parameter for run_self_reflection_cycle
    )
    print(f"Reflection suggestions: {suggestions_result!r}")
except Exception as e_reflect:
    print(f"Error during self-reflection cycle: {type(e_reflect).__name__} - {e_reflect}")
    import traceback
    traceback.print_exc()

print("\n--- Test Script Finished ---")
