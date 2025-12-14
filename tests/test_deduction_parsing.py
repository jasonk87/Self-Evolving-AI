import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from ai_assistant.learning.learning import LearningAgent

class TestToolDeductionParsing(unittest.TestCase):
    def setUp(self):
        self.agent = LearningAgent(
            insights_filepath="dummy.json",
            task_manager=None,
            notification_manager=None
        )
        self.agent._load_insights = MagicMock()
    
    def test_json_parsing(self):
        async def run_test():
            from unittest.mock import patch
            with patch('ai_assistant.learning.learning.invoke_gemini_model_async', new_callable=AsyncMock) as mock_llm:
                
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

                # Test 4: JSON with extra text (should arguably fail or handle if we strip well enough, but current logic strips outer but maybe not pre-text if not markdown)
                # Current logic: cleaned_response = response.strip(); if "```" in ...
                # If valid JSON is returned, it works.
                mock_llm.return_value = '   {"tool_name": "read_file"}   '
                result = await self.agent._deduce_tool_from_description("desc")
                print(f"Test 4 Result: '{result}'")
                self.assertEqual(result, "read_file")

        asyncio.run(run_test())

if __name__ == '__main__':
    unittest.main()
