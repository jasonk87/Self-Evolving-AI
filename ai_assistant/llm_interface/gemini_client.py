# ai_assistant/llm_interface/gemini_client.py
import os
import requests
import json
import logging
import aiohttp
import asyncio
import re
from typing import Optional, Dict, Any, List
# Import config
from ai_assistant.config import (
    GOOGLE_API_KEY, 
    ENABLE_RATE_LIMITING,
    ENABLE_THINKING,
    THINKING_SUPPORTED_MODELS,
    VERBOSE_LLM_LOGGING
)

THINKING_SYSTEM_INSTRUCTION = "You are a deep thinking AI. You MUST first think through the Logic, Edge cases, and Plan in a <think> block before answering. <think> ... </think>"

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

def _get_api_key() -> str:
    """Retrieves the Google API Key from config or environment."""
    if GOOGLE_API_KEY:
        return GOOGLE_API_KEY
    return os.environ.get("GOOGLE_API_KEY", "")

import time

class RateLimiter:
    def __init__(self, rpm=15):
        self.interval = 60.0 / rpm
        self.last_call = 0
        # lazy init for async locks to handle multiple event loops (e.g. threads)
        self._locks: Dict[asyncio.AbstractEventLoop, asyncio.Lock] = {} 
        self._sync_lock = asyncio.Lock() # Not strictly thread-safe for sync across threads but okay for this context

    async def wait_async(self):
        if not ENABLE_RATE_LIMITING:
            return

        loop = asyncio.get_running_loop()
        if loop not in self._locks:
            self._locks[loop] = asyncio.Lock()
            
        async with self._locks[loop]:
            now = time.time()
            wait_time = self.last_call + self.interval - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self.last_call = time.time()

    def wait_sync(self):
        if not ENABLE_RATE_LIMITING:
            return

        # detailed sync locking is complex, simple blocking sleep is safer for single-threaded or low-concurrency sync usage
        now = time.time()
        wait_time = self.last_call + self.interval - now
        if wait_time > 0:
            time.sleep(wait_time)
        self.last_call = time.time()

# Global Rate Limiter
# Setting conservative limit to avoid 429s (Gemini Free Tier is ~15 RPM)
GLOBAL_RATE_LIMITER = RateLimiter(rpm=10) 

def _extract_and_log_thinking(text: str) -> str:
    """
    Extracts content within <think> tags, logs it, and returns the simplified text.
    """
    if not text:
        return text
        
    thinking_pattern = r'<think>(.*?)</think>'
    match = re.search(thinking_pattern, text, re.DOTALL)
    
    if match:
        thinking_content = match.group(1).strip()
        logger.info(f"Gemini Thought Process: {thinking_content}")
        # Remove the thinking block from the text
        cleaned_text = re.sub(thinking_pattern, '', text, flags=re.DOTALL).strip()
        return cleaned_text
    
    return text 

