# ai_assistant/llm_interface/ollama_client.py
import requests
import json
from typing import Optional, Dict, Tuple, Any, List
import asyncio
import aiohttp
import os # Added import os

from ai_assistant.config import (
    DEFAULT_MODEL as CFG_DEFAULT_MODEL,
    is_debug_mode,
    LLM_PROVIDER,
    VERBOSE_LLM_LOGGING,
    DAILY_TOKEN_BUDGET,
    CATEGORY_BUDGETS
)
from ai_assistant.config import GEMINI_EMBEDDING_MODEL
from ai_assistant.debugging.resilience import retry_with_backoff
import ai_assistant.llm_interface.gemini_client as gemini_client
import ai_assistant.llm_interface.deepseek_client as deepseek_client
from ai_assistant.core.telemetry import telemetry_tracker
from ai_assistant.llm_interface.exceptions import BudgetExceededError

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_API_ENDPOINT = f"{OLLAMA_HOST}/api/generate"
OLLAMA_CHAT_API_ENDPOINT = f"{OLLAMA_HOST}/api/chat"
DEFAULT_OLLAMA_MODEL = CFG_DEFAULT_MODEL

def _is_deepseek_model(model_name: Optional[str]) -> bool:
    return str(model_name or "").lower().startswith("deepseek-")

def process_llm_response(response_data: Dict) -> Optional[Tuple[str, Optional[str]]]:
    if not response_data:
        return None
        
    thinking = None
    content = None
    
    if "message" in response_data:
        message = response_data["message"]
        if isinstance(message, dict):
            thinking = message.get("thinking")
            content = message.get("content")
    
    if content is None:
        content = response_data.get("response", "").strip()

    if not content:
        return None
        
    return (content, thinking)

def _check_budget(task: str = "unknown"):
    """Throws BudgetExceededError if hard limits are crossed."""
    if telemetry_tracker.check_hard_limit_exceeded(DAILY_TOKEN_BUDGET):
        raise BudgetExceededError("Daily Token Budget Exceeded.")
    if telemetry_tracker.check_category_limit_exceeded(task, CATEGORY_BUDGETS):
        raise BudgetExceededError(f"Category budget for task '{task}' exceeded.")

@retry_with_backoff(retries=3, base_delay=1.0, max_delay=10.0, jitter=True)
def invoke_ollama_model(
    prompt: str,
    model_name: str = DEFAULT_OLLAMA_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    task_name: Optional[str] = None,
    json_mode: bool = False
) -> Optional[str]:
    _check_budget(task_name or "unknown")

    if _is_deepseek_model(model_name):
        api_key = deepseek_client._get_api_key()
        if not api_key:
            print("DeepSeek API key is not configured.")
            return None
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max(max_tokens, 512),
            "temperature": temperature,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        }
        try:
            response = requests.post(
                deepseek_client.DEEPSEEK_API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            choices = response.json().get("choices", [])
            return choices[0].get("message", {}).get("content", "") if choices else None
        except requests.RequestException as exc:
            print(f"Error invoking DeepSeek model '{model_name}': {exc}")
            return None

    if LLM_PROVIDER == "gemini":
        return gemini_client.invoke_gemini_model(
            prompt,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            strategy="RAW",
            task_name=task_name or "unknown",
            json_mode=json_mode
        )

    use_chat_api = False

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}] if use_chat_api else None,
        "prompt": "" if use_chat_api else prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens}
    }
    if json_mode:
        payload["format"] = "json"
    api_endpoint = OLLAMA_CHAT_API_ENDPOINT if use_chat_api else OLLAMA_API_ENDPOINT

    if VERBOSE_LLM_LOGGING:
        print(f"\n{'-'*60}")
        print(f" [OLLAMA SYNC REQUEST] Model: {model_name}")
        print(f"{'-'*60}")
        print(f"PROMPT:\n{prompt}")
        print(f"{'-'*60}\n")

    try:
        if is_debug_mode() and not VERBOSE_LLM_LOGGING:
            print(f"[DEBUG] Sending request to Ollama with model: {model_name}, prompt: '{prompt[:100]}...'")
        elif not VERBOSE_LLM_LOGGING:
             print(f"Sending request to Ollama with model: {model_name}, prompt: '{prompt[:50]}...'")
             
        response = requests.post(api_endpoint, json=payload, timeout=600)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error occurred: {e}")
        if is_debug_mode() and e.response is not None:
            print(f"[DEBUG] Status code: {e.response.status_code}")
            try: print(f"[DEBUG] Response body: {e.response.json()}")
            except Exception: print("[DEBUG] Response body could not be parsed as JSON.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error invoking Ollama model '{model_name}': {e}")
        print("Please ensure the Ollama service is running and accessible.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during the request: {e}")
        return None

    try:
        parsed_response = response.json()
        result = process_llm_response(parsed_response)
        if not result: return None
        content, thinking = result
        
        if VERBOSE_LLM_LOGGING:
             print(f"\n{'-'*60}")
             print(f" [OLLAMA SYNC RESPONSE] Model: {model_name}")
             print(f"{'-'*60}")
             if thinking: print(f"THINKING:\n{thinking}\n{'-'*30}")
             print(f"CONTENT:\n{content}")
             print(f"{'-'*60}\n")

        if is_debug_mode(): print(f"[DEBUG] Final content being returned: {content[:200]}...")
        telemetry_tracker.track_call(model_name, len(prompt), len(content), task=f"ollama_sync_{task_name or 'unknown'}")
        return content
    except json.JSONDecodeError:
        print("Error: Failed to parse JSON response from Ollama.")
        print(f"Raw response text: {response.text}")
        return None

