import sys
import os

# Add project root to sys.path
# The script is expected to be in /app/, so Self-Evolving-Agent... is one level down.
project_root_parts = ["Self-Evolving-Agent-feat-learning-module", "Self-Evolving-Agent-feat-chat-history-context"]
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), *project_root_parts))

if project_root not in sys.path:
    sys.path.insert(0, project_root)

print(f"Python version: {sys.version}")
print(f"Project root: {project_root}")
print(f"sys.path: {sys.path}")

try:
    from ai_assistant.utils.display_utils import format_header, format_message, CLIColors
    # We are not using prompt_toolkit.print_formatted_text here,
    # but directly printing the strings which will contain ANSI codes.
    print("Display utils imported successfully.")
except Exception as e:
    print(f"Error importing display utils: {type(e).__name__} - {e}")
    sys.exit(1)

test_suggestions = [
    {
        'suggestion_id': 'SUG_001',
        '_action_result': {
            'overall_status': True,
            'overall_message': 'Code modified and tests passed successfully!'
        }
    },
    {
        'suggestion_id': 'SUG_002',
        '_action_result': {
            'overall_status': False,
            'overall_message': 'Code modification failed: Tests did not pass after change.'
        }
    },
    {
        'suggestion_id': 'SUG_003',
        '_action_result': {
            'status': 'PENDING_EXECUTION',
            'message': 'Action selected for UPDATE_TOOL_DESCRIPTION, execution handled by caller.'
        }
    },
    {
        'suggestion_id': 'SUG_004',
        '_action_result': {
            'status': 'SUCCESS_WITH_NOTE',
            'message': 'Tool description updated with a note.'
        }
    },
    {
        'suggestion_id': 'SUG_005',
        '_action_result': {
            'overall_status': True # Missing message keys
        }
    },
    {
        'suggestion_id': 'SUG_006',
        '_action_result': "Simple string status, not a dict."
    },
    {
        'suggestion_id': 'SUG_007'
        # Missing _action_result key
    },
    { # Test for "FAIL" string in status
        'suggestion_id': 'SUG_008',
        '_action_result': {
            'status': 'ACTION_FAILED_BADLY',
            'message': 'Something went very wrong during the action.'
        }
    }
]

print("\n--- Testing /review_insights Display Logic ---")

for i, selected_suggestion in enumerate(test_suggestions):
    print(f"\n--- Test Case {i+1}: Suggestion ID {selected_suggestion.get('suggestion_id')} ---")

    # This is the logic copied and adapted from cli.py
    action_result = selected_suggestion.get('_action_result')
    if action_result and isinstance(action_result, dict):
        result_message = action_result.get('overall_message',
                                           action_result.get('message', 'No detailed result message available.'))

        raw_status = action_result.get('overall_status', action_result.get('status', False))

        color = CLIColors.SYSTEM_MESSAGE
        if isinstance(raw_status, bool):
            if raw_status:
                color = CLIColors.SUCCESS
            else:
                color = CLIColors.ERROR_MESSAGE
        elif isinstance(raw_status, str):
            if "SUCCESS" in raw_status.upper():
                color = CLIColors.SUCCESS
            elif "PENDING" in raw_status.upper():
                color = CLIColors.SYSTEM_MESSAGE
            elif "FAIL" in raw_status.upper():
                color = CLIColors.ERROR_MESSAGE

        # In cli.py, these are passed to print_formatted_text(ANSI(...))
        # Here, we print the direct string output which includes ANSI codes.
        # The format_header and format_message functions return ANSI objects from prompt_toolkit,
        # which when printed/str() include the ANSI codes.
        print(str(format_header("Autonomous Action Result")))
        print(str(format_message("RESULT", result_message, color)))
    elif action_result:
        print(str(format_header("Autonomous Action Result")))
        print(str(format_message("RESULT", str(action_result), CLIColors.SYSTEM_MESSAGE)))
    else:
        print("No _action_result found in suggestion or it's None.")

print("\n--- Test Script Finished ---")