def invoke_gemini_model(
    prompt: str,
    model_name: str = "gemini-2.0-flash-exp",
    temperature: float = 0.7,
    max_tokens: int = 8192
) -> Optional[str]:
    """
    Synchronously invokes the Google Gemini model.
    """
    # Wait for rate limit
    GLOBAL_RATE_LIMITER.wait_sync()

    api_key = _get_api_key()
    if not api_key:
        logger.error("Google API Key not found. Please set GOOGLE_API_KEY in config.py or environment.")
        return None

    url = GEMINI_API_URL.format(model=model_name)
    headers = {"Content-Type": "application/json"}
    
    # Construct the request payload
    # Note: Gemini 2.0 Flash generation config might differ slightly, but this is standard v1beta
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens
        }
    }

    # Inject system instruction for thinking models if enabled
    if ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS:
        payload["system_instruction"] = {
            "parts": [{"text": THINKING_SYSTEM_INSTRUCTION}]
        }

    if VERBOSE_LLM_LOGGING:
        print(f"\n{'-'*60}")
        print(f" [GEMINI SYNC REQUEST] Model: {model_name}")
        print(f"{'-'*60}")
        print(f"PROMPT:\n{prompt}")
        print(f"{'-'*60}\n")

    try:
        response = requests.post(
            url, 
            headers=headers, 
            json=payload, 
            params={"key": api_key},
            timeout=60
        )
        if response.status_code == 429:
             logger.warning("Gemini API Rate Limit Hit (Sync). Backing off...")
             time.sleep(5) # Extra backoff
             # build simple retry? For now just fail gracefully or let caller handle, but logging is key.
             
        response.raise_for_status()
        data = response.json()
        
        # Extract text from response
        # Structure: candidates[0].content.parts[0].text
        if "candidates" in data and len(data["candidates"]) > 0:
            candidate = data["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                 raw_text = candidate["content"]["parts"][0]["text"]
                 
                 if VERBOSE_LLM_LOGGING:
                     print(f"\n{'-'*60}")
                     print(f" [GEMINI SYNC RESPONSE] Model: {model_name}")
                     print(f"{'-'*60}")
                     print(f"RESPONSE:\n{raw_text}")
                     print(f"{'-'*60}\n")
                     
                 return _extract_and_log_thinking(raw_text)
            elif "finishReason" in candidate:
                logger.warning(f"Gemini finished with reason: {candidate['finishReason']}")
                return None
        
        logger.warning(f"Unexpected response structure from Gemini: {data}")
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error invoking Gemini model (sync): {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response body: {e.response.text}")
        return None

async def invoke_gemini_model_async(
    prompt: str,
    model_name: str = "gemini-2.0-flash-exp",
    temperature: float = 0.7,
    max_tokens: int = 8192,
    images: Optional[List[str]] = None
) -> Optional[str]:
    """
    Asynchronously invokes the Google Gemini model.
    Args:
        prompt: The text prompt.
        model_name: Model name.
        temperature: Sampling temperature.
        max_tokens: Max output tokens.
        images: Optional list of base64 encoded strings for multimodal input.
    """
    # Wait for rate limit
    await GLOBAL_RATE_LIMITER.wait_async()

    api_key = _get_api_key()
    if not api_key:
        logger.error("Google API Key not found.")
        return None

    url = GEMINI_API_URL.format(model=model_name)
    headers = {"Content-Type": "application/json"}
    
    parts = [{"text": prompt}]

    if images:
        for b64_img in images:
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": b64_img
                }
            })

    payload = {
        "contents": [{
            "parts": parts
        }],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens
        }
    }

    # Inject system instruction for thinking models if enabled
    if ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS:
        payload["system_instruction"] = {
            "parts": [{"text": THINKING_SYSTEM_INSTRUCTION}]
        }

    if VERBOSE_LLM_LOGGING:
        print(f"\n{'-'*60}")
        print(f" [GEMINI ASYNC REQUEST] Model: {model_name}")
        print(f"{'-'*60}")
        print(f"PROMPT:\n{prompt}")
        print(f"{'-'*60}\n")

    async with aiohttp.ClientSession() as session:
        retries = 3
        base_delay = 2
        
        for attempt in range(retries + 1):
            try:
                async with session.post(
                    url, 
                    headers=headers, 
                    json=payload, 
                    params={"key": api_key},
                    timeout=60
                ) as response:
                    if response.status == 429:
                         logger.warning(f"Gemini API Rate Limit Hit (Async). Attempt {attempt+1}/{retries+1}. Backing off...")
                         if attempt < retries:
                             await asyncio.sleep(base_delay * (2 ** attempt)) # Exponential backoff
                             continue
                         else:
                             # Retries exhausted
                             logger.error("Gemini API Rate Limit Retries Exhausted.")
                             return None
                    
                    response.raise_for_status()
                    data = await response.json()
                    
                    if "candidates" in data and len(data["candidates"]) > 0:
                        candidate = data["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                             raw_text = candidate["content"]["parts"][0]["text"]
                             
                             if VERBOSE_LLM_LOGGING:
                                 print(f"\n{'-'*60}")
                                 print(f" [GEMINI ASYNC RESPONSE] Model: {model_name}")
                                 print(f"{'-'*60}")
                                 print(f"RESPONSE:\n{raw_text}")
                                 print(f"{'-'*60}\n")
                                 
                             return _extract_and_log_thinking(raw_text)
                        elif "finishReason" in candidate:
                            logger.warning(f"Gemini finished with reason: {candidate['finishReason']}")
                            return None
                    
                    logger.warning(f"Unexpected response structure from Gemini (async): {data}")
                    return None

            except aiohttp.ClientError as e:
                logger.error(f"ClientError in Gemini async call: {e}")
                if attempt < retries:
                     await asyncio.sleep(base_delay)
                     continue
                return None
            except Exception as e:
                 logger.error(f"Unexpected error in Gemini async call: {e}")
                 return None
        
        # Windows/ProactorEventLoop workaround: Give time for SSL transport to close
        await asyncio.sleep(0.250)

async def get_embeddings_async(text: str, model_name: str = "text-embedding-004") -> Optional[List[float]]:
    """
    Generates embeddings for the given text using Google Gemini Embedding API.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error("Google API Key not found for embeddings.")
        return None

    # URL for embedding
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:embedContent"
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "content": {
            "parts": [{"text": text}]
        }
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url, 
                headers=headers, 
                json=payload, 
                params={"key": api_key},
                timeout=30
            ) as response:
                if response.status != 200:
                    logger.error(f"Gemini Embedding API Error: {response.status}")
                    txt = await response.text()
                    logger.error(f"Response: {txt}")
                    return None
                
                data = await response.json()
                # Expected: {"embedding": {"values": [...]}}
                if "embedding" in data and "values" in data["embedding"]:
                    return data["embedding"]["values"]
                
                logger.error(f"Unexpected response structure from Gemini Embeddings: {data}")
                return None

        except Exception as e:
            logger.error(f"Error getting embeddings from Gemini: {e}")
            return None

        # Windows/ProactorEventLoop workaround
        await asyncio.sleep(0.250)

MERGER_PROMPT_TEMPLATE = """You are a "Judge" and "Merger" AI. You have been provided with {num_branches} independent thought paths and solutions to a problem.

Original Prompt:
{original_prompt}

---
{branches_text}
---

Your Task:
1. Synthesize the best possible final answer by combining the strongest elements from all branches.
2. CRITICAL: Do NOT list, summarize, or mention the existence of the "branches". The user should NOT know multiple paths were explored.
3. Present the result as a single, authoritative, and cohesive response.
4. FORMATTING RULES:
   - If the original prompt requested a specific output format (e.g., JSON, Python code, SQL), output ONLY that format (and the explanation if requested).
   - If the branches generated tool calls (JSON), output the single BEST tool call.
   - Do NOT output "Branch 1 said X, Branch 2 said Y". Just say "The answer is Z".

Start with a brief <thinking> block explaining your synthesis decision, then provide the Final Answer.
"""

async def invoke_parallel_thinking(
    prompt: str,
    model_name: str = "gemini-2.0-flash-exp",
    temperature: float = 0.7,
    max_tokens: int = 1500,
    num_branches: int = 3,
    merge_model: Optional[str] = None,
    temperature_merge: Optional[float] = None
) -> Optional[str]:
    """
    Executes 'Parallel Thinking' by invoking the model multiple times concurrently
    and then merging the results using a 'Judge/Merger' call.
    """
    if VERBOSE_LLM_LOGGING:
        print(f"\n{'-'*60}")
        print(f" [PARALLEL THINKING STARTED] Branches: {num_branches}")
        print(f"{'-'*60}\n")

    # 1. Branching: Asynchronously fire separate calls
    tasks = []
    for i in range(num_branches):
        # Use the branch temperature
        tasks.append(invoke_gemini_model_async(
            prompt,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens
        ))

    # Wait for all branches to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid_responses = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.error(f"Parallel Branch {i+1} failed: {res}")
        elif res:
            valid_responses.append(f"Branch {i+1} Output:\n{res}\n")
        else:
            logger.warning(f"Parallel Branch {i+1} returned None.")

    if not valid_responses:
        logger.error("All parallel branches failed.")
        return None

    # 2. Merging
    branches_text = "\n---\n".join(valid_responses)
    merger_prompt = MERGER_PROMPT_TEMPLATE.format(
        num_branches=len(valid_responses),
        original_prompt=prompt,
        branches_text=branches_text
    )

    if VERBOSE_LLM_LOGGING:
        print(f"\n{'-'*60}")
        print(f" [PARALLEL THINKING MERGE STEP]")
        print(f"{'-'*60}\n")

    # Recursive call to standard invoke for the merge step
    # The merger acts as the final judge.
    final_response = await invoke_gemini_model_async(
        merger_prompt,
        model_name=merge_model or model_name,
        temperature=temperature_merge if temperature_merge is not None else 0.2, # Lower temp for merge/judge
        max_tokens=max_tokens
    )

    return final_response

if __name__ == "__main__":
    # Test block
    print("Testing Gemini Client...")
    
    # Sync Test
    print("\n--- Sync Test ---")
    response = invoke_gemini_model("Hello, how are you?", model_name="gemini-2.0-flash-exp")
    print(f"Response: {response}")

    # Async Test
    print("\n--- Async Test ---")
    async def run_async_test():
        resp = await invoke_gemini_model_async("Tell me a quick joke.", model_name="gemini-2.0-flash-exp")
        print(f"Response: {resp}")
    
    asyncio.run(run_async_test())
