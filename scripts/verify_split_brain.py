
import asyncio
import sys
import os

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ai_assistant.llm_interface.gemini_client import invoke_split_brain_async
from ai_assistant.utils.display_utils import CLIColors, color_text

async def test_split_brain():
    print(color_text("Testing Split Brain...", CLIColors.SYSTEM_MESSAGE))
    
    prompt = "What is the best way to implement a singleton in Python? Give me one solid example."
    
    try:
        response, thoughts = await invoke_split_brain_async(
            prompt,
            context_text="Verification Test"
        )
        
        print(color_text("\n--- THOUGHTS ---", CLIColors.THOUGHT))
        print(thoughts)
        
        print(color_text("\n--- RESPONSE ---", CLIColors.TOOL_OUTPUT))
        print(response)
        
        if thoughts and response:
            print(color_text("\n✅ Split Brain Verification PASSED", CLIColors.SUCCESS))
        else:
            print(color_text("\n❌ Split Brain Verification FAILED (Empty output)", CLIColors.ERROR))
            
    except Exception as e:
        print(color_text(f"\n❌ Split Brain Verification ERROR: {e}", CLIColors.ERROR))

if __name__ == "__main__":
    asyncio.run(test_split_brain())
