import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from ai_assistant.learning.learning import LearningAgent

class TestToolDeductionParsing(unittest.TestCase):
    def setUp(self):
        self.agent = LearningAgent(
            insights_filepath="dummy.json",
            task_manager=None,
            notification_manager=None
        )
        self.agent._load_insights = MagicMock()
        self.agent.action_executor = MagicMock()
        self.agent.action_executor.code_service = MagicMock()
        self.agent.action_executor.code_service.llm_provider = AsyncMock()
        self.get_tool_patcher = patch('ai_assistant.learning.learning.get_tool', return_value={"name": "mocked_tool"})
        self.mock_get_tool = self.get_tool_patcher.start()

    def tearDown(self):
        self.get_tool_patcher.stop()
    
    def test_json_parsing(self):
        async def run_test():
            mock_llm = self.agent.action_executor.code_service.llm_provider.invoke_ollama_model_async
            
            # Test 1: Simple JSON
            mock_llm.return_value = '{"tool_name": "get_weather"}'
            result = await self.agent._deduce_tool_from_description("desc")
            print(f"Test 1 Result: '{result}'")
            self.assertEqual(result, "get_weather")
            
            # Test 2: JSON wrapped in markdown
            mock_llm.return_value = '```json\n{"tool_name": "get_weather"}\n```'
            result = await self.agent._deduce_tool_from_description("desc")
            print(f"Test 2 Result: '{result}'")
            self.assertEqual(result, "get_weather")

            # Test 3: Null case
            mock_llm.return_value = '{"tool_name": null}'
            result = await self.agent._deduce_tool_from_description("desc")
            print(f"Test 3 Result: '{result}'")
            self.assertIsNone(result)

            # Test 4: JSON with extra text
            mock_llm.return_value = '   {"tool_name": "read_file"}   '
            result = await self.agent._deduce_tool_from_description("desc")
            print(f"Test 4 Result: '{result}'")
            self.assertEqual(result, "read_file")

        asyncio.run(run_test())

if __name__ == '__main__':
    unittest.main()
