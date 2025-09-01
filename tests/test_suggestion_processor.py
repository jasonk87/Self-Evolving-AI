import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import sys
import os
import json
import uuid # For mocking

# Ensure ai_assistant module can be imported
try:
    from ai_assistant.core.suggestion_processor import SuggestionProcessor, LLM_TARGET_IDENTIFICATION_PROMPT_TEMPLATE
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.code_services.service import CodeService
    # from ai_assistant.llm_interface.ollama_client import OllamaProvider # If needed for type hinting mock
except ImportError as e: # pragma: no cover
    print(f"Import error in test_suggestion_processor: {e}")
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.core.suggestion_processor import SuggestionProcessor, LLM_TARGET_IDENTIFICATION_PROMPT_TEMPLATE
    from ai_assistant.execution.action_executor import ActionExecutor
    from ai_assistant.code_services.service import CodeService
    # from ai_assistant.llm_interface.ollama_client import OllamaProvider


class TestSuggestionProcessor(unittest.TestCase):
    def setUp(self):
        self.mock_action_executor = AsyncMock(spec=ActionExecutor)

        self.mock_llm_provider = AsyncMock()
        # If CodeService or its llm_provider.invoke_ollama_model_async uses get_model_for_task,
        # it should be patched in the module where it's called, e.g.,
        # @patch('ai_assistant.llm_interface.ollama_client.get_model_for_task') for invoke_ollama_model_async
        # For this test, we directly mock invoke_ollama_model_async on the provider instance.
        # self.mock_llm_provider.get_model_for_task.return_value = "mock_model_for_processor" # If needed

        self.mock_code_service = MagicMock(spec=CodeService)
        self.mock_code_service.llm_provider = self.mock_llm_provider

        self.processor = SuggestionProcessor(
            action_executor=self.mock_action_executor,
            code_service=self.mock_code_service
        )

        self.tool_system_patcher = patch('ai_assistant.core.suggestion_processor.tool_system_instance')
        self.mock_tool_system_instance = self.tool_system_patcher.start()

        self.list_suggestions_patcher = patch('ai_assistant.core.suggestion_processor.list_suggestions')
        self.mock_list_suggestions = self.list_suggestions_patcher.start()

        # Patch get_model_for_task used directly by _identify_target_tool_from_suggestion
        self.get_model_patcher = patch('ai_assistant.core.suggestion_processor.get_model_for_task')
        self.mock_get_model_for_task = self.get_model_patcher.start()
        self.mock_get_model_for_task.return_value = "mock_planning_model"


    def tearDown(self):
        self.tool_system_patcher.stop()
        self.list_suggestions_patcher.stop()
        self.get_model_patcher.stop()

    # --- Tests for _identify_target_tool_from_suggestion ---
    def test_identify_target_tool_success_high_confidence(self):
        async def run_test():
            suggestion_desc = "The 'calculate_area' tool should handle negative inputs."
            mock_tools_output = {
                "calculate_area_tool_key": [{
                    "module_path": "ai_assistant.custom_tools.calculator",
                    "function_name": "calculate_area",
                    "description": "Calculates area."
                }]
            }
            self.mock_tool_system_instance.list_tools_with_sources.return_value = mock_tools_output
            llm_response = {
                "module_path": "ai_assistant.custom_tools.calculator",
                "function_name": "calculate_area",
                "confidence": "high",
                "reasoning": "Matches tool name and context."
            }
            self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_response)
            target_info = await self.processor._identify_target_tool_from_suggestion(suggestion_desc)
            self.assertIsNotNone(target_info)
            self.assertEqual(target_info["module_path"], llm_response["module_path"])
            self.assertEqual(target_info["function_name"], llm_response["function_name"])
            self.mock_llm_provider.invoke_ollama_model_async.assert_called_once()
        asyncio.run(run_test())

    def test_identify_target_tool_llm_low_confidence(self):
        async def run_test():
            suggestion_desc = "Maybe improve the way it talks about files."
            self.mock_tool_system_instance.list_tools_with_sources.return_value = {
                "some_file_tool": [{"module_path": "some.module", "function_name": "file_tool", "description": "desc"}]
            }
            llm_response = {"module_path": None, "function_name": None, "confidence": "low", "reasoning": "Too vague."}
            self.mock_llm_provider.invoke_ollama_model_async.return_value = json.dumps(llm_response)
            target_info = await self.processor._identify_target_tool_from_suggestion(suggestion_desc)
            self.assertIsNone(target_info)
        asyncio.run(run_test())

    def test_identify_target_tool_llm_error(self):
        async def run_test():
            suggestion_desc = "Improve tool X."
            self.mock_tool_system_instance.list_tools_with_sources.return_value = {
                "tool_x": [{"module_path": "module.x", "function_name": "tool_x_func", "description": "desc"}]
            }
            self.mock_llm_provider.invoke_ollama_model_async.side_effect = Exception("LLM network error")
            target_info = await self.processor._identify_target_tool_from_suggestion(suggestion_desc)
            self.assertIsNone(target_info)
        asyncio.run(run_test())

    def test_identify_target_tool_no_tools_available(self):
        async def run_test():
            suggestion_desc = "Improve tool X."
            self.mock_tool_system_instance.list_tools_with_sources.return_value = {}
            target_info = await self.processor._identify_target_tool_from_suggestion(suggestion_desc)
            self.assertIsNone(target_info)
            self.mock_llm_provider.invoke_ollama_model_async.assert_not_called()
        asyncio.run(run_test())


    # --- Tests for process_pending_suggestions ---
    @patch('ai_assistant.core.suggestion_processor.uuid.uuid4')
    def test_process_pending_suggestions_identifies_and_dispatches(self, mock_uuid_call):
        async def run_test(mock_uuid_call_inner):
            mock_uuid_instance = MagicMock()
            mock_uuid_instance.__str__.return_value = "abcdef1234567890"
            mock_uuid_call_inner.return_value = mock_uuid_instance
            pending_sugg1 = {
                "suggestion_id": "sugg_id_1", "type": "tool_improvement",
                "description": "Fix 'calc_sum' tool for big numbers.", "status": "pending",
                "source_reflection_id": "reflect_abc"
            }
            self.mock_list_suggestions.return_value = [pending_sugg1]
            identified_target = {
                "module_path": "ai_assistant.custom_tools.math_tools",
                "function_name": "calc_sum",
                "reasoning": "Clear match."
            }
            with patch.object(self.processor, '_identify_target_tool_from_suggestion', new_callable=AsyncMock, return_value=identified_target) as mock_identify:
                self.mock_action_executor.execute_action.return_value = True
                await self.processor.process_pending_suggestions(limit=1)
                mock_identify.assert_called_once_with("Fix 'calc_sum' tool for big numbers.")
                self.mock_action_executor.execute_action.assert_called_once()
                call_args = self.mock_action_executor.execute_action.call_args[0][0]
                self.assertEqual(call_args["action_type"], "PROPOSE_TOOL_MODIFICATION")
                self.assertEqual(call_args["source_insight_id"], "sugg_id_1")
                details = call_args["details"]
                self.assertEqual(details["module_path"], "ai_assistant.custom_tools.math_tools")
                self.assertEqual(details["function_name"], "calc_sum")
                self.assertEqual(details["tool_name"], "calc_sum")
                self.assertIsNone(details["suggested_code_change"])
                self.assertEqual(details["suggested_change_description"], "Fix 'calc_sum' tool for big numbers.")
                self.assertEqual(details["original_reflection_entry_id"], "reflect_abc")
        asyncio.run(run_test(mock_uuid_call))

    def test_process_pending_suggestions_target_not_identified(self):
        async def run_test():
            pending_sugg1 = {"suggestion_id": "sugg_id_2", "type": "tool_improvement", "description": "Vague improvement idea.", "status": "pending"}
            self.mock_list_suggestions.return_value = [pending_sugg1]
            with patch.object(self.processor, '_identify_target_tool_from_suggestion', new_callable=AsyncMock, return_value=None) as mock_identify:
                await self.processor.process_pending_suggestions(limit=1)
                mock_identify.assert_called_once_with("Vague improvement idea.")
                self.mock_action_executor.execute_action.assert_not_called()
        asyncio.run(run_test())

    def test_process_pending_suggestions_no_pending_tool_improvements(self):
        async def run_test():
            self.mock_list_suggestions.return_value = [
                {"suggestion_id": "sugg_id_3", "type": "ui_improvement", "description": "Better colors.", "status": "pending"},
                {"suggestion_id": "sugg_id_4", "type": "tool_improvement", "description": "Fix tool X.", "status": "approved"}
            ]
            await self.processor.process_pending_suggestions(limit=5)
            self.mock_action_executor.execute_action.assert_not_called()
        asyncio.run(run_test())


if __name__ == '__main__': # pragma: no cover
    unittest.main()
