import asyncio
from unittest.mock import MagicMock, patch
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ai_assistant.core.orchestrator import DynamicOrchestrator

async def test_full_history_context():
    # Mock dependencies
    planner = MagicMock()
    executor = MagicMock()
    learning_agent = MagicMock()
    action_executor = MagicMock()
    
    # Create orchestrator
    orchestrator = DynamicOrchestrator(
        planner=planner,
        executor=executor,
        learning_agent=learning_agent,
        action_executor=action_executor
    )
    
    # Create a LONG history (e.g., 50 messages)
    history = [{"role": "user", "content": f"Message {i}"} for i in range(50)]
    history.append({"role": "user", "content": "What was the very first message?"})
    
    # Mock invoke_gemini_model_async to capture the prompt
    with patch('ai_assistant.core.orchestrator.invoke_gemini_model_async') as mock_llm:
        # Return a dummy plan so it proceeds
        mock_llm.side_effect = [
            "Plan: FINAL ANSWER: It was Message 0.", # Strategist response
            "FINAL ANSWER: It was Message 0." # Operator response (if called, but strategist might short circuit if I didn't mock it right)
        ]
        
        # Call process_prompt
        await orchestrator.process_prompt("Current Goal", conversation_history=history)
        
    # Write results to a file
    output_path = r"C:\Users\Jason\.gemini\test_output_chat.txt"
    with open(output_path, "w") as f:
        f.write("STARTING TEST\n")
        
        # Verify the calls
        if not mock_llm.call_args_list:
             f.write("FAILURE: LLM was not called.\n")
             return
             
        call_args = mock_llm.call_args_list[0]
        # call_args is (args, kwargs)
        _, kwargs = call_args
        
        prompt_sent = kwargs.get('prompt')
        if not prompt_sent:
             # Fallback
             args, _ = call_args
             if args:
                 prompt_sent = args[0]
        
        if not prompt_sent:
            f.write("FAILURE: Could not find prompt in LLM call arguments.\n")
            return

        f.write(f"Prompt length: {len(prompt_sent)}\n")
        
        # Check if Message 0 (the first one) is in the prompt
        if "Message 0" in prompt_sent:
            f.write("SUCCESS: 'Message 0' found in prompt. Full history appears to be present.\n")
        else:
            f.write("FAILURE: 'Message 0' NOT found in prompt. History might be truncated.\n")
            
        # Check for Few-Shot Examples
        if "Few-Shot Examples (How to Think):" in prompt_sent:
            f.write("SUCCESS: Few-Shot Examples found in Strategist prompt.\n")
        else:
            f.write("FAILURE: Few-Shot Examples NOT found.\n")

    print(f"Test finished. Results written to {output_path}")

if __name__ == "__main__":
    asyncio.run(test_full_history_context())
