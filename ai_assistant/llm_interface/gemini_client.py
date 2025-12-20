# ai_assistant/llm_interface/gemini_client.py
import os
import requests
import json
import logging
import aiohttp
import asyncio
import re
from typing import Optional, Dict, Any, List, Tuple
from ai_assistant.utils.display_utils import CLIColors, color_text
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

class GeminiError(Exception):
    """Exception raised for errors in the Gemini API interaction."""
    pass

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
# Global Concurrency Limit (Queue)
# Limits the number of requests actively "talking" to the API at once.
GLOBAL_CONCURRENCY_LIMITER = asyncio.Semaphore(5)

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
) -> str:
    """
    Synchronously invokes the Google Gemini model.
    Raises GeminiError on failure.
    """
    # Wait for rate limit
    GLOBAL_RATE_LIMITER.wait_sync()

    api_key = _get_api_key()
    if not api_key:
        error_msg = "Google API Key not found. Please set GOOGLE_API_KEY in config.py or environment."
        logger.error(error_msg)
        raise GeminiError(error_msg)

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
                reason = candidate['finishReason']
                logger.warning(f"Gemini finished with reason: {reason}")
                raise GeminiError(f"Gemini finished with reason: {reason}")
        
        logger.warning(f"Unexpected response structure from Gemini: {data}")
        raise GeminiError(f"Unexpected response structure from Gemini: {data}")

    except requests.exceptions.RequestException as e:
        logger.error(f"Error invoking Gemini model (sync): {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response body: {e.response.text}")
        raise GeminiError(f"Error invoking Gemini model (sync): {e}")

async def invoke_gemini_model_async(
    prompt: str,
    model_name: str = "gemini-2.0-flash-exp",
    temperature: float = 0.7,
    max_tokens: int = 8192,
    images: Optional[List[str]] = None
) -> str:
    """
    Asynchronously invokes the Google Gemini model.
    Raises GeminiError on failure.
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
        error_msg = "Google API Key not found."
        logger.error(error_msg)
        raise GeminiError(error_msg)

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
        
        attempt = 0
        while True: # "Infinite" retry loop for Rate Limits
            # Acquire Concurrency Slot
            async with GLOBAL_CONCURRENCY_LIMITER:
                try:
                    async with session.post(
                        url, 
                        headers=headers, 
                        json=payload, 
                        params={"key": api_key},
                        timeout=60
                    ) as response:
                        if response.status == 429:
                            attempt += 1
                            wait_time = base_delay * (2 ** min(attempt, 5)) # Cap backoff at ~64s
                            logger.warning(f"Gemini API Rate Limit Hit (Async). Request Queued. Waiting {wait_time}s to retry...")
                            await asyncio.sleep(wait_time)
                            continue # Retry indefinitely for 429s
                        
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
                                reason = candidate['finishReason']
                                logger.warning(f"Gemini finished with reason: {reason}")
                                # Don't crash, just return empty string or specific message so Orchestrator can handle it
                                return f"[SYSTEM: The model finished with reason '{reason}' and generated no text.]"

                        # Handle cases where candidates are missing but usageMetadata exists (often safety related)
                        if "promptFeedback" in data:
                             block_reason = data["promptFeedback"].get("blockReason", "UNKNOWN")
                             logger.warning(f"Gemini Request Blocked. Reason: {block_reason}")
                             return f"[SYSTEM: The request was blocked by the model. Reason: {block_reason}]"
                        
                        # Fallback for just usage metadata or completely empty
                        if "usageMetadata" in data and "candidates" not in data:
                             logger.warning("Gemini returned usage metadata but no candidates (Empty Response).")
                             return "[SYSTEM: The model returned an empty response.]"
                        
                        logger.warning(f"Unexpected response structure from Gemini (async): {data}")
                        raise GeminiError(f"Unexpected response structure from Gemini (async): {data}")

                except aiohttp.ClientError as e:
                    logger.error(f"ClientError in Gemini async call: {e}")
                    attempt += 1
                    if attempt < 5: # Limited retries for network connection errors (not Rate Limits)
                         await asyncio.sleep(base_delay)
                         continue
                    raise GeminiError(f"ClientError in Gemini async call: {e}")
                except Exception as e:
                     logger.error(f"Unexpected error in Gemini async call: {e}")
                     # If it's already a GeminiError, re-raise it
                     if isinstance(e, GeminiError):
                         raise e
                     raise GeminiError(f"Unexpected error in Gemini async call: {e}")
        
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

async def invoke_split_brain_async(
    prompt: str,
    model_name: str = "gemini-2.0-flash-exp",
    temperature: float = 0.7,
    max_tokens: int = 8192,
    images: Optional[List[str]] = None,
    context_text: str = ""
) -> Tuple[str, str]:
    """
    Executes a "Split Brain" decision process:
    1. Thinking Phase: Deep reasoning to analyze the problem.
    2. Execution Phase: Final answer based on the reasoning.
    
    Returns: (final_response, thought_process)
    """
    
    # --- Phase 1: Thinking ---
    if VERBOSE_LLM_LOGGING:
        print(color_text("\n[SPLIT BRAIN] Phase 1: Deep Thinking...", CLIColors.THOUGHT))

    thinking_prompt = f"""You are the PRE-PROCESSOR and STRATEGIST for an AI system.
Your goal is to THINK deeply about the user's request, facts, hidden constraints, and edge cases.

User Request:
{prompt}

Additional Context:
{context_text}

Instructions:
1. Analyze the request. What does the user REALLY want?
2. Recall relevant facts or context.
3. Identify potential pitfalls or ambiguity.
4. Formulate a step-by-step strategy.

Output ONLY your internal monologue/reasoning. Do not output the final answer yet.
"""
    # Use a slightly lower temp for reasoning to be more logical
    raw_thoughts = await invoke_gemini_model_async(
        thinking_prompt, 
        model_name=model_name, 
        temperature=0.7, # Balanced for creativity + logic
        max_tokens=2000,
        images=images
    )
    
    # Clean up thoughts (remove <think> if present, though prompt says output logic)
    thoughts = _extract_and_log_thinking(raw_thoughts)
    
    if VERBOSE_LLM_LOGGING:
        print(color_text(f"\n[SPLIT BRAIN] Thoughts Generated ({len(thoughts)} chars)", CLIColors.THOUGHT))

    # --- Phase 2: Execution ---
    execution_prompt = f"""You are the EXECUTOR.
You have successfully analyzed the problem. Now execute the strategy.

User Request:
{prompt}

Your Strategic Analysis (The "Split Brain" Reference):
{thoughts}

Instructions:
1. Use the analysis above to construct the BEST possible response.
2. If the strategy says to use a tool, format the tool call correctly.
3. If the strategy says to answer, provide the final answer clearly.
4. Do NOT repeat the analysis in the final output unless requested or crucial for explanation.

Additional Context:
{context_text}
"""

    final_response = await invoke_gemini_model_async(
        execution_prompt,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        images=images # Pass images again if needed, or maybe just reliance on thought is enough? 
                      # Ideally executor should see them too for specifics.
    )

    return final_response, thoughts

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