async def invoke_ollama_model_async_internal(
    prompt: str,
    model_name: str = DEFAULT_OLLAMA_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    api_endpoint_override: Optional[str] = None,
    task_name: Optional[str] = None,
    json_mode: bool = False
) -> Optional[str]:
    _check_budget(task_name or "unknown")

    if _is_deepseek_model(model_name):
        return await deepseek_client.invoke_raw_deepseek_async(
            messages=[{"role": "user", "content": prompt}],
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            task_name=task_name or "unknown",
            json_mode=json_mode,
        )

    if LLM_PROVIDER == "gemini":
        return await gemini_client.invoke_gemini_model_async(
            prompt,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            strategy="RAW",
            task_name=task_name or "unknown",
            json_mode=json_mode
        )

    use_chat_api = False

    current_api_endpoint = api_endpoint_override if api_endpoint_override else OLLAMA_API_ENDPOINT
    if use_chat_api and not api_endpoint_override:
        current_api_endpoint = OLLAMA_CHAT_API_ENDPOINT
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}] if use_chat_api else None,
        "prompt": "" if use_chat_api else prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens}
    }
    if json_mode:
        payload["format"] = "json"
    current_api_endpoint = api_endpoint_override if api_endpoint_override else (OLLAMA_CHAT_API_ENDPOINT if use_chat_api else OLLAMA_API_ENDPOINT)

    if VERBOSE_LLM_LOGGING:
        print(f"\n{'-'*60}")
        print(f" [OLLAMA ASYNC REQUEST] Model: {model_name}")
        print(f"{'-'*60}")
        print(f"PROMPT:\n{prompt}")
        print(f"{'-'*60}\n")

    if is_debug_mode() and not VERBOSE_LLM_LOGGING:
        print(f"[DEBUG] Sending async request to Ollama with model: {model_name}, prompt: '{prompt[:100]}...' to {current_api_endpoint}")
    elif not VERBOSE_LLM_LOGGING:
         print(f"Sending async request to Ollama with model: {model_name}, prompt: '{prompt[:50]}...'")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600.0)) as session:
        try:
            async with session.post(current_api_endpoint, json=payload) as response:
                response.raise_for_status()
                response_data = await response.json()
                if is_debug_mode() and not VERBOSE_LLM_LOGGING: print(f"[DEBUG] Ollama async response JSON: {str(response_data)[:500]}")
                
                result = process_llm_response(response_data)
                if not result: return None
                content, thinking = result
                
                if VERBOSE_LLM_LOGGING:
                     print(f"\n{'-'*60}")
                     print(f" [OLLAMA ASYNC RESPONSE] Model: {model_name}")
                     print(f"{'-'*60}")
                     if thinking: print(f"THINKING:\n{thinking}\n{'-'*30}")
                     print(f"CONTENT:\n{content}")
                     print(f"{'-'*60}\n")

                if is_debug_mode() and not VERBOSE_LLM_LOGGING: print(f"[DEBUG] Async final content being returned: {content[:200]}...")
                telemetry_tracker.track_call(model_name, len(prompt), len(content), task=f"ollama_async_{task_name or 'unknown'}")
                return content
        except aiohttp.ClientError as e: print(f"HTTP error occurred in async call: {e}"); return None
        except json.JSONDecodeError: print("Error: Failed to parse JSON response from Ollama (async)."); return None
        except Exception as e: print(f"An unexpected error occurred during the async request: {e}"); return None

        # Windows/ProactorEventLoop workaround
        await asyncio.sleep(0.250)

async def invoke_ollama_model_async(
    prompt: str,
    model_name: str = DEFAULT_OLLAMA_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    api_endpoint_override: Optional[str] = None,
    task_name: Optional[str] = None,
    json_mode: bool = False
) -> Optional[str]:
    return await retry_with_backoff(retries=3, base_delay=1.0, max_delay=10.0, jitter=True)(invoke_ollama_model_async_internal)(
        prompt, model_name, temperature, max_tokens, api_endpoint_override, task_name, json_mode
    )

