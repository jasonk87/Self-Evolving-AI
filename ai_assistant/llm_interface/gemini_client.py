# ai_assistant/llm_interface/gemini_client.py
import os
import requests
import json
import logging
import aiohttp
import asyncio
import re
import time
from typing import Optional, Dict, Any, List, Tuple
from ai_assistant.utils.display_utils import CLIColors, color_text
# Import config
from ai_assistant.config import (
    GOOGLE_API_KEY, 
    GEMINI_FLASH_LITE_MODEL,
    ENABLE_RATE_LIMITING,
    VERBOSE_LLM_LOGGING,
    DAILY_TOKEN_BUDGET,
    CATEGORY_BUDGETS
)
from ai_assistant.core.telemetry import telemetry_tracker
from ai_assistant.core.rate_limiter import global_llm_rate_limiter
from ai_assistant.llm_interface.exceptions import BudgetExceededError

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

def _check_budget(task: str = "unknown"):
    """Throws BudgetExceededError if hard limits are crossed."""
    if telemetry_tracker.check_hard_limit_exceeded(DAILY_TOKEN_BUDGET):
        raise BudgetExceededError("Daily Token Budget Exceeded.")
    if telemetry_tracker.check_category_limit_exceeded(task, CATEGORY_BUDGETS):
        raise BudgetExceededError(f"Category budget for task '{task}' exceeded.")

class GeminiError(Exception):
    """Exception raised for errors in the Gemini API interaction."""
    pass

def _get_api_key() -> str:
    """Retrieves the Google API Key from config or environment."""
    if GOOGLE_API_KEY:
        return GOOGLE_API_KEY
    return os.environ.get("GOOGLE_API_KEY", "")

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
# Global Concurrency Limit (Queue)
# Limits the number of requests actively "talking" to the API at once.
class ConcurrencyLimiter:
    def __init__(self, limit=5):
        self.limit = limit
        self._semaphores: Dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}

    def get_semaphore(self):
        loop = asyncio.get_running_loop()
        if loop not in self._semaphores:
            self._semaphores[loop] = asyncio.Semaphore(self.limit)
        return self._semaphores[loop]

GLOBAL_CONCURRENCY_LIMITER = ConcurrencyLimiter(limit=5)

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
        # Log cleaner thinking
        if VERBOSE_LLM_LOGGING:
             print(color_text(f"  [Thought Process]: {thinking_content[:200]}...", CLIColors.THOUGHT))
        logger.debug(f"Gemini Thought Process: {thinking_content}")
        # Remove the thinking block from the text
        cleaned_text = re.sub(thinking_pattern, '', text, flags=re.DOTALL).strip()
        return cleaned_text
    
    return text 

# --- RAW INVOKE FUNCTIONS (Internal Use Only) ---

