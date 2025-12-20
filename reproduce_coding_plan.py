import asyncio
from unittest.mock import MagicMock, patch
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ai_assistant.planning.planning import PlannerAgent

async def test_coding_plan_prompt():
    # Mock memory manager
    memory_manager = MagicMock()
    
    # Create planner
    planner = PlannerAgent(memory_manager=memory_manager)
    
    # Define available tools (simplified for test)
    available_tools = {
        "read_file": {"description": "Reads a file", "schema_details": {}},
        "write_to_file": {"description": "Writes to a file", "schema_details": {}}
    }
    
    # Mock invoke_ollama_model_async to capture the prompt
    with patch('ai_assistant.planning.planning.invoke_ollama_model_async') as mock_llm:
        # Return a dummy plan
        mock_llm.return_value = '```json\n[{"tool_name": "read_file", "args": ["test.py"], "kwargs": {}}]\n```'
        
        # Call create_plan_with_llm
        await planner.create_plan_with_llm(
            goal_description="Refactor main.py",
            available_tools=available_tools
        )
        
    # Write results to a file
    output_path = r"C:\Users\Jason\.gemini\test_output_coding.txt"
    with open(output_path, "w") as f:
        f.write("STARTING CODING PLAN PROMPT TEST\n")
        
        if not mock_llm.call_args_list:
             f.write("FAILURE: LLM was not called.\n")
             return
             
        call_args = mock_llm.call_args_list[0]
        # call_args is (args, kwargs) if async mock works similarly, usually it's args[0] for first pos arg
        args, _ = call_args
        prompt_sent = args[0]
        
        if not prompt_sent:
            f.write("FAILURE: Could not find prompt.\n")
            return
            
        f.write(f"Prompt length: {len(prompt_sent)}\n")
        
        # Check for Few-Shot Examples
        if "Few-Shot Examples (How to Plan):" in prompt_sent:
            f.write("SUCCESS: Few-Shot Coding Examples found.\n")
        else:
            f.write("FAILURE: Few-Shot Coding Examples NOT found.\n")

        # Check for Verification Instruction
        if "VERIFY FIRST" in prompt_sent:
            f.write("SUCCESS: Verification instruction found.\n")
        else:
            f.write("FAILURE: Verification instruction NOT found.\n")

    print(f"Test finished. Results written to {output_path}")

if __name__ == "__main__":
    asyncio.run(test_coding_plan_prompt())
