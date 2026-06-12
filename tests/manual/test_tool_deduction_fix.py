
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ai_assistant.learning.learning import LearningAgent
# from ai_assistant.tools.tool_system import tool_system_instance 

async def test_deduction_Alias():
    print("Test 1: Alias Mapping (tool_code_generator -> generate_new_tool_from_description)")
    
    # Mock LLM provider
    mock_llm = MagicMock()
    mock_llm.invoke_ollama_model_async = AsyncMock(return_value='{"tool_name": "tool_code_generator"}')
    
    # Mock ActionExecutor
    mock_executor = MagicMock()
    mock_executor.llm_interface = mock_llm
    
    # Initialize agent without action_executor arg (incorrect in previous attempt)
    # We pass None for managers to avoid complex init
    agent = LearningAgent(insights_filepath="test_insights_temp.json", task_manager=None, notification_manager=None)
    
    # Inject our mock executor to replace the real one created in __init__
    agent.action_executor = mock_executor
    
    # Call the method
    deduced = await agent._deduce_tool_from_description("Fix the code generator tool")
    
    print(f"Deduced Tool Name: {deduced}")
    
    if deduced == "generate_new_tool_from_description":
        print("PASS: Alias mapped correctly.")
    else:
        print(f"FAIL: Expected 'generate_new_tool_from_description', got '{deduced}'")

async def test_deduction_Validation():
    print("\nTest 2: Validation (Non-existent tool rejection)")
    
    # Mock LLM provider to return a completely fake name
    mock_llm = MagicMock()
    mock_llm.invoke_ollama_model_async = AsyncMock(return_value='{"tool_name": "super_fake_tool_xyz_123"}')
    
    mock_executor = MagicMock()
    mock_executor.llm_interface = mock_llm
    
    agent = LearningAgent(insights_filepath="test_insights_temp.json", task_manager=None, notification_manager=None)
    agent.action_executor = mock_executor
    
    deduced = await agent._deduce_tool_from_description("some description")
    
    print(f"Deduced Tool Name: {deduced}")
    
    if deduced is None:
        print("PASS: Non-existent tool rejected.")
    else:
        print(f"FAIL: Expected None, got '{deduced}'")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_deduction_Alias())
    loop.run_until_complete(test_deduction_Validation())
