import unittest
from unittest.mock import patch
import json # To construct mock LLM responses
from ai_assistant.planning.planning import PlannerAgent

class TestPlannerAgentLLMSearch(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures, if any."""
        self.planner = PlannerAgent()

        # Schema for request_user_clarification, to be included in available_tools
        self.REQUEST_USER_CLARIFICATION_SCHEMA = {
            "name": "request_user_clarification",
            "description": "Asks the user a clarifying question to resolve ambiguities or gather missing information needed to complete a task. Returns the user's textual response.",
            "parameters": [
                {"name": "question_text", "type": "str", "description": "The question to ask the user."},
                {"name": "options", "type": "list", "description": "Optional. A list of suggested string options for the user to choose from or consider."}
            ],
            "returns": { # Added for completeness, though planner might not use it directly
                "type": "str",
                "description": "The user's textual reply to the clarification question."
            }
        }

        self.available_tools_rich = { # Renamed to avoid conflict if old tests used the simple dict
            "search_duckduckgo": {
                "description": "Searches the internet using DuckDuckGo. Args: query (str). Returns JSON string.",
                "schema_details": {
                    "name": "search_duckduckgo",
                    "description": "Searches the internet using DuckDuckGo. Args: query (str). Returns JSON string.",
                    "parameters": [{"name": "query", "type": "str", "description": "The search query."}],
                }
            },
            "process_search_results": {
                "description": "Processes JSON search results. Args: search_query (str), search_results_json (str). Optional kwargs: processing_instruction (str: 'answer_query' (default), 'summarize_results', 'extract_entities', 'custom_instruction:<your_request>'). Returns natural language text.",
                "schema_details": {
                    "name": "process_search_results",
                    "description": "Processes JSON search results to answer a query, summarize, or extract entities.",
                    "parameters": [
                        {"name": "search_query", "type": "str", "description": "The original search query."},
                        {"name": "search_results_json", "type": "str", "description": "The JSON string of search results."}
                    ],
                    "kwargs_parameters": [ # Assuming kwargs can also be defined in schema
                        {"name": "processing_instruction", "type": "str", "description": "How to process results ('answer_query', 'summarize_results', etc.)."}
                    ]
                }
            },
            "no_op_tool": {
                "description": "Does nothing, useful for default plans or when no other tool is suitable.",
                "schema_details": {"name": "no_op_tool", "description": "Does nothing.", "parameters": []}
            },
            "request_user_clarification": {
                "description": self.REQUEST_USER_CLARIFICATION_SCHEMA["description"],
                "schema_details": self.REQUEST_USER_CLARIFICATION_SCHEMA
            },
            "find_agent_tool_source": {
                "description": "Finds an agent tool's source code, module path, etc.",
                "schema_details": {
                    "name": "find_agent_tool_source",
                    "description": "Finds an existing agent tool's source code, module path, and file path. Searches in standard agent tool directories.",
                    "parameters": [{"name": "tool_name", "type": "str", "description": "The name of the agent tool to find (e.g., 'my_calculator')."}],
                    "returns": {"type": "dict", "description": "A dictionary with 'module_path', 'function_name', 'file_path', and 'source_code', or null if not found."}
                }
            },
            "call_code_service_modify_code": { # Placeholder tool name from planner prompt
                "description": "Invokes CodeService to generate modified code for a function.",
                "schema_details": {
                    "name": "call_code_service_modify_code",
                    "description": "Generates modified code for a given function using CodeService based on an instruction.",
                    "parameters": [
                        {"name": "module_path", "type": "str", "description": "Module path of the function."},
                        {"name": "function_name", "type": "str", "description": "Name of the function."},
                        {"name": "existing_code", "type": "str", "description": "The current source code of the function."},
                        {"name": "modification_instruction", "type": "str", "description": "Detailed instruction for the change."},
                        {"name": "context", "type": "str", "description": "CodeService context (e.g., GRANULAR_CODE_REFACTOR, SELF_FIX_TOOL)."}
                    ],
                    "returns": {"type": "dict", "description": "Dict with 'modified_code_string', 'status', etc."}
                }
            },
            "stage_agent_tool_modification": {
                "description": "Stages parameters for proposing an agent tool modification.",
                "schema_details": {
                    "name": "stage_agent_tool_modification",
                    "description": "Stages the parameters needed to propose a modification to an existing agent tool for later execution by ActionExecutor.",
                    "parameters": [
                        {"name": "module_path", "type": "str", "description": "Module path of the tool to be modified."},
                        {"name": "function_name", "type": "str", "description": "Function name of the tool to be modified."},
                        {"name": "modified_code_string", "type": "str", "description": "The complete new source code for the function."},
                        {"name": "change_description", "type": "str", "description": "A description of the changes made and the reason, suitable for review or commit messages."},
                        {"name": "original_reflection_entry_id", "type": "str", "description": "Optional ID of the reflection entry that triggered this modification."}
                    ],
                    "returns": {"type": "dict", "description": "A structured dictionary representing the staged modification, typically confirming the parameters were received."}
                }
            },
            "greet_user": { # Ensure the tool to be edited is also in the list
                "description": "Greets the user. Args: name (str)",
                "schema_details": {
                    "name": "greet_user",
                    "description": "Greets the user.",
                    "parameters": [{"name": "name", "type": "str", "description": "Name of person to greet."}]
                }
            }
        }

    @patch('ai_assistant.planning.planning.invoke_ollama_model_async')
    async def test_plan_search_answer_query(self, mock_invoke_llm_async):
        """Test planning a search with default 'answer_query' processing."""
        goal = "What is the current weather in London?"
        
        llm_response_json = json.dumps([
            {"tool_name": "search_duckduckgo", "args": ["current weather in London"], "kwargs": {}},
            {"tool_name": "process_search_results", "args": ["current weather in London", "[[step_1_output]]"], "kwargs": {}}
        ])
        mock_invoke_llm_async.return_value = llm_response_json # Mock the async version

        plan = await self.planner.create_plan_with_llm(goal, self.available_tools_rich) # Use rich tools

        self.assertIsInstance(plan, list)
        self.assertEqual(len(plan), 2)
        
        self.assertEqual(plan[0]['tool_name'], "search_duckduckgo")
        self.assertEqual(plan[0]['args'], ("current weather in London",))
        self.assertEqual(plan[0]['kwargs'], {})

        self.assertEqual(plan[1]['tool_name'], "process_search_results")
        self.assertEqual(plan[1]['args'], ("current weather in London", "[[step_1_output]]"))
        self.assertEqual(plan[1]['kwargs'], {}) # Default processing_instruction is answer_query

    @patch('ai_assistant.planning.planning.invoke_ollama_model_async')
    async def test_plan_search_summarize_results(self, mock_invoke_llm_async):
        """Test planning a search with 'summarize_results' processing."""
        goal = "Summarize the latest advancements in quantum computing."
        
        llm_response_json = json.dumps([
            {"tool_name": "search_duckduckgo", "args": ["latest advancements in quantum computing"], "kwargs": {}},
            {"tool_name": "process_search_results", "args": ["latest advancements in quantum computing", "[[step_1_output]]"], "kwargs": {"processing_instruction": "summarize_results"}}
        ])
        mock_invoke_llm_async.return_value = llm_response_json

        plan = await self.planner.create_plan_with_llm(goal, self.available_tools_rich)

        self.assertIsInstance(plan, list)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[1]['tool_name'], "process_search_results")
        self.assertEqual(plan[1]['args'], ("latest advancements in quantum computing", "[[step_1_output]]"))
        self.assertEqual(plan[1]['kwargs'], {"processing_instruction": "summarize_results"})

    @patch('ai_assistant.planning.planning.invoke_ollama_model_async')
    async def test_plan_search_extract_entities(self, mock_invoke_llm_async):
        """Test planning a search with 'extract_entities' processing."""
        goal = "Extract key people mentioned in articles about the G7 summit."
        
        llm_response_json = json.dumps([
            {"tool_name": "search_duckduckgo", "args": ["key people in articles about G7 summit"], "kwargs": {}},
            {"tool_name": "process_search_results", "args": ["key people in articles about G7 summit", "[[step_1_output]]"], "kwargs": {"processing_instruction": "extract_entities"}}
        ])
        mock_invoke_llm_async.return_value = llm_response_json

        plan = await self.planner.create_plan_with_llm(goal, self.available_tools_rich)

        self.assertIsInstance(plan, list)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[1]['tool_name'], "process_search_results")
        self.assertEqual(plan[1]['args'], ("key people in articles about G7 summit", "[[step_1_output]]"))
        self.assertEqual(plan[1]['kwargs'], {"processing_instruction": "extract_entities"})

    @patch('ai_assistant.planning.planning.invoke_ollama_model_async')
    async def test_plan_search_custom_instruction(self, mock_invoke_llm_async):
        """Test planning a search with a custom processing instruction."""
        goal = "Find out the main arguments against nuclear power from web results."
        
        llm_response_json = json.dumps([
            {"tool_name": "search_duckduckgo", "args": ["main arguments against nuclear power"], "kwargs": {}},
            {"tool_name": "process_search_results", "args": ["main arguments against nuclear power", "[[step_1_output]]"], "kwargs": {"processing_instruction": "custom_instruction:Extract the main arguments against nuclear power"}}
        ])
        mock_invoke_llm_async.return_value = llm_response_json

        plan = await self.planner.create_plan_with_llm(goal, self.available_tools_rich)

        self.assertIsInstance(plan, list)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[1]['tool_name'], "process_search_results")
        self.assertEqual(plan[1]['args'], ("main arguments against nuclear power", "[[step_1_output]]"))
        self.assertEqual(plan[1]['kwargs'], {"processing_instruction": "custom_instruction:Extract the main arguments against nuclear power"})

    @patch('ai_assistant.planning.planning.invoke_ollama_model_async')
    async def test_plan_no_search_for_simple_math(self, mock_invoke_llm_async):
        """Test that simple math queries do not trigger a web search."""
        goal = "What is 5 plus 5?"
        
        llm_response_json = json.dumps([
            {"tool_name": "no_op_tool", "args": [], "kwargs": {"note":"LLM decided no specific tool needed or cannot answer."}}
        ])
        mock_invoke_llm_async.return_value = llm_response_json

        plan = await self.planner.create_plan_with_llm(goal, self.available_tools_rich)

        self.assertIsInstance(plan, list)
        if plan:
            for step in plan:
                self.assertNotEqual(step['tool_name'], "search_duckduckgo", "Search tool should not be used for simple math.")
            if len(plan) == 1:
                 self.assertEqual(plan[0]['tool_name'], "no_op_tool")

    @patch('ai_assistant.planning.planning.invoke_ollama_model_async')
    async def test_plan_no_search_for_creative_task(self, mock_invoke_llm_async):
        """Test that creative tasks do not trigger a web search."""
        goal = "Write a short story about a dragon."

        llm_response_json = json.dumps([
            {"tool_name": "no_op_tool", "args": [], "kwargs": {"note":"LLM decided no specific tool needed or cannot answer with available tools."}}
        ])
        mock_invoke_llm_async.return_value = llm_response_json

        plan = await self.planner.create_plan_with_llm(goal, self.available_tools_rich)

        self.assertIsInstance(plan, list)
        if plan:
            for step in plan:
                self.assertNotEqual(step['tool_name'], "search_duckduckgo", "Search tool should not be used for creative tasks.")
            if len(plan) == 1:
                 self.assertEqual(plan[0]['tool_name'], "no_op_tool")

    @patch('ai_assistant.planning.planning.invoke_ollama_model_async')
    async def test_create_plan_with_llm_uses_clarification_tool_for_ambiguity(self, mock_invoke_llm_async):
        """Test that an ambiguous creation goal uses request_user_clarification."""
        goal = "Create an organizer for my thoughts." # Ambiguous: agent tool or user project?

        # Mock LLM to return a plan that uses the clarification tool
        clarification_question = "To help me create exactly what you need, could you clarify: Are you looking for a new capability/tool for me (the AI assistant), or are you asking to start a new software project for yourself?"
        options_list_str = "['Agent Tool', 'User Project']" # Planner stringifies list args

        llm_response_json = json.dumps([
            {
                "tool_name": "request_user_clarification",
                "args": [clarification_question, options_list_str],
                "kwargs": {}
            }
        ])
        mock_invoke_llm_async.return_value = llm_response_json

        plan = await self.planner.create_plan_with_llm(goal, self.available_tools_rich)

        self.assertIsInstance(plan, list, "Plan should be a list.")
        self.assertEqual(len(plan), 1, "Plan should have one step.")

        clarification_step = plan[0]
        self.assertEqual(clarification_step.get("tool_name"), "request_user_clarification")

        # Args are converted to tuple of strings by planner
        self.assertIsInstance(clarification_step.get("args"), tuple, "Args should be a tuple.")
        self.assertEqual(len(clarification_step.get("args", [])), 2, "Should have two arguments.")
        self.assertEqual(clarification_step["args"][0], clarification_question)
        self.assertEqual(clarification_step["args"][1], options_list_str) # Check for stringified list
        self.assertEqual(clarification_step.get("kwargs"), {})

    @patch('ai_assistant.planning.planning.invoke_ollama_model_async')
    async def test_create_plan_for_agent_tool_edit_feedback(self, mock_invoke_llm_async):
        """Tests that the planner generates the correct 3-step plan for editing an agent tool based on user feedback."""
        user_goal = "The 'greet_user' tool is too formal. Make it say 'Hey' instead of 'Hello'."

        # Expected synthesized modification instruction by the LLM planner
        expected_modification_instruction = "User wants it to say 'Hey' instead of 'Hello'. Change greeting to be more informal."
        # Expected change description (can be similar to user_goal or a summary)
        expected_change_description = "User request: The 'greet_user' tool is too formal. Make it say 'Hey' instead of 'Hello'."

        # Mock the LLM response to be the 3-step plan
        llm_response_json = json.dumps([
            {
                "tool_name": "find_agent_tool_source",
                "args": ["greet_user"],
                "kwargs": {}
            },
            {
                "tool_name": "call_code_service_modify_code",
                "args": [
                    "[[step_1_output.module_path]]",
                    "[[step_1_output.function_name]]",
                    "[[step_1_output.source_code]]",
                    expected_modification_instruction, # This is what the LLM should synthesize
                    "GRANULAR_CODE_REFACTOR" # LLM's choice based on prompt
                ],
                "kwargs": {}
            },
            {
                "tool_name": "stage_agent_tool_modification",
                "args": [
                    "[[step_1_output.module_path]]",
                    "[[step_1_output.function_name]]",
                    "[[step_2_output.modified_code_string]]",
                    expected_change_description, # This should be the user's goal or a summary
                    "" # original_reflection_entry_id (empty if not applicable)
                ],
                "kwargs": {}
            }
        ])
        mock_invoke_llm_async.return_value = llm_response_json

        plan = await self.planner.create_plan_with_llm(user_goal, self.available_tools_rich)

        self.assertIsInstance(plan, list, "Plan should be a list.")
        self.assertEqual(len(plan), 3, "Plan should have three steps for tool modification.")

        # Step 1: find_agent_tool_source
        step1 = plan[0]
        self.assertEqual(step1.get("tool_name"), "find_agent_tool_source")
        self.assertEqual(step1.get("args"), ("greet_user",)) # Planner converts list to tuple

        # Step 2: call_code_service_modify_code
        step2 = plan[1]
        self.assertEqual(step2.get("tool_name"), "call_code_service_modify_code")
        expected_args_step2 = (
            "[[step_1_output.module_path]]",
            "[[step_1_output.function_name]]",
            "[[step_1_output.source_code]]",
            expected_modification_instruction,
            "GRANULAR_CODE_REFACTOR"
        )
        self.assertEqual(step2.get("args"), expected_args_step2)

        # Step 3: stage_agent_tool_modification
        step3 = plan[2]
        self.assertEqual(step3.get("tool_name"), "stage_agent_tool_modification")
        expected_args_step3 = (
            "[[step_1_output.module_path]]",
            "[[step_1_output.function_name]]",
            "[[step_2_output.modified_code_string]]",
            expected_change_description,
            ""
        )
        self.assertEqual(step3.get("args"), expected_args_step3)


if __name__ == '__main__':
    unittest.main()
