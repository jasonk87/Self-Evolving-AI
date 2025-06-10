import unittest
from unittest.mock import patch, MagicMock, call
import json

# Assuming the module structure allows this import path
from ai_assistant.core.autonomous_reflection import (
    _invoke_suggestion_scoring_llm,
    run_self_reflection_cycle,
    select_suggestion_for_autonomous_action,
    get_reflection_log_summary_for_analysis, # Added for potential use in run_self_reflection_cycle tests
    _invoke_pattern_identification_llm # Added for potential use in run_self_reflection_cycle tests
)
import datetime # Added for ReflectionLogEntry timestamp

# Assuming the module structure allows this import path
from ai_assistant.core.reflection import ReflectionLogEntry, global_reflection_log # Added
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model # Added for mocking

# If DEFAULT_OLLAMA_MODEL is a global constant in autonomous_reflection.py that needs to be defined for tests:
# from ai_assistant.core.autonomous_reflection import DEFAULT_OLLAMA_MODEL # Or define a mock one here

# Mock constants if they are not easily importable or for test stability
DEFAULT_OLLAMA_MODEL_FOR_TEST = "test_model"

# Merging new tests into a more general class or creating a new one.
# For simplicity, let's rename existing classes if their scope expands significantly
# or add a new class. Given the tasks, let's add to a new class for clarity.

