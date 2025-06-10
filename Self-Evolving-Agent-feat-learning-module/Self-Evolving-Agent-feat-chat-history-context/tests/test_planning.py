import unittest
from unittest.mock import patch
import json # To construct mock LLM responses
from ai_assistant.planning.planning import PlannerAgent

class TestPlannerAgentLLMSearch(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures, if any."""
        self.planner = PlannerAgent()
        self.available_tools = {
            "search_duckduckgo": "Searches the internet using DuckDuckGo. Args: query (str). Returns JSON string.",
            "process_search_results": "Processes JSON search results. Args: search_query (str), search_results_json (str). Optional kwargs: processing_instruction (str: 'answer_query' (default), 'summarize_results', 'extract_entities', 'custom_instruction:<your_request>'). Returns natural language text.",
            "no_op_tool": "Does nothing, useful for default plans or when no other tool is suitable."
            # Example of another tool the LLM might consider for other tasks:
            # "calculate_math": "Calculates simple math expressions. Args: expression (str). Returns number."
        }

    @patch('ai_assistant.planning.planning.invoke_ollama_model')
    def test_plan_search_answer_query(self, mock_invoke_llm):
        """Test planning a search with default 'answer_query' processing."""
        goal = "What is the current weather in London?"
        
        llm_response_json = json.dumps([
            {"tool_name": "search_duckduckgo", "args": ["current weather in London"], "kwargs": {}},
            {"tool_name": "process_search_results", "args": ["current weather in London", "[[step_1_output]]"], "kwargs": {}}
        ])
        mock_invoke_llm.return_value = llm_response_json

        plan = self.planner.create_plan_with_llm(goal, self.available_tools)

        self.assertIsInstance(plan, list)
        self.assertEqual(len(plan), 2)
        
        self.assertEqual(plan[0]['tool_name'], "search_duckduckgo")
        self.assertEqual(plan[0]['args'], ("current weather in London",))
        self.assertEqual(plan[0]['kwargs'], {})

        self.assertEqual(plan[1]['tool_name'], "process_search_results")
        self.assertEqual(plan[1]['args'], ("current weather in London", "[[step_1_output]]"))
        self.assertEqual(plan[1]['kwargs'], {}) # Default processing_instruction is answer_query

    @patch('ai_assistant.planning.planning.invoke_ollama_model')
    def test_plan_search_summarize_results(self, mock_invoke_llm):
        """Test planning a search with 'summarize_results' processing."""
        goal = "Summarize the latest advancements in quantum computing."
        
        llm_response_json = json.dumps([
            {"tool_name": "search_duckduckgo", "args": ["latest advancements in quantum computing"], "kwargs": {}},
            {"tool_name": "process_search_results", "args": ["latest advancements in quantum computing", "[[step_1_output]]"], "kwargs": {"processing_instruction": "summarize_results"}}
        ])
        mock_invoke_llm.return_value = llm_response_json

        plan = self.planner.create_plan_with_llm(goal, self.available_tools)

        self.assertIsInstance(plan, list)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[1]['tool_name'], "process_search_results")
        self.assertEqual(plan[1]['args'], ("latest advancements in quantum computing", "[[step_1_output]]"))
        self.assertEqual(plan[1]['kwargs'], {"processing_instruction": "summarize_results"})

    @patch('ai_assistant.planning.planning.invoke_ollama_model')
    def test_plan_search_extract_entities(self, mock_invoke_llm):
        """Test planning a search with 'extract_entities' processing."""
        goal = "Extract key people mentioned in articles about the G7 summit."
        
        llm_response_json = json.dumps([
            {"tool_name": "search_duckduckgo", "args": ["key people in articles about G7 summit"], "kwargs": {}},
            {"tool_name": "process_search_results", "args": ["key people in articles about G7 summit", "[[step_1_output]]"], "kwargs": {"processing_instruction": "extract_entities"}}
        ])
        mock_invoke_llm.return_value = llm_response_json

        plan = self.planner.create_plan_with_llm(goal, self.available_tools)

        self.assertIsInstance(plan, list)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[1]['tool_name'], "process_search_results")
        self.assertEqual(plan[1]['args'], ("key people in articles about G7 summit", "[[step_1_output]]"))
        self.assertEqual(plan[1]['kwargs'], {"processing_instruction": "extract_entities"})

    @patch('ai_assistant.planning.planning.invoke_ollama_model')
    def test_plan_search_custom_instruction(self, mock_invoke_llm):
        """Test planning a search with a custom processing instruction."""
        goal = "Find out the main arguments against nuclear power from web results."
        
        llm_response_json = json.dumps([
            {"tool_name": "search_duckduckgo", "args": ["main arguments against nuclear power"], "kwargs": {}},
            {"tool_name": "process_search_results", "args": ["main arguments against nuclear power", "[[step_1_output]]"], "kwargs": {"processing_instruction": "custom_instruction:Extract the main arguments against nuclear power"}}
        ])
        mock_invoke_llm.return_value = llm_response_json

        plan = self.planner.create_plan_with_llm(goal, self.available_tools)

        self.assertIsInstance(plan, list)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[1]['tool_name'], "process_search_results")
        self.assertEqual(plan[1]['args'], ("main arguments against nuclear power", "[[step_1_output]]"))
        self.assertEqual(plan[1]['kwargs'], {"processing_instruction": "custom_instruction:Extract the main arguments against nuclear power"})

    @patch('ai_assistant.planning.planning.invoke_ollama_model')
    def test_plan_no_search_for_simple_math(self, mock_invoke_llm):
        """Test that simple math queries do not trigger a web search."""
        goal = "What is 5 plus 5?"
        
        # LLM might decide no tool is appropriate or use a general no_op/placeholder
        llm_response_json = json.dumps([
            {"tool_name": "no_op_tool", "args": [], "kwargs": {"note":"LLM decided no specific tool needed or cannot answer."}}
        ])
        mock_invoke_llm.return_value = llm_response_json

        plan = self.planner.create_plan_with_llm(goal, self.available_tools)

        self.assertIsInstance(plan, list)
        if plan: # Plan might be empty if LLM returns [] and no_op_tool is not forced
            for step in plan:
                self.assertNotEqual(step['tool_name'], "search_duckduckgo", "Search tool should not be used for simple math.")
            # Check if the no_op_tool was used as per mock
            if len(plan) == 1:
                 self.assertEqual(plan[0]['tool_name'], "no_op_tool")


    @patch('ai_assistant.planning.planning.invoke_ollama_model')
    def test_plan_no_search_for_creative_task(self, mock_invoke_llm):
        """Test that creative tasks do not trigger a web search."""
        goal = "Write a short story about a dragon."

        llm_response_json = json.dumps([
            {"tool_name": "no_op_tool", "args": [], "kwargs": {"note":"LLM decided no specific tool needed or cannot answer with available tools."}}
        ])
        mock_invoke_llm.return_value = llm_response_json

        plan = self.planner.create_plan_with_llm(goal, self.available_tools)

        self.assertIsInstance(plan, list)
        if plan:
            for step in plan:
                self.assertNotEqual(step['tool_name'], "search_duckduckgo", "Search tool should not be used for creative tasks.")
            if len(plan) == 1:
                 self.assertEqual(plan[0]['tool_name'], "no_op_tool")

if __name__ == '__main__':
    unittest.main()
