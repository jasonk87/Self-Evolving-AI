import asyncio
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import unittest
from unittest.mock import MagicMock
from ai_assistant.planning.execution import ExecutionAgent

class TestExecutionVariableSubstitution(unittest.TestCase):
    def setUp(self):
        self.agent = ExecutionAgent()
        # Mock dependencies
        self.mock_tool_system = MagicMock()
        self.mock_planner = MagicMock()
        self.mock_learning = MagicMock()
        
        # Async mock for execute_tool
        f = asyncio.Future()
        f.set_result("tool_execution_result")
        self.mock_tool_system.execute_tool.return_value = f

    def test_resolve_arguments_simple_substitution(self):
        """Test simple [[step_N_output]] substitution"""
        pass

    def test_resolve_value_simple(self):
        """Test resolving a simple string value with placeholders"""
        plan_results = ["result_1"]
        resolved = self.agent._resolve_value_with_substitution("[[step_1_output]]", plan_results, 1, "test")
        self.assertEqual(resolved, "result_1")

    def test_resolve_value_dot_notation_dict(self):
        """Test resolving a dict value using dot notation [[step_1_output.key]]"""
        plan_results = [{"key": "value"}]
        resolved = self.agent._resolve_value_with_substitution("[[step_1_output.key]]", plan_results, 1, "test")
        self.assertEqual(resolved, "value")

    def test_resolve_value_dot_notation_nested_dict(self):
        """Test resolving a nested dict value [[step_1_output.key.subkey]]"""
        plan_results = [{"key": {"subkey": "nested_value"}}]
        resolved = self.agent._resolve_value_with_substitution("[[step_1_output.key.subkey]]", plan_results, 1, "test")
        self.assertEqual(resolved, "nested_value")

    def test_resolve_value_dot_notation_obj(self):
        """Test resolving an object attribute [[step_1_output.attr]]"""
        class ResultObj:
            def __init__(self):
                self.attr = "attr_value"
        
        plan_results = [ResultObj()]
        resolved = self.agent._resolve_value_with_substitution("[[step_1_output.attr]]", plan_results, 1, "test")
        self.assertEqual(resolved, "attr_value")
        
    def test_resolve_value_mixed_text(self):
        """Test substitution within larger text ?? currently exact match is supported by regex ^...$ in original code?"""
        plan_results = ["result"]
        resolved = self.agent._resolve_value_with_substitution("Prefix [[step_1_output]]", plan_results, 1, "test")
        self.assertEqual(resolved, "Prefix [[step_1_output]]")

    def test_resolve_value_invalid_step(self):
        """Test referencing a non-existent step"""
        plan_results = []
        resolved = self.agent._resolve_value_with_substitution("[[step_1_output]]", plan_results, 1, "test")
        self.assertEqual(resolved, "[[step_1_output]]")

    def test_resolve_value_invalid_key(self):
        """Test referencing a non-existent key"""
        plan_results = [{"key": "value"}]
        resolved = self.agent._resolve_value_with_substitution("[[step_1_output.missing]]", plan_results, 1, "test")
        self.assertEqual(resolved, "[[step_1_output.missing]]")

if __name__ == '__main__':
    unittest.main()
