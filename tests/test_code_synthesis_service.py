import unittest
from unittest import mock
import asyncio
import os
import sys
import json
from enum import Enum

try:
    from ai_assistant.code_synthesis import CodeSynthesisService, CodeTaskRequest, CodeTaskType, CodeTaskStatus, CodeTaskResult
except ImportError: # pragma: no cover
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ai_assistant.code_synthesis import CodeSynthesisService, CodeTaskRequest, CodeTaskType, CodeTaskStatus, CodeTaskResult

class TestCodeSynthesisService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.service = CodeSynthesisService()

    @mock.patch('ai_assistant.code_synthesis.service.CodeSynthesisService._run_linter', new_callable=mock.AsyncMock)
    @mock.patch('ai_assistant.code_synthesis.service.invoke_ollama_model_async', new_callable=mock.AsyncMock)
    async def test_handle_new_tool_creation_llm_success(self, mock_invoke_llm, mock_linter):
        mock_invoke_llm.return_value = """# METADATA: {"suggested_function_name": "new_tool", "suggested_tool_name": "newTool", "suggested_description": "A new tool"}
def new_tool():
    pass
"""
        mock_linter.return_value = ([], None)

        request = CodeTaskRequest(
            task_type=CodeTaskType.NEW_TOOL_CREATION_LLM,
            context_data={"description": "Create a new tool"}
        )

        result = await self.service.submit_task(request)

        self.assertEqual(result.status, CodeTaskStatus.SUCCESS)
        self.assertIn("def new_tool():", result.generated_code)
        self.assertEqual(result.metadata["parsed_tool_metadata"]["suggested_function_name"], "new_tool")

    @mock.patch('ai_assistant.code_synthesis.service.self_modification.get_function_source_code')
    @mock.patch('ai_assistant.code_synthesis.service.CodeSynthesisService._run_linter', new_callable=mock.AsyncMock)
    @mock.patch('ai_assistant.code_synthesis.service.invoke_ollama_model_async', new_callable=mock.AsyncMock)
    async def test_handle_existing_tool_self_fix_llm_success(self, mock_invoke_llm, mock_linter, mock_get_source):
        mock_get_source.return_value = "def old_func(a): return a"
        mock_invoke_llm.return_value = "def old_func(a): return a + 1"
        mock_linter.return_value = ([], None)

        request = CodeTaskRequest(
            task_type=CodeTaskType.EXISTING_TOOL_SELF_FIX_LLM,
            context_data={
                "module_path": "dummy.module",
                "function_name": "old_func",
                "problem_description": "Needs a fix"
            }
        )

        result = await self.service.submit_task(request)

        self.assertEqual(result.status, CodeTaskStatus.SUCCESS)
        self.assertEqual(result.generated_code, "def old_func(a): return a + 1")

    @mock.patch('ai_assistant.code_synthesis.service.invoke_ollama_model_async', new_callable=mock.AsyncMock)
    async def test_handle_hierarchical_outline_success(self, mock_invoke_llm):
        outline_json = {
            "module_docstring": "Test module",
            "imports": ["os"],
            "components": [
                {"type": "function", "name": "test_func", "description": "A test function"}
            ]
        }
        mock_invoke_llm.return_value = json.dumps(outline_json)

        request = CodeTaskRequest(
            task_type=CodeTaskType.HIERARCHICAL_GENERATION_OUTLINE,
            context_data={"description": "Create a test module"}
        )

        result = await self.service.submit_task(request)

        self.assertEqual(result.status, CodeTaskStatus.SUCCESS)
        self.assertEqual(result.metadata["parsed_outline"], outline_json)

    @mock.patch('ai_assistant.code_synthesis.service.CodeSynthesisService._run_linter', new_callable=mock.AsyncMock)
    @mock.patch('ai_assistant.code_synthesis.service.invoke_ollama_model_async', new_callable=mock.AsyncMock)
    async def test_handle_hierarchical_full_success(self, mock_invoke_llm, mock_linter):
        # Mock responses for outline and detail generation
        outline_json = {
            "module_docstring": "Test module",
            "imports": ["os"],
            "components": [
                {
                    "type": "function",
                    "name": "test_func",
                    "description": "A test function",
                    "signature": "() -> None",
                    "body_placeholder": "Pass"
                }
            ]
        }

        # We need side_effect to return different responses for sequential calls
        # 1. Outline generation
        # 2. Detail generation for test_func
        mock_invoke_llm.side_effect = [
            json.dumps(outline_json), # Outline response
            "def test_func() -> None:\n    pass" # Detail response
        ]
        mock_linter.return_value = ([], None)

        request = CodeTaskRequest(
            task_type=CodeTaskType.HIERARCHICAL_GENERATION_FULL,
            context_data={"description": "Create a test module"}
        )

        result = await self.service.submit_task(request)

        self.assertEqual(result.status, CodeTaskStatus.SUCCESS)
        self.assertIn("def test_func() -> None:", result.generated_code)
        self.assertIn("import os", result.generated_code)

    async def test_submit_task_unsupported_type(self):
        class MockUnsupportedTaskType(Enum):
             BOGUS_TASK = 999

        # We need to trick the type checker or just pass it in if python allows dynamic enum passing (it usually does for duck typing if not strict)
        # But here the service checks `request.task_type.name` probably or equality.
        # Actually `request.task_type` is expected to be CodeTaskType enum.
        # Let's just create a request with a fake type.

        request = CodeTaskRequest(task_type=MockUnsupportedTaskType.BOGUS_TASK, context_data={}) # type: ignore
        result = await self.service.submit_task(request)
        self.assertEqual(result.status, CodeTaskStatus.FAILURE_UNSUPPORTED_TASK)

if __name__ == '__main__': # pragma: no cover
    unittest.main()
