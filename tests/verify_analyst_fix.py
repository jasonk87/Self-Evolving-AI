
import asyncio
import os
import sys
# Add project root to path
sys.path.insert(0, os.path.abspath("C:/Users/Jason/Desktop/Self Evolving AI"))

from ai_assistant.learning.conversation_analyst import ConversationalAnalyst

# Mock the LLM Response to test parsing logic without hitting the actual LLM (which is hard to deterministic)
# Or we can subclass/patch invoke_ollama_model_async
from unittest.mock import AsyncMock, patch

async def test_parsing():
    analyst = ConversationalAnalyst()
    
    # Mock LLM response with related_tool_name
    mock_json_response = """
    ```json
    {
      "insights": [
        {
          "type": "TOOL_BUG_SUSPECTED",
          "description": "The weather tool failed.",
          "related_tool_name": "get_weather",
          "evidence": "Access error.",
          "suggestion": "Fix it."
        }
      ]
    }
    ```
    """
    
    with patch("ai_assistant.learning.conversation_analyst.invoke_ollama_model_async", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_json_response
        
        session_data = {"id": "test_session", "history": [{"role": "user", "content": "hi"}]}
        insights = await analyst.analyze_session_transcript(session_data)
        
        if len(insights) == 1:
            print(f"Success: Found 1 insight.")
            print(f"Type: {insights[0].type.name}")
            print(f"Related Tool Name: {insights[0].related_tool_name}")
            if insights[0].related_tool_name == "get_weather":
                print("PASS: Tool name correctly extracted.")
            else:
                print("FAIL: Tool name extraction failed.")
        else:
            print(f"FAIL: Found {len(insights)} insights, expected 1.")

if __name__ == "__main__":
    asyncio.run(test_parsing())
