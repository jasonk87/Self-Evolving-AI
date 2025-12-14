import asyncio
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import json
from unittest.mock import patch, AsyncMock
from ai_assistant.learning.conversation_analyst import ConversationalAnalyst
from ai_assistant.learning.learning import InsightType

async def verify_bug_insight():
    print("Verifying TOOL_BUG_SUSPECTED support in ConversationalAnalyst...")
    
    # Mock LLM response for a bug
    mock_llm_response = json.dumps({
        "insights": [
            {
                "type": "TOOL_BUG_SUSPECTED",
                "description": "The weather tool failed with a 500 status code.",
                "evidence": "Tool output: 'Error 500'",
                "suggestion": "Check API key configuration."
            }
        ]
    })

    analyst = ConversationalAnalyst()
    
    # Mock the LLM invocation
    with patch('ai_assistant.learning.conversation_analyst.invoke_ollama_model_async', new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = mock_llm_response
        
        # Dummy session data
        session_data = {"history": [{"role": "user", "content": "check weather"}]}
        
        insights = await analyst.analyze_session_transcript(session_data)
        
        if len(insights) == 1:
            insight = insights[0]
            if insight.type == InsightType.TOOL_BUG_SUSPECTED:
                print(f"PASS: Correctly parsed TOOL_BUG_SUSPECTED insight.")
                if "ISSUE DETECTED:" in insight.description:
                     print(f"PASS: Description correctly prefixed with 'ISSUE DETECTED:'.")
                     print(f"Description: {insight.description}")
                else:
                     print(f"FAIL: Description missing prefix. Got: {insight.description}")
            else:
                print(f"FAIL: Parsed insight but wrong type: {insight.type}")
        else:
            print(f"FAIL: Expected 1 insight, got {len(insights)}")

if __name__ == "__main__":
    asyncio.run(verify_bug_insight())