def _invoke_raw_gemini_sync(
    prompt: str,
    model_name: str = GEMINI_FLASH_LITE_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    task_name: str = "unknown"
) -> str:
    """
    Synchronously invokes the Google Gemini model directly.
    """
    model_name = model_name or GEMINI_FLASH_LITE_MODEL
    _check_budget(task_name)
    # Wait for rate limit
    GLOBAL_RATE_LIMITER.wait_sync()

    api_key = _get_api_key()
    if not api_key:
        error_msg = "Google API Key not found. Please set GOOGLE_API_KEY in config.py or environment."
        logger.error(error_msg)
        raise GeminiError(error_msg)

    url = GEMINI_API_URL.format(model=model_name)
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens
        }
    }

    if VERBOSE_LLM_LOGGING:
        print(color_text(f">>> [Gemini Sync] Requesting ({model_name})...", CLIColors.OKBLUE))

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
             time.sleep(5)
             
        response.raise_for_status()
        data = response.json()
        
        if "candidates" in data and len(data["candidates"]) > 0:
            candidate = data["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                 raw_text = candidate["content"]["parts"][0]["text"]
                 if VERBOSE_LLM_LOGGING:
                     print(color_text(f"<<< [Gemini Sync] Response Received ({len(raw_text)} chars)", CLIColors.OKGREEN))
                 telemetry_tracker.track_call(model_name, len(prompt), len(raw_text), task=f"gemini_sync_{task_name}")
                 return _extract_and_log_thinking(raw_text)
            elif "finishReason" in candidate:
                reason = candidate['finishReason']
                logger.warning(f"Gemini finished with reason: {reason}")
                raise GeminiError(f"Gemini finished with reason: {reason}")
        
        logger.warning(f"Unexpected response structure from Gemini: {data}")
        raise GeminiError(f"Unexpected response structure from Gemini: {data}")

    except requests.exceptions.RequestException as e:
        logger.error(f"Error invoking Gemini model (sync): {e}")
        raise GeminiError(f"Error invoking Gemini model (sync): {e}")

async def _invoke_raw_gemini_async(
    prompt: str,
    model_name: str = GEMINI_FLASH_LITE_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    images: Optional[List[str]] = None,
    task_name: str = "unknown"
) -> str:
    """
    Asynchronously invokes the Google Gemini model directly.
    """
    model_name = model_name or GEMINI_FLASH_LITE_MODEL
    _check_budget(task_name)
    if ENABLE_RATE_LIMITING:
        await global_llm_rate_limiter.acquire()

    api_key = _get_api_key()
    if not api_key:
        raise GeminiError("Google API Key not found.")

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
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens
        }
    }

    if VERBOSE_LLM_LOGGING:
        print(color_text(f">>> [Gemini Async] Requesting ({model_name})...", CLIColors.OKBLUE))

    async with aiohttp.ClientSession() as session:
        retries = 3
        base_delay = 2
        attempt = 0
        while True:
            async with GLOBAL_CONCURRENCY_LIMITER.get_semaphore():
                try:
                    async with session.post(
                        url, headers=headers, json=payload, params={"key": api_key}, timeout=60
                    ) as response:
                        if response.status == 429:
                            attempt += 1
                            wait_time = base_delay * (2 ** min(attempt, 5))
                            logger.warning(f"Gemini Rate Limit (Async). Retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        
                        response.raise_for_status()
                        data = await response.json()
                        
                        if "candidates" in data and len(data["candidates"]) > 0:
                            candidate = data["candidates"][0]
                            if "content" in candidate and "parts" in candidate["content"]:
                                raw_text = candidate["content"]["parts"][0]["text"]
                                if VERBOSE_LLM_LOGGING:
                                     print(color_text(f"<<< [Gemini Async] Response Received ({len(raw_text)} chars)", CLIColors.OKGREEN))
                                telemetry_tracker.track_call(model_name, len(prompt), len(raw_text), task=f"gemini_async_{task_name}")
                                return _extract_and_log_thinking(raw_text)
                            elif "finishReason" in candidate:
                                reason = candidate['finishReason']
                                return f"[SYSTEM: The model finished with reason '{reason}' and generated no text.]"

                        if "promptFeedback" in data:
                             block_reason = data["promptFeedback"].get("blockReason", "UNKNOWN")
                             return f"[SYSTEM: The request was blocked by the model. Reason: {block_reason}]"
                        
                        if "usageMetadata" in data and "candidates" not in data:
                             return "[SYSTEM: The model returned an empty response.]"
                        
                        raise GeminiError(f"Unexpected response structure from Gemini (async): {data}")

                except aiohttp.ClientError as e:
                    attempt += 1
                    if attempt < 5:
                         await asyncio.sleep(base_delay)
                         continue
                    raise GeminiError(f"ClientError in Gemini async call: {e}")
                except Exception as e:
                     if isinstance(e, GeminiError): raise e
                     raise GeminiError(f"Unexpected error in Gemini async call: {e}")
        
        await asyncio.sleep(0.250)

# --- PUBLIC WRAPPERS ---

def invoke_raw_gemini_sync(prompt: str, model_name: str = GEMINI_FLASH_LITE_MODEL, temperature: float = 0.7, max_tokens: int = 8192, task_name: str = "unknown") -> str:
    """Public wrapper for raw sync invocation."""
    return _invoke_raw_gemini_sync(prompt, model_name, temperature, max_tokens, task_name=task_name)

async def invoke_raw_gemini_async(prompt: str, model_name: str = GEMINI_FLASH_LITE_MODEL, temperature: float = 0.7, max_tokens: int = 8192, images: Optional[List[str]] = None, task_name: str = "unknown") -> str:
    """Public wrapper for raw async invocation."""
    return await _invoke_raw_gemini_async(prompt, model_name, temperature, max_tokens, images, task_name=task_name)

def invoke_gemini_model(
    prompt: str,
    model_name: str = GEMINI_FLASH_LITE_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    strategy: str = "RAW",
    task_name: str = "unknown"
) -> str:
    """
    Synchronously invokes Gemini with a single direct model call.
    """
    model_name = model_name or GEMINI_FLASH_LITE_MODEL

    return _invoke_raw_gemini_sync(prompt, model_name, temperature, max_tokens, task_name=task_name)

async def invoke_gemini_model_async(
    prompt: str,
    model_name: str = GEMINI_FLASH_LITE_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    images: Optional[List[str]] = None,
    strategy: str = "RAW",
    task_name: str = "unknown"
) -> str:
    """
    Asynchronously invokes Gemini with a single direct model call.
    """
    model_name = model_name or GEMINI_FLASH_LITE_MODEL

    return await _invoke_raw_gemini_async(prompt, model_name, temperature, max_tokens, images, task_name=task_name)

# --- EMBEDDINGS ---

async def get_embeddings_async(text: str, model_name: str = "gemini-embedding-001") -> Optional[List[float]]:
    """
    Generates embeddings for the given text using Google Gemini Embedding API.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error("Gemini API key is missing. Cannot generate embeddings.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:embedContent"
    headers = {"Content-Type": "application/json"}
    payload = {"model": f"models/{model_name}", "content": {"parts": [{"text": text}]}}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url, headers=headers, json=payload, params={"key": api_key}, timeout=30
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Gemini Embedding API Error ({response.status}): {error_text}")
                    return None
                data = await response.json()
                if "embedding" in data and "values" in data["embedding"]:
                    return data["embedding"]["values"]
                logger.error(f"Unexpected embedding response format: {data}")
                return None
        except Exception as e:
            logger.error(f"Error getting embeddings from Gemini: {e}")
            return None
        await asyncio.sleep(0.250)

if __name__ == "__main__":
    print("Testing Gemini Client...")
    # Async Test
    async def run_async_test():
        resp = await invoke_gemini_model_async("Tell me a quick joke.", model_name=GEMINI_FLASH_LITE_MODEL)
        print(f"Response: {resp}")
    asyncio.run(run_async_test())
