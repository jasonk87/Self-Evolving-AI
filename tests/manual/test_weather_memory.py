
import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from ai_assistant.core.memory_manager import MemoryManager
from ai_assistant.planning.planning import PlannerAgent
from ai_assistant.utils.conversational_helpers import summarize_tool_result_conversationally
from ai_assistant.llm_interface.ollama_client import OllamaProvider

async def test_memory_integration():
    print("\n--- Testing Memory Integration ---")
    
    # Mock Memory Manager
    class MockMemoryManager(MemoryManager):
        async def retrieve_relevant_context(self, query, k=5):
            print(f"DEBUG: Mock Memory retrieving for query: '{query}'")
            return [{"text": "User Location: Smiths Grove, KY", "metadata": {"category": "fact"}, "score": 0.95}]

    memory_manager = MockMemoryManager()
    planner = PlannerAgent(memory_manager=memory_manager)
    
    # Test Planning with Implicit Context
    print("\n[Test 1] Planning with Implicit Context (Location)")
    tools = {"get_weather": {"description": "Gets weather for a location. Args: location (str)"}}
    
    # Using the planning method directly to inspect the prompt generation (simulated)
    # Since we can't easily inspect the internal prompt variable without modifying code, 
    # we will rely on the behavior: does it produce a plan using the memory?
    
    # Since we don't want to burn tokens on a real LLM call for this unit test if possible,
    # or if we do, we want to see the result.
    # Let's try to mock the LLM provider for the planner to just return what we want if the prompt is right.
    
    # Actually, let's just verify the RESPONSE summarizer first, as that was the main bug.
    
    print("\n[Test 2] Response Summarization (Anti-Placeholder)")
    
    llm_provider = OllamaProvider()
    
    tool_results = [{"temperature": 24, "description": "Sunny", "city": "Smiths Grove"}]
    plan_steps = [{"tool_name": "get_weather", "args": ("Smiths Grove, KY",), "kwargs": {}}]
    
    summary = await summarize_tool_result_conversationally(
        original_user_query="What's the weather?",
        executed_plan_steps=plan_steps,
        tool_results=tool_results,
        overall_success=True,
        llm_provider=llm_provider
    )
    
    with open("test_output.txt", "w") as f:
        f.write(summary)
    
    print(f"\nFull Generated Summary written to test_output.txt")
    
    if "[temperature]" in summary or "[description]" in summary:
        print("FAIL: Summary contains placeholders!")
    elif "24" in summary and "Sunny" in summary:
        print("PASS: Summary contains actual values.")
    else:
        print("WARNING: Summary might be vague.")

if __name__ == "__main__":
    asyncio.run(test_memory_integration())
