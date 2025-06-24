import unittest
from unittest import mock
import asyncio
import os
import sys
import uuid
import datetime
from dataclasses import field

try:
    from ai_assistant.code_synthesis import CodeSynthesisService, CodeTaskRequest, CodeTaskType, CodeTaskStatus, CodeTaskResult
except ImportError: # pragma: no cover
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.code_synthesis import CodeSynthesisService, CodeTaskRequest, CodeTaskType, CodeTaskStatus, CodeTaskResult

class TestCodeSynthesisService(unittest.IsolatedAsyncioTestCase): # Use IsolatedAsyncioTestCase for async tests

    def setUp(self):
        self.service = CodeSynthesisService()

    @mock.patch('ai_assistant.code_synthesis.service.self_modification.get_function_source_code')
    @mock.patch('ai_assistant.code_synthesis.service.invoke_ollama_model_async', new_callable=mock.AsyncMock)
    async def test_handle_existing_tool_self_fix_llm_success(self, mock_invoke_llm, mock_get_source):
        mock_get_source.return_value = "def old_func(a): return a"
        mock_invoke_llm.return_value = "def old_func(a): return a + 1 # Fixed by LLM"

        request_data = {
            "module_path": "dummy.module",
            "function_name": "old_func",
            "problem_description": "Needs a fix"
        }
        request = CodeTaskRequest(
            task_type=CodeTaskType.EXISTING_TOOL_SELF_FIX_LLM,
            context_data=request_data
        )

        result = await self.service._handle_existing_tool_self_fix_llm(request)

        self.assertEqual(result.status, CodeTaskStatus.SUCCESS)
        self.assertIsNotNone(result.generated_code)
        self.assertIn("# Fixed by LLM", result.generated_code if result.generated_code else "")
        mock_get_source.assert_called_once_with("dummy.module", "old_func")
        mock_invoke_llm.assert_called_once()
        self.assertEqual(result.request_id, request.request_id) # Corrected assertion

    @mock.patch('ai_assistant.code_synthesis.service.self_modification.get_function_source_code')
    @mock.patch('ai_assistant.code_synthesis.service.invoke_ollama_model_async', new_callable=mock.AsyncMock)
    async def test_handle_existing_tool_self_fix_llm_no_code_from_llm(self, mock_invoke_llm, mock_get_source):
        mock_get_source.return_value = "def old_func(a): return a"
        mock_invoke_llm.return_value = "// NO_CODE_SUGGESTION_POSSIBLE"

        request = CodeTaskRequest(task_type=CodeTaskType.EXISTING_TOOL_SELF_FIX_LLM, context_data={
            "module_path": "dummy.module", "function_name": "old_func", "problem_description": "Needs a fix"
        })
        result = await self.service._handle_existing_tool_self_fix_llm(request)

        self.assertEqual(result.status, CodeTaskStatus.FAILURE_LLM_GENERATION)
        self.assertIsNone(result.generated_code)

    @mock.patch('ai_assistant.code_synthesis.service.self_modification.get_function_source_code')
    async def test_handle_existing_tool_self_fix_llm_no_original_code(self, mock_get_source):
        mock_get_source.return_value = None

        request = CodeTaskRequest(task_type=CodeTaskType.EXISTING_TOOL_SELF_FIX_LLM, context_data={
            "module_path": "dummy.module", "function_name": "old_func", "problem_description": "Needs a fix"
        })
        result = await self.service._handle_existing_tool_self_fix_llm(request)

        self.assertEqual(result.status, CodeTaskStatus.FAILURE_PRECONDITION)
        self.assertIsNotNone(result.error_message)

    async def test_submit_task_unsupported_type(self):
        # Create a dummy enum member for test by directly assigning an int value outside the Enum definition
        # This is a bit hacky for testing but avoids modifying the original Enum for a test case.
        class MockUnsupportedTaskType(Enum):
             BOGUS_TASK = 999
             # Add existing valid values to satisfy isinstance checks if any occur before dispatch
             NEW_TOOL_CREATION_LLM = CodeTaskType.NEW_TOOL_CREATION_LLM.value
             EXISTING_TOOL_SELF_FIX_LLM = CodeTaskType.EXISTING_TOOL_SELF_FIX_LLM.value
             EXISTING_TOOL_SELF_FIX_AST = CodeTaskType.EXISTING_TOOL_SELF_FIX_AST.value

        request = CodeTaskRequest(task_type=MockUnsupportedTaskType.BOGUS_TASK, context_data={}) # type: ignore
        result = await self.service.submit_task(request)
        self.assertEqual(result.status, CodeTaskStatus.FAILURE_UNSUPPORTED_TASK)

    async def test_handle_new_tool_creation_llm_placeholder(self):
        request = CodeTaskRequest(task_type=CodeTaskType.NEW_TOOL_CREATION_LLM, context_data={"description": "test"})
        result = await self.service._handle_new_tool_creation_llm(request) # Test private method directly
        self.assertEqual(result.status, CodeTaskStatus.FAILURE_UNSUPPORTED_TASK)
        self.assertIn("not fully implemented", result.error_message or "")

if __name__ == '__main__': # pragma: no cover
    unittest.main()
