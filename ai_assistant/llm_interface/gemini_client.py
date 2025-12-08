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

def invoke_gemini_model(
    prompt: str,
    model_name: str = "gemini-2.0-flash-exp",
    temperature: float = 0.7,
    max_tokens: int = 1500
) -> Optional[str]:
    """
    Synchronously invokes the Google Gemini model.
    """
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
            logger.error(f"Error invoking Gemini model (async): {e}")
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
