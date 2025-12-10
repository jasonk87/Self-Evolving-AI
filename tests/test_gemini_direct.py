import asyncio
import os
import sys

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async
from ai_assistant.config import DEFAULT_MODEL

async def test_gemini():
    print(f"Testing Gemini client with model: {DEFAULT_MODEL}")
    try:
        response = await invoke_gemini_model_async("Hello, say 'Gemini Works' if you can hear me.", model_name=DEFAULT_MODEL)
        print(f"Gemini Response: {response}")
    except Exception as e:
        print(f"Gemini call failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_gemini())
