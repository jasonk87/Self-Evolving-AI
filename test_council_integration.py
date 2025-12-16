import asyncio
import logging
import sys
import os
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.getcwd())

# --- MOCKING VIA SYS.MODULES ---
# We must inject mocks BEFORE importing the code that uses them
mock_reviewer_module = MagicMock()
mock_critic_module = MagicMock()

sys.modules["ai_assistant.core.reviewer"] = mock_reviewer_module
sys.modules["ai_assistant.core.critical_reviewer"] = mock_critic_module

# define the classes on the mock modules
class MockReviewerAgent:
    def __init__(self, name): pass

mock_reviewer_module.ReviewerAgent = MockReviewerAgent
mock_critic_module.CriticalReviewCoordinator = MagicMock()

# Now import the SUT
from ai_assistant.custom_tools.meta_programming_tools import generate_new_tool_from_description

# Configure logging
logging.basicConfig(level=logging.INFO)

class MockLLM:
    """Mocks the LLM to provide a tool."""
    async def invoke_ollama_model_async(self, prompt, model_name, temperature=0.2):
        if "Critical" in prompt or "Reviewer" in prompt:
             return "Status: APPROVED\nReasoning: Mock Approval via sys.modules."
        return """
```python
def council_sys_test_tool():
    return "Sys Tested"
```
Suggested Filename: council_sys_test_tool.py
"""
    async def send_request(self, prompt, model_name, temperature=0.2):
        return await self.invoke_ollama_model_async(prompt, model_name, temperature)

class MockActionExecutor:
    def __init__(self):
        self.llm_interface = MockLLM()

async def verify_council_integration():
    print("Verifying Council Integration (Attempt 3 - sys.modules)...")
    
    MockCoordinator = mock_critic_module.CriticalReviewCoordinator
    mock_instance = MockCoordinator.return_value
    
    # Async mock setup
    f = asyncio.Future()
    f.set_result((True, "Mock Council Approval in sys.modules"))
    mock_instance.execute_council_debate.return_value = f

    executor = MockActionExecutor()
    
    print("Calling generate_new_tool_from_description...")
    result = await generate_new_tool_from_description(
        tool_description="Create a test tool for sys verification",
        action_executor=executor
    )
    
    print(f"Generation Result: {result}")
    
    if MockCoordinator.called:
        print("SUCCESS: CriticalReviewCoordinator was instantiated.")
    else:
        print("FAILURE: CriticalReviewCoordinator was NOT instantiated.")
        
    if mock_instance.execute_council_debate.called:
         print("SUCCESS: execute_council_debate was called.")
    else:
         print("FAILURE: execute_council_debate was NOT called.")

if __name__ == "__main__":
    asyncio.run(verify_council_integration())
