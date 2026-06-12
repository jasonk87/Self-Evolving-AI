
import asyncio
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async, GLOBAL_CONCURRENCY_LIMITER
from ai_assistant.utils.display_utils import CLIColors, color_text

async def spam_requests(num_requests=10):
    print(color_text(f"Starting Stress Test with {num_requests} requests...", CLIColors.SYSTEM_MESSAGE))
    print(f"Global Concurrency Limiter: {GLOBAL_CONCURRENCY_LIMITER}")
    
    tasks = []
    
    async def worker(i):
        try:
            print(f"Task {i} starting...")
            # We use a short prompt
            resp = await invoke_gemini_model_async(f"Say number {i}", temperature=0.7, max_tokens=10)
            print(f"Task {i} COMPLETED. Response length: {len(resp)}")
            return True
        except Exception as e:
            print(f"Task {i} FAILED: {e}")
            return False

    for i in range(num_requests):
        tasks.append(worker(i))
    
    results = await asyncio.gather(*tasks)
    success_count = sum(results)
    print(color_text(f"\nStress Test Complete. Success: {success_count}/{num_requests}", CLIColors.SUCCESS))

if __name__ == "__main__":
    try:
        asyncio.run(spam_requests(15))
    except KeyboardInterrupt:
        print("Stress test interrupted.")
