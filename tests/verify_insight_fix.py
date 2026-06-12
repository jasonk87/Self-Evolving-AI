
import asyncio
import sys
import os
import json
from unittest.mock import AsyncMock, patch

# Add project root to sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ai_assistant.learning.learning import InsightType
from ai_assistant.learning.conversation_analyst import ConversationalAnalyst

async def verify_fix():
    print("Verifying USER_FRUSTRATION in InsightType Enum...")
    if hasattr(InsightType, 'USER_FRUSTRATION'):
        print("PASS: USER_FRUSTRATION found in InsightType.")
    else:
        print("FAIL: USER_FRUSTRATION not found in InsightType!")
        return

    print("\nVerifying ConversationalAnalyst parsing...")
    
    # Mock LLM response
    mock_llm_response = json.dumps({
        "insights": [
            {
                "type": "USER_FRUSTRATION",
                "description": "User is frustrated with repeated questions.",
                "evidence": "User said 'stop asking me'",
                "suggestion": "Be more proactive."
            }
        ]
    })

    analyst = ConversationalAnalyst()
    
    # Mock the LLM invocation
    with patch('ai_assistant.learning.conversation_analyst.invoke_ollama_model_async', new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = mock_llm_response
        
        # Test data
        session_data = {"history": [{"role": "user", "content": "test"}]}
        
        insights = await analyst.analyze_session_transcript(session_data)
        
        if len(insights) == 1:
            insight = insights[0]
            if insight.type == InsightType.USER_FRUSTRATION:
                print(f"PASS: Correctly parsed USER_FRUSTRATION insight. Type: {insight.type}")
            else:
                print(f"FAIL: Parsed insight but wrong type: {insight.type}")
        else:
            print(f"FAIL: Expected 1 insight, got {len(insights)}")

if __name__ == "__main__":
    asyncio.run(verify_fix())