class TestAutonomousReflectionEnhancements(unittest.TestCase):

    def setUp(self):
        # Common setup for new tests, if any
        self.sample_suggestion_for_modify = {
            "suggestion_id": "SUG_MODIFY_001",
            "suggestion_text": "Modify 'sample_tool_function' to improve efficiency.",
            "action_type": "MODIFY_TOOL_CODE",
            "action_details": {
                "module_path": "ai_assistant.dummy_modules.dummy_tool_module",
                "function_name": "sample_tool_function",
                "suggested_code_change": "def sample_tool_function(param1):\n    return param1 * 2",
                "original_code_snippet": "def sample_tool_function(param1):\n    return param1",
                "suggested_change_description": "Refactored for 2x performance."
            },
            "impact_score": 4, "risk_score": 1, "effort_score": 2, # Priority: 4-1-1 = 2
            "review_looks_good": True,
            "qualitative_review": "Looks good to go.",
            "reviewer_confidence": 0.9
        }

    @patch('ai_assistant.core.reflection.global_reflection_log.log_execution')
    @patch('ai_assistant.learning.evolution.apply_code_modification')
    def test_select_suggestion_logs_self_modification_details_on_success(self, mock_apply_code, mock_log_exec):
        mock_apply_return = {
            "overall_status": True, "overall_message": "All good, code modified and committed.",
            "edit_outcome": {"status": True, "message": "Edited successfully.", "backup_path": "path/to/dummy_tool_module.py.bak"},
            "test_outcome": {"passed": True, "stdout": "All tests passed.", "stderr": "", "notes": "Tests ran successfully."},
            "revert_outcome": None,
            "commit_outcome": {"status": True, "commit_message_generated": "AI Autocommit: Modified sample_tool_function...", "error_message": None}
        }
        mock_apply_code.return_value = mock_apply_return

        select_suggestion_for_autonomous_action([self.sample_suggestion_for_modify])
        
        mock_log_exec.assert_called_once()
        call_kwargs = mock_log_exec.call_args[1]

        self.assertTrue(call_kwargs['is_self_modification_attempt'])
        self.assertEqual(call_kwargs['source_suggestion_id'], self.sample_suggestion_for_modify['suggestion_id'])
        self.assertEqual(call_kwargs['modification_type'], "MODIFY_TOOL_CODE")
        self.assertEqual(call_kwargs['post_modification_test_passed'], True)
        self.assertEqual(call_kwargs['post_modification_test_details'], mock_apply_return['test_outcome'])
        self.assertEqual(call_kwargs['commit_info'], mock_apply_return['commit_outcome'])
        self.assertTrue(call_kwargs['overall_success']) # Based on mock_apply_return['overall_status']
        self.assertIn("Self-modification attempt for suggestion SUG_MODIFY_001", call_kwargs['goal_description'])
        self.assertEqual(call_kwargs['notes'], mock_apply_return['overall_message'])
        self.assertEqual(call_kwargs['modification_details']['module'], self.sample_suggestion_for_modify['action_details']['module_path'])

    @patch('ai_assistant.core.reflection.global_reflection_log.log_execution')
    @patch('ai_assistant.learning.evolution.apply_code_modification')
    def test_select_suggestion_logs_self_modification_details_on_test_failure(self, mock_apply_code, mock_log_exec):
        mock_apply_return_test_fail = {
            "overall_status": False, "overall_message": "Tests failed, reverted.",
            "edit_outcome": {"status": True, "message": "Edited successfully.", "backup_path": "path/to/dummy_tool_module.py.bak"},
            "test_outcome": {"passed": False, "stdout": "", "stderr": "AssertionError: 1 != 2", "notes": "Test failed."},
            "revert_outcome": {"status": True, "message": "Reverted successfully."},
            "commit_outcome": None 
        }
        mock_apply_code.return_value = mock_apply_return_test_fail

        select_suggestion_for_autonomous_action([self.sample_suggestion_for_modify])
        
        mock_log_exec.assert_called_once()
        call_kwargs = mock_log_exec.call_args[1]

        self.assertTrue(call_kwargs['is_self_modification_attempt'])
        self.assertEqual(call_kwargs['source_suggestion_id'], self.sample_suggestion_for_modify['suggestion_id'])
        self.assertEqual(call_kwargs['post_modification_test_passed'], False)
        self.assertEqual(call_kwargs['post_modification_test_details'], mock_apply_return_test_fail['test_outcome'])
        self.assertIsNone(call_kwargs['commit_info']) # No commit if tests fail
        self.assertFalse(call_kwargs['overall_success'])
        self.assertEqual(call_kwargs['notes'], mock_apply_return_test_fail['overall_message'])

    @patch('ai_assistant.core.reflection.global_reflection_log.get_entries')
    def test_get_summary_includes_self_modification(self, mock_get_entries):
        now = datetime.datetime.now(datetime.timezone.utc)
        mock_self_mod_entry = ReflectionLogEntry(
            goal_description="Self-mod attempt: SUG00X",
            plan=[], execution_results=[], status="SUCCESS", timestamp=now,
            is_self_modification_attempt=True,
            source_suggestion_id="SUG00X",
            modification_type="MODIFY_TOOL_CODE",
            modification_details={"module": "a.b.c", "function": "test_func"},
            post_modification_test_passed=True,
            post_modification_test_details={"passed": True, "notes": "All good in the hood.", "stdout": "OK"},
            commit_info={"status": True, "message": "Committed: AI fix for SUG00X"}
        )
        mock_normal_entry = ReflectionLogEntry(
            goal_description="Normal goal", plan=[], execution_results=[], status="FAILURE", 
            error_type="TypeError", error_message="Something bad", timestamp=now
        )
        mock_get_entries.return_value = [mock_normal_entry, mock_self_mod_entry]

        summary = get_reflection_log_summary_for_analysis(max_entries=2, min_entries_for_analysis=1)

        self.assertIn("--- SELF-MODIFICATION ATTEMPT ---", summary)
        self.assertIn("Source Suggestion ID: SUG00X", summary)
        self.assertIn("Test Outcome: PASSED", summary) # Based on ReflectionLogEntry formatting
        self.assertIn("Test Notes: All good in the hood.", summary)
        self.assertIn("Commit Status: Committed (Msg: Committed: AI fix for SUG00X)", summary)
        # Ensure normal entry details are also present
        self.assertIn("Goal: Normal goal", summary)
        self.assertIn("Error: TypeError - Something bad", summary)

    @patch('ai_assistant.core.autonomous_reflection.invoke_ollama_model')
    def test_suggestion_generation_prompt_for_modify_tool_code(self, mock_invoke_ollama):
        # This test focuses on the prompt content for _invoke_suggestion_generation_llm
        mock_invoke_ollama.return_value = '{"improvement_suggestions": []}' # Minimal valid response
        
        sample_patterns = [{"pattern_type": "FREQUENTLY_FAILING_TOOL", "tool_name": "test_tool"}]
        sample_tools = {"test_tool": "A tool that often fails."}
        
        # We call the internal _invoke_suggestion_generation_llm directly for this test
        # In a real scenario, run_self_reflection_cycle would call this after other steps.
        from ai_assistant.core.autonomous_reflection import _invoke_suggestion_generation_llm
        _invoke_suggestion_generation_llm(
            identified_patterns_json_list_str=json.dumps(sample_patterns),
            available_tools_json_str=json.dumps(sample_tools),
            llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST
        )

        mock_invoke_ollama.assert_called_once()
        prompt_arg = mock_invoke_ollama.call_args[0][0]

        # Check for key phrases from the updated MODIFY_TOOL_CODE example in the prompt
        self.assertIn('"module_path": "path.to.your.module"', prompt_arg)
        self.assertIn('"function_name": "function_to_modify"', prompt_arg)
        self.assertIn('"suggested_code_change": "def function_to_modify(param1, param2):', prompt_arg)
        self.assertIn("New, complete function code here", prompt_arg)
        self.assertIn('"original_code_snippet": "(Optional) Few lines of the original code for context', prompt_arg)
        self.assertIn('"suggested_change_description": "Detailed textual description of what was changed and why, suitable for a commit message body."', prompt_arg)
        self.assertIn("For MODIFY_TOOL_CODE, 'module_path', 'function_name', and 'suggested_code_change' (the new complete function source code) are mandatory.", prompt_arg)


