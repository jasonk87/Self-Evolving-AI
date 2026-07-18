import os
import logging
import aiohttp
import asyncio
import json
import os
import time
from typing import Optional, Dict, Any, List
from ai_assistant.utils.display_utils import CLIColors, color_text
from ai_assistant.config import DEEPSEEK_API_KEY, VERBOSE_LLM_LOGGING
from ai_assistant.core.telemetry import telemetry_tracker

logger = logging.getLogger(__name__)

DEEPSEEK_API_URL = os.environ.get(
    "DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions"
)
DEEPSEEK_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("DEEPSEEK_REQUEST_TIMEOUT_SECONDS", "60"))
DEEPSEEK_MAX_ATTEMPTS = 3

class DeepseekError(Exception):
    pass

def _get_api_key() -> str:
    if DEEPSEEK_API_KEY:
        return DEEPSEEK_API_KEY
    return os.environ.get("DEEPSEEK_API_KEY", "")

async def invoke_raw_deepseek_async(
    messages: List[Dict[str, str]],
    model_name: str = "deepseek-chat",
    temperature: float = 0.7,
    max_tokens: int = 8192,
    task_name: str = "unknown",
    json_mode: bool = False,
) -> str:
    api_key = _get_api_key()
    if not api_key or api_key == "your_deepseek_api_key_here":
        raise DeepseekError("Deepseek API Key not found.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    # Thinking consumes output tokens before the final answer. Avoid allowing
    # callers to request a budget too small to contain both reasoning and output.
    effective_max_tokens = max(max_tokens, 512)
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": effective_max_tokens,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high"
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    if VERBOSE_LLM_LOGGING:
        print(color_text(f">>> [Deepseek Async] Requesting ({model_name})...", CLIColors.OKBLUE))

    async with aiohttp.ClientSession() as session:
        attempt = 0
        while attempt < DEEPSEEK_MAX_ATTEMPTS:
            try:
                async with session.post(
                    DEEPSEEK_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=DEEPSEEK_REQUEST_TIMEOUT_SECONDS,
                ) as response:
                    if response.status in (429, 500, 502, 503, 504):
                        attempt += 1
                        if attempt >= DEEPSEEK_MAX_ATTEMPTS:
                            response.raise_for_status()
                        await asyncio.sleep(2 ** attempt)
                        continue
                    
                    response.raise_for_status()
                    data = await response.json()
                    
                    if "choices" in data and len(data["choices"]) > 0:
                        choice = data["choices"][0]
                        message = choice.get("message", {})
                        
                        content = message.get("content", "")
                        reasoning = message.get("reasoning_content", "")
                        
                        if reasoning and VERBOSE_LLM_LOGGING:
                            print(color_text(f"  [Deepseek Thought Process]: {reasoning[:200]}...", CLIColors.THOUGHT))
                            logger.debug(f"Deepseek Thought Process: {reasoning}")
                            
                        if VERBOSE_LLM_LOGGING:
                            print(color_text(f"<<< [Deepseek Async] Response Received ({len(content)} chars)", CLIColors.OKGREEN))
                        
                        prompt_len = sum(len(m.get("content", "")) for m in messages)
                        telemetry_tracker.track_call(
                            model_name,
                            prompt_len,
                            len(content),
                            task=f"deepseek_async_{task_name}"
                        )
                        return content
                    
                    raise DeepseekError(f"Unexpected response from Deepseek: {data}")

            except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
                attempt += 1
                if attempt >= DEEPSEEK_MAX_ATTEMPTS:
                    raise DeepseekError(
                        "Deepseek request timed out after "
                        f"{DEEPSEEK_REQUEST_TIMEOUT_SECONDS:g} seconds "
                        f"({DEEPSEEK_MAX_ATTEMPTS} attempts)."
                    ) from e
                await asyncio.sleep(2 ** attempt)
            except aiohttp.ClientError as e:
                attempt += 1
                detail = str(e).strip() or type(e).__name__
                if attempt >= DEEPSEEK_MAX_ATTEMPTS:
                    raise DeepseekError(
                        f"Deepseek client error after {DEEPSEEK_MAX_ATTEMPTS} attempts: {detail}"
                    ) from e
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                if isinstance(e, DeepseekError): raise e
                detail = str(e).strip() or type(e).__name__
                raise DeepseekError(
                    f"Unexpected {type(e).__name__} in Deepseek async call: {detail}"
                ) from e
        
        return ""
