import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__))))

from ai_assistant.utils.conversational_helpers import summarize_tool_result_conversationally

# Mock Provider
class MockProvider:
    async def invoke_ollama_model_async(self, prompt: str, model_name: str, temperature: float) -> str:
        print(f"\n[MOCK LLM] Received Prompt:\n{prompt}\n[END MOCK LLM]\n")
        return "Processed."

async def test_fact_summarization():
    print("--- Testing Fact Summarization Logic ---")
    
    # Simulate the output of recall_facts
    facts_data = [
        {'fact_id': 'fact_1', 'text': 'The AI can access Google Search.', 'category': 'general'},
        {'fact_id': 'fact_2', 'text': 'The user lives in Glasgow, KY.', 'category': 'user'},
        {'fact_id': 'fact_3', 'text': 'The user\'s name is jason kinslwo.', 'category': 'user'},
        {'fact_id': 'fact_4', 'text': 'Python is a programming language.', 'category': 'general'},
    ]
    
    # 20+ items to test truncation logic trigger
    for i in range(20):
        facts_data.append({'fact_id': f'fact_extra_{i}', 'text': f'Extra fact number {i}', 'category': 'filler'})

    plan = [{"tool_name": "recall_facts", "args": (), "kwargs": {}}]
    results = [facts_data]
    
    provider = MockProvider()
    
    await summarize_tool_result_conversationally(
        original_user_query="What do you know about me?",
        executed_plan_steps=plan,
        tool_results=results,
        overall_success=True,
        llm_provider=provider
    )
    print("--- Test Finished ---")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_fact_summarization())
