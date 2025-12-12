# ai_assistant/llm_interface/gemini_client.py
import os
import requests
import json
import logging
import aiohttp
import asyncio
from typing import Optional, Dict, Any, List

from ai_assistant.config import GOOGLE_API_KEY

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
        self._lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock() # Not strictly thread-safe for sync across threads but okay for this context

    async def wait_async(self):
        async with self._lock:
            now = time.time()
            wait_time = self.last_call + self.interval - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self.last_call = time.time()

    def wait_sync(self):
        # detailed sync locking is complex, simple blocking sleep is safer for single-threaded or low-concurrency sync usage
        now = time.time()
        wait_time = self.last_call + self.interval - now
        if wait_time > 0:
            time.sleep(wait_time)
        self.last_call = time.time()

# Global Rate Limiter
# Setting conservative limit to avoid 429s (Gemini Free Tier is ~15 RPM)
GLOBAL_RATE_LIMITER = RateLimiter(rpm=10) 

def invoke_gemini_model(
    prompt: str,
    model_name: str = "gemini-2.0-flash-exp",
    temperature: float = 0.7,
    max_tokens: int = 1500
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
                 return candidate["content"]["parts"][0]["text"]
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
    max_tokens: int = 1500
) -> Optional[str]:
    """
    Asynchronously invokes the Google Gemini model.
    """
    # Wait for rate limit
    await GLOBAL_RATE_LIMITER.wait_async()

    api_key = _get_api_key()
    if not api_key:
        logger.error("Google API Key not found.")
        return None

    url = GEMINI_API_URL.format(model=model_name)
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens
        }
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url, 
                headers=headers, 
                json=payload, 
                params={"key": api_key},
                timeout=60
            ) as response:
                if response.status == 429:
                     logger.warning("Gemini API Rate Limit Hit (Async). Backing off...")
                     await asyncio.sleep(5) # Extra backoff
                
                response.raise_for_status()
                data = await response.json()
                
                if "candidates" in data and len(data["candidates"]) > 0:
                    candidate = data["candidates"][0]
                    if "content" in candidate and "parts" in candidate["content"]:
                         return candidate["content"]["parts"][0]["text"]
                    elif "finishReason" in candidate:
                        logger.warning(f"Gemini finished with reason: {candidate['finishReason']}")
                        return None
                
                logger.warning(f"Unexpected response structure from Gemini (async): {data}")
                return None

        except aiohttp.ClientError as e:
            return None
        except Exception as e:
             logger.error(f"Unexpected error in Gemini async call: {e}")
             return None

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
