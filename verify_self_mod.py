
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import ast
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath("c:/Users/Jason/Desktop/Self Evolving AI"))

from ai_assistant.core.self_modification import _parse_and_validate_function_update, edit_function_source_code

class TestSelfModificationValidation(unittest.IsolatedAsyncioTestCase):
    
    def test_validate_valid_code(self):
        code = """
import os
def my_func():
    print("Hello")
"""
        node, imports, err = _parse_and_validate_function_update(code, "my_func")
        self.assertIsNone(err)
        self.assertIsNotNone(node)
        self.assertEqual(len(imports), 1)

    def test_validate_multiple_functions(self):
        code = """
def my_func():
    pass
def helper():
    pass
"""
        node, imports, err = _parse_and_validate_function_update(code, "my_func")
        self.assertIsNotNone(err)
        self.assertIn("multiple function definitions", err)

    def test_validate_top_level_statement(self):
        code = """
print("Side effect")
def my_func():
    pass
"""
        node, imports, err = _parse_and_validate_function_update(code, "my_func")
        self.assertIsNotNone(err)
        self.assertIn("unsupported top-level statement", err)

    @patch("ai_assistant.core.self_modification._resolve_file_path_robust")
    @patch("ai_assistant.core.self_modification.get_function_source_code")
    async def test_edit_function_source_code_fails_early(self, mock_get_source, mock_resolve):
        # Setup mocks to pass the file checks
        mock_resolve.return_value = "dummy.py"
        mock_get_source.return_value = "def my_func(): pass"
        
        # We also need to mock reading the file
        with patch("builtins.open", unittest.mock.mock_open(read_data="def my_func(): pass")):
            
            # Invalid code
            bad_code = """
def my_func():
    pass
def extra():
    pass
"""
            # This should fail BEFORE generating diff or calling Critic
            result = await edit_function_source_code("mod", "my_func", bad_code, ".", "change")
            
            self.assertIn("multiple function definitions", result)

if __name__ == "__main__":
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    unittest.main()