class TestInvokeSuggestionScoringLLM(unittest.TestCase):

    @patch('ai_assistant.core.autonomous_reflection.invoke_ollama_model')
    def test_successful_scoring(self, mock_invoke_ollama):
        mock_response = '{ "impact_score": 4, "risk_score": 2, "effort_score": 3 }'
        mock_invoke_ollama.return_value = mock_response
        
        sample_suggestion = {
            "suggestion_id": "SUG_001",
            "suggestion_text": "Test suggestion",
            "action_type": "MODIFY_TOOL_CODE",
            "action_details": {"tool_name": "test_tool"}
        }
        
        expected_scores = {"impact_score": 4, "risk_score": 2, "effort_score": 3}
        
        result = _invoke_suggestion_scoring_llm(sample_suggestion, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        
        self.assertEqual(result, expected_scores)
        mock_invoke_ollama.assert_called_once()
        # You could add more assertions here to check the prompt contents if needed, by inspecting mock_invoke_ollama.call_args

    @patch('ai_assistant.core.autonomous_reflection.invoke_ollama_model')
    @patch('builtins.print')
    def test_llm_returns_invalid_json(self, mock_print, mock_invoke_ollama):
        mock_invoke_ollama.return_value = "This is not JSON"
        
        sample_suggestion = {"suggestion_text": "Test", "action_type": "ANY"}
        result = _invoke_suggestion_scoring_llm(sample_suggestion, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        
        self.assertIsNone(result)
        mock_print.assert_any_call("Error decoding JSON from suggestion scoring LLM: Expecting value: line 1 column 1 (char 0). Response: This is not JSON")

    @patch('ai_assistant.core.autonomous_reflection.invoke_ollama_model')
    @patch('builtins.print')
    def test_llm_returns_json_with_missing_keys(self, mock_print, mock_invoke_ollama):
        mock_invoke_ollama.return_value = '{ "impact_score": 4, "risk_score": 2 }' # Missing "effort_score"
        
        sample_suggestion = {"suggestion_text": "Test", "action_type": "ANY"}
        result = _invoke_suggestion_scoring_llm(sample_suggestion, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        
        self.assertIsNone(result)
        mock_print.assert_any_call("Warning: LLM response for suggestion scoring missing key 'effort_score'. Response: { \"impact_score\": 4, \"risk_score\": 2 }")

    @patch('ai_assistant.core.autonomous_reflection.invoke_ollama_model')
    @patch('builtins.print')
    def test_llm_returns_json_with_non_integer_scores(self, mock_print, mock_invoke_ollama):
        mock_invoke_ollama.return_value = '{ "impact_score": "high", "risk_score": 2, "effort_score": 3 }'
        
        sample_suggestion = {"suggestion_text": "Test", "action_type": "ANY"}
        result = _invoke_suggestion_scoring_llm(sample_suggestion, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        
        self.assertIsNone(result)
        mock_print.assert_any_call("Warning: LLM response for suggestion scoring key 'impact_score' is not an integer. Value: high. Response: { \"impact_score\": \"high\", \"risk_score\": 2, \"effort_score\": 3 }")

    @patch('ai_assistant.core.autonomous_reflection.invoke_ollama_model')
    def test_handling_action_details_present_and_absent(self, mock_invoke_ollama):
        # Test with action_details
        mock_invoke_ollama.return_value = '{ "impact_score": 1, "risk_score": 1, "effort_score": 1 }'
        suggestion_with_details = {
            "suggestion_text": "Test with details", 
            "action_type": "MODIFY_TOOL_CODE",
            "action_details": {"tool_name": "some_tool", "change": "critical"}
        }
        result_with_details = _invoke_suggestion_scoring_llm(suggestion_with_details, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        self.assertIsNotNone(result_with_details)
        
        # Check if prompt formatting for action_details was as expected (stringified JSON)
        args_with_details, _ = mock_invoke_ollama.call_args
        prompt_with_details = args_with_details[0]
        self.assertIn('"action_details": {"tool_name": "some_tool", "change": "critical"}', prompt_with_details.replace("\\", "")) # Handle potential escapes

        mock_invoke_ollama.reset_mock() # Reset for the next call

        # Test without action_details (should default to "{}")
        mock_invoke_ollama.return_value = '{ "impact_score": 2, "risk_score": 2, "effort_score": 2 }'
        suggestion_without_details = {
            "suggestion_text": "Test without details", 
            "action_type": "MANUAL_REVIEW_NEEDED"
            # "action_details": None is implied
        }
        result_without_details = _invoke_suggestion_scoring_llm(suggestion_without_details, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        self.assertIsNotNone(result_without_details)
        
        args_without_details, _ = mock_invoke_ollama.call_args
        prompt_without_details = args_without_details[0]
        self.assertIn('"action_details_json_str": "{}"', prompt_without_details.replace(" ", "").replace("\\n", "")) # Check for empty JSON object in prompt


class TestRunSelfReflectionCycleScoring(unittest.TestCase):

    @patch('ai_assistant.core.autonomous_reflection.get_reflection_log_summary_for_analysis')
    @patch('ai_assistant.core.autonomous_reflection._invoke_pattern_identification_llm')
    @patch('ai_assistant.core.autonomous_reflection._invoke_suggestion_generation_llm')
    @patch('ai_assistant.core.autonomous_reflection._invoke_suggestion_scoring_llm')
    def test_successful_scoring_for_all_suggestions(
        self, 
        mock_score_suggestion, 
        mock_generate_suggestions, 
        mock_identify_patterns, 
        mock_get_log_summary
    ):
        # Setup: Mock previous steps in the cycle to return valid data
        mock_get_log_summary.return_value = "Some log summary"
        mock_identify_patterns.return_value = {"identified_patterns": [{"pattern_type": "Test Pattern"}]}
        
        sample_suggestions_generated = [
            {"suggestion_id": "SUG_001", "suggestion_text": "Suggestion 1", "action_type": "TYPE_A"},
            {"suggestion_id": "SUG_002", "suggestion_text": "Suggestion 2", "action_type": "TYPE_B"},
        ]
        mock_generate_suggestions.return_value = {"improvement_suggestions": sample_suggestions_generated}
        
        # Mock scoring to return different valid scores
        mock_score_suggestion.side_effect = [
            {"impact_score": 5, "risk_score": 1, "effort_score": 2},
            {"impact_score": 4, "risk_score": 2, "effort_score": 3},
        ]
        
        result = run_self_reflection_cycle(available_tools={"tool1": "desc"}, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        
        self.assertEqual(result[0]["suggestion_id"], "SUG_001")
        self.assertEqual(result[0]["impact_score"], 5)
        self.assertEqual(result[0]["risk_score"], 1)
        self.assertEqual(result[0]["effort_score"], 2)
        
        self.assertEqual(result[1]["suggestion_id"], "SUG_002")
        self.assertEqual(result[1]["impact_score"], 4)
        self.assertEqual(result[1]["risk_score"], 2)
        self.assertEqual(result[1]["effort_score"], 3)
        
        self.assertEqual(mock_score_suggestion.call_count, 2)
        mock_score_suggestion.assert_any_call(sample_suggestions_generated[0], llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        mock_score_suggestion.assert_any_call(sample_suggestions_generated[1], llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)

    @patch('ai_assistant.core.autonomous_reflection.get_reflection_log_summary_for_analysis')
    @patch('ai_assistant.core.autonomous_reflection._invoke_pattern_identification_llm')
    @patch('ai_assistant.core.autonomous_reflection._invoke_suggestion_generation_llm')
    @patch('ai_assistant.core.autonomous_reflection._invoke_suggestion_scoring_llm')
    @patch('builtins.print') # To suppress or check print warnings
    def test_scoring_fails_for_one_suggestion(
        self, 
        mock_print,
        mock_score_suggestion, 
        mock_generate_suggestions, 
        mock_identify_patterns, 
        mock_get_log_summary
    ):
        mock_get_log_summary.return_value = "Some log summary"
        mock_identify_patterns.return_value = {"identified_patterns": [{"pattern_type": "Test Pattern"}]}
        
        sample_suggestions_generated = [
            {"suggestion_id": "SUG_001", "suggestion_text": "Suggestion 1", "action_type": "TYPE_A"},
            {"suggestion_id": "SUG_002", "suggestion_text": "Suggestion 2", "action_type": "TYPE_B"},
        ]
        mock_generate_suggestions.return_value = {"improvement_suggestions": sample_suggestions_generated}
        
        # Mock scoring: success for first, None (failure) for second
        mock_score_suggestion.side_effect = [
            {"impact_score": 5, "risk_score": 1, "effort_score": 2},
            None, 
        ]
        
        result = run_self_reflection_cycle(available_tools={"tool1": "desc"}, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        
        self.assertEqual(result[0]["suggestion_id"], "SUG_001")
        self.assertEqual(result[0]["impact_score"], 5)
        self.assertEqual(result[0]["risk_score"], 1)
        self.assertEqual(result[0]["effort_score"], 2)
        
        self.assertEqual(result[1]["suggestion_id"], "SUG_002")
        self.assertEqual(result[1]["impact_score"], -1) # Default error score
        self.assertEqual(result[1]["risk_score"], -1)  # Default error score
        self.assertEqual(result[1]["effort_score"], -1) # Default error score
        
        mock_print.assert_any_call("Warning: Failed to score suggestion ID: SUG_002. Assigning default error scores (-1).")
        self.assertEqual(mock_score_suggestion.call_count, 2)

    @patch('ai_assistant.core.autonomous_reflection.get_reflection_log_summary_for_analysis')
    @patch('ai_assistant.core.autonomous_reflection._invoke_pattern_identification_llm')
    @patch('ai_assistant.core.autonomous_reflection._invoke_suggestion_generation_llm')
    def test_no_suggestions_generated(
        self, 
        mock_generate_suggestions, 
        mock_identify_patterns, 
        mock_get_log_summary
    ):
        mock_get_log_summary.return_value = "Some log summary"
        mock_identify_patterns.return_value = {"identified_patterns": [{"pattern_type": "Test Pattern"}]}
        mock_generate_suggestions.return_value = {"improvement_suggestions": []} # No suggestions
        
        result = run_self_reflection_cycle(available_tools={"tool1": "desc"}, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        self.assertEqual(result, []) # Should return an empty list

    @patch('ai_assistant.core.autonomous_reflection.get_reflection_log_summary_for_analysis')
    @patch('ai_assistant.core.autonomous_reflection._invoke_pattern_identification_llm')
    @patch('ai_assistant.core.autonomous_reflection._invoke_suggestion_generation_llm')
    def test_suggestion_generation_returns_none(
        self, 
        mock_generate_suggestions, 
        mock_identify_patterns, 
        mock_get_log_summary
    ):
        mock_get_log_summary.return_value = "Some log summary"
        mock_identify_patterns.return_value = {"identified_patterns": [{"pattern_type": "Test Pattern"}]}
        mock_generate_suggestions.return_value = None # LLM call failed for suggestions
        
        result = run_self_reflection_cycle(available_tools={"tool1": "desc"}, llm_model_name=DEFAULT_OLLAMA_MODEL_FOR_TEST)
        self.assertIsNone(result)


class TestSelectSuggestionForAutonomousAction(unittest.TestCase):

    def _create_sample_suggestion(self, id, action_type, action_details, impact, risk, effort):
        return {
            "suggestion_id": id,
            "suggestion_text": f"Text for {id}",
            "action_type": action_type,
            "action_details": action_details,
            "impact_score": impact,
            "risk_score": risk,
            "effort_score": effort,
            # "_priority_score" will be calculated by the function if scores are valid
        }

    def test_basic_selection_with_scoring(self):
        suggestions = [
            self._create_sample_suggestion("S1", "UPDATE_TOOL_DESCRIPTION", {"tool_name": "t1", "new_description": "d1"}, impact=3, risk=1, effort=1), # Priority: 3-1-0.5 = 1.5
            self._create_sample_suggestion("S2", "CREATE_NEW_TOOL", {"tool_description_prompt": "p2"}, impact=5, risk=1, effort=2), # Priority: 5-1-1 = 3
            self._create_sample_suggestion("S3", "UPDATE_TOOL_DESCRIPTION", {"tool_name": "t3", "new_description": "d3"}, impact=4, risk=2, effort=2), # Priority: 4-2-1 = 1
        ]
        selected = select_suggestion_for_autonomous_action(suggestions)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["suggestion_id"], "S2")

    def test_higher_scored_suggestion_is_invalid_action_details(self):
        suggestions = [
            self._create_sample_suggestion("S1_invalid_details", "CREATE_NEW_TOOL", {"tool_description_prompt": ""}, impact=5, risk=1, effort=1), # High priority (3.5), but invalid (empty prompt)
            self._create_sample_suggestion("S2_valid", "UPDATE_TOOL_DESCRIPTION", {"tool_name": "t2", "new_description": "d2"}, impact=3, risk=1, effort=1), # Lower priority (1.5) but valid
        ]
        selected = select_suggestion_for_autonomous_action(suggestions)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["suggestion_id"], "S2_valid")

    def test_filtering_by_action_type(self):
        suggestions = [
            self._create_sample_suggestion("S1_unsupported_type", "MODIFY_TOOL_CODE", {"tool_name": "t1", "suggested_change_description": "c1"}, impact=5, risk=1, effort=1), # High priority (3.5), but unsupported type
            self._create_sample_suggestion("S2_supported_type", "CREATE_NEW_TOOL", {"tool_description_prompt": "p2"}, impact=3, risk=1, effort=1), # Lower priority (1.5) but supported type
        ]
        # Default supported: ["UPDATE_TOOL_DESCRIPTION", "CREATE_NEW_TOOL"]
        selected = select_suggestion_for_autonomous_action(suggestions)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["suggestion_id"], "S2_supported_type")
        
        # Test with explicit supported types
        selected_custom = select_suggestion_for_autonomous_action(suggestions, supported_action_types=["MODIFY_TOOL_CODE"])
        self.assertIsNotNone(selected_custom)
        self.assertEqual(selected_custom["suggestion_id"], "S1_unsupported_type")


    def test_filtering_out_failed_scores(self):
        suggestions = [
            self._create_sample_suggestion("S1_failed_score", "CREATE_NEW_TOOL", {"tool_description_prompt": "p1"}, impact=5, risk=-1, effort=1), # High "raw" impact, but risk is -1
            self._create_sample_suggestion("S2_valid_scores", "UPDATE_TOOL_DESCRIPTION", {"tool_name": "t2", "new_description": "d2"}, impact=3, risk=1, effort=1), # Valid scores, priority 1.5
        ]
        selected = select_suggestion_for_autonomous_action(suggestions)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["suggestion_id"], "S2_valid_scores")

    def test_empty_suggestion_list(self):
        selected = select_suggestion_for_autonomous_action([])
        self.assertIsNone(selected)

    def test_no_suitable_suggestion_found_all_invalid(self):
        suggestions = [
            self._create_sample_suggestion("S1_unsupported", "MODIFY_TOOL_CODE", {"tool_name": "t1", "suggested_change_description": "c1"}, impact=5, risk=1, effort=1),
            self._create_sample_suggestion("S2_failed_score", "CREATE_NEW_TOOL", {"tool_description_prompt": "p2"}, impact=5, risk=1, effort=-1),
            self._create_sample_suggestion("S3_invalid_details", "UPDATE_TOOL_DESCRIPTION", {"tool_name": "t3"}, impact=4, risk=1, effort=1), # Missing new_description
        ]
        selected = select_suggestion_for_autonomous_action(suggestions)
        self.assertIsNone(selected)
        
    def test_priority_calculation_and_sorting(self):
        # Effort has 0.5 multiplier, lower is better for risk and effort
        # Priority = Impact - Risk - (Effort * 0.5)
        suggestions = [
            self._create_sample_suggestion("S_LowImpact_LowRisk_LowEffort", "CREATE_NEW_TOOL", {"tool_description_prompt": "p1"}, impact=2, risk=1, effort=1), # P = 2 - 1 - 0.5 = 0.5
            self._create_sample_suggestion("S_HighImpact_HighRisk_HighEffort", "CREATE_NEW_TOOL", {"tool_description_prompt": "p2"}, impact=5, risk=3, effort=4),# P = 5 - 3 - 2 = 0
            self._create_sample_suggestion("S_MidImpact_LowRisk_MidEffort", "CREATE_NEW_TOOL", {"tool_description_prompt": "p3"}, impact=4, risk=1, effort=2), # P = 4 - 1 - 1 = 2
            self._create_sample_suggestion("S_HighImpact_MidRisk_LowEffort", "CREATE_NEW_TOOL", {"tool_description_prompt": "p4"}, impact=5, risk=2, effort=1), # P = 5 - 2 - 0.5 = 2.5 (Highest)
        ]
        # Expected order: S_HighImpact_MidRisk_LowEffort (2.5), S_MidImpact_LowRisk_MidEffort (2), S_LowImpact_LowRisk_LowEffort (0.5), S_HighImpact_HighRisk_HighEffort (0)
        selected = select_suggestion_for_autonomous_action(suggestions)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["suggestion_id"], "S_HighImpact_MidRisk_LowEffort")

    def test_selection_amongst_equally_prioritized_valid_suggestions(self):
        # If multiple suggestions have the same highest priority score and are valid,
        # the current implementation will pick the one that appears first in the *sorted* list.
        # The sort is stable, so if they had same priority, their original relative order (after filtering) would be maintained.
        # This test ensures one is picked.
        suggestions = [
             self._create_sample_suggestion("S1_equal_priority", "CREATE_NEW_TOOL", {"tool_description_prompt": "prompt1"}, impact=4, risk=1, effort=2), # P = 4 - 1 - 1 = 2
             self._create_sample_suggestion("S2_equal_priority", "UPDATE_TOOL_DESCRIPTION", {"tool_name":"t1", "new_description": "desc1"}, impact=4, risk=1, effort=2), # P = 4 - 1 - 1 = 2
        ]
        selected = select_suggestion_for_autonomous_action(suggestions)
        self.assertIsNotNone(selected)
        # The exact one depends on Python's list sort stability if scores are identical.
        # Both are valid, so one of them should be chosen.
        self.assertIn(selected["suggestion_id"], ["S1_equal_priority", "S2_equal_priority"])


if __name__ == '__main__':
    unittest.main()