class OllamaProvider:
    """
    A provider class for interacting with an Ollama service.
    This class wraps the model invocation functions.
    """
    # Expose defaults as class attributes if needed by external users
    DEFAULT_MODEL = DEFAULT_OLLAMA_MODEL

    def __init__(self, model_name: str = DEFAULT_OLLAMA_MODEL, base_url: Optional[str] = None):
        self.model = model_name
        # Ensure os is imported if you use os.path.join here
        # For now, assuming OLLAMA_API_ENDPOINT is a full URL and we derive base_url
        self.base_url = base_url or OLLAMA_API_ENDPOINT.rsplit('/api/', 1)[0]
        self.generate_endpoint = f"{self.base_url}/api/generate"
        self.chat_endpoint = f"{self.base_url}/api/chat"
        self.embeddings_endpoint = f"{self.base_url}/api/embeddings"

    async def get_embeddings_async(self, text: str, model_name: Optional[str] = None) -> Optional[List[float]]:
        """
        Generates embeddings using the dedicated Gemini embedding model.

        Embeddings are intentionally independent from the chat provider. DeepSeek
        V4 handles generation, but it is not an embeddings endpoint, and routing
        this call to local Ollama would fail when DeepSeek is the active provider.
        """
        return await gemini_client.get_embeddings_async(
            text,
            model_name=model_name or GEMINI_EMBEDDING_MODEL,
        )

    async def generate_code_async(self, prompt: str) -> Optional[str]:
        """
        Generates code using the LLM. Convenience wrapper around invoke_ollama_model_async.
        """
        return await self.invoke_ollama_model_async(prompt, temperature=0.2)

    async def invoke_ollama_model_async(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1500,
        task_name: Optional[str] = None,
        json_mode: bool = False
    ) -> Optional[str]:
        effective_model_name = model_name or self.model
        return await invoke_ollama_model_async_internal(
            prompt=prompt,
            model_name=effective_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            api_endpoint_override=self.generate_endpoint,
            task_name=task_name,
            json_mode=json_mode
        )

    def invoke_ollama_model(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1500,
        task_name: Optional[str] = None,
        json_mode: bool = False
    ) -> Optional[str]:
        effective_model_name = model_name or self.model
        return invoke_ollama_model(
            prompt=prompt,
            model_name=effective_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            task_name=task_name,
            json_mode=json_mode
        )

    async def list_models_async(self) -> List[Dict[str, Any]]:
        list_endpoint = os.path.join(self.base_url, "api/tags")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60.0)) as session:
            try:
                async with session.get(list_endpoint) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return data.get("models", [])
            except aiohttp.ClientError as e:
                print(f"HTTP error listing models: {e}")
                return []
            except json.JSONDecodeError:
                print("Error parsing JSON from list models response.")
                return []
            except Exception as e:
                print(f"Unexpected error listing models: {e}")
                return []
            
            # Windows/ProactorEventLoop workaround
            await asyncio.sleep(0.250)


async def main_async_test():
    print("\n--- Testing Asynchronous Ollama Client (with retries) ---")
    provider = OllamaProvider()
    print(f"Attempting to invoke model: {provider.model} via {provider.base_url} (async with retries)")
    print("Please ensure your Ollama service is running and the model is available.")
    
    test_prompt = "Why is the sky blue? Explain very concisely using async."
    
    try:
        response = await provider.invoke_ollama_model_async(test_prompt)
        
        if response:
            print("\n--- Ollama Response (async) ---")
            print(response)
            print("-----------------------------")
        else:
            print("\n--- Failed to get response from Ollama (async) after retries ---")
            print("Check console for specific errors (HTTP, connection, timeout, etc.).")
            print("Possible reasons:")
            print("1. Ollama service is not running or not accessible at the endpoint.")
            print(f"2. The model '{provider.model}' is not available. Try 'ollama pull {provider.model}'.")
    except Exception as e:
        print(f"\n--- An error occurred during async test after potential retries: {e} ---")
        print("This might be the final exception after all retries failed.")

    print("\n--- Listing models (async via provider) ---")
    models = await provider.list_models_async()
    if models:
        print("Available models:")
        for model_info in models:
            print(f"  - {model_info.get('name')} (Size: {model_info.get('size')}, Modified: {model_info.get('modified_at')})")
    else:
        print("Could not retrieve model list or no models available.")


if __name__ == '__main__':
    print("--- Testing Ollama Client (with retries) ---")
    sync_provider = OllamaProvider()
    print(f"Attempting to invoke model: {sync_provider.model} via {sync_provider.base_url} (sync with retries)")
    print(f"Please ensure your Ollama service is running and the model is available (e.g., run 'ollama pull {sync_provider.model}').")
    
    sync_test_prompt = "Why is the sun hot? Explain concisely."
    
    try:
        sync_response_content = sync_provider.invoke_ollama_model(sync_test_prompt)
        if sync_response_content:
            print("\n--- Ollama Response (sync) ---")
            print(sync_response_content)
            print("-----------------------")
        else:
            print("\n--- Failed to get response from Ollama (sync) after retries ---")
    except Exception as e:
        print(f"\n--- An error occurred during sync test after potential retries: {e} ---")

    asyncio.run(main_async_test())

    print("\n--- Ollama Client Test Finished ---")
