# ai_assistant/llm_interface/ollama_client.py
import requests
import json
from typing import Optional, Dict, Union, Tuple, Any, List
import asyncio
import aiohttp
import os # Added import os

from ai_assistant.config import (
    DEFAULT_MODEL as CFG_DEFAULT_MODEL,
    is_debug_mode,
    ENABLE_THINKING,
    THINKING_SUPPORTED_MODELS,
    ENABLE_CHAIN_OF_THOUGHT,
    DEFAULT_TEMPERATURE_THINKING,
    DEFAULT_TEMPERATURE_RESPONSE,
    THINKING_CONFIG
)
from ai_assistant.debugging.resilience import retry_with_backoff

OLLAMA_API_ENDPOINT = "http://192.168.86.30:11434/api/generate"
OLLAMA_CHAT_API_ENDPOINT = "http://192.168.86.30:11434/api/chat"
DEFAULT_OLLAMA_MODEL = CFG_DEFAULT_MODEL

THINKING_PROMPT_TEMPLATE = """You are a highly capable AI assistant with strong analytical and problem-solving abilities. Let's solve this problem step by step.

Original prompt: {user_prompt}

Before providing the final answer, I want you to think through this carefully. Break down your thought process:
1. Understand what's being asked
2. Identify the key elements and requirements
3. Consider potential approaches
4. Plan your response
5. Think about edge cases or potential issues

Do not give the final answer yet. Instead, walk me through your thinking process step by step.
Think it through..."""

RESPONSE_WITH_THINKING_PROMPT_TEMPLATE = """Now that you've thought it through, use your analysis to provide a clear, concise, and accurate response.

Your previous thinking process:
{thinking_process}

Original prompt: {user_prompt}

Provide your final response now, using your thought process to ensure accuracy and completeness."""

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

@retry_with_backoff(retries=3, base_delay=1.0, max_delay=10.0, jitter=True)
def invoke_ollama_model(
    prompt: str,
    model_name: str = DEFAULT_OLLAMA_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500
) -> Optional[str]:
    enable_thinking = ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS
    enable_chain_of_thought = ENABLE_CHAIN_OF_THOUGHT and not enable_thinking
    use_chat_api = enable_thinking

    if enable_chain_of_thought:
        thinking_prompt = THINKING_PROMPT_TEMPLATE.format(user_prompt=prompt)
        thinking_payload = {
            "model": model_name,
            "prompt": thinking_prompt,
            "stream": False,
            "options": {
                "temperature": DEFAULT_TEMPERATURE_THINKING,
                "num_predict": max_tokens
            }
        }
        if is_debug_mode():
            print(f"[DEBUG] Chain of thought - Thinking phase starting for model {model_name}")
            print(f"[DEBUG] Thinking prompt: {thinking_prompt[:200]}...")
        try:
            thinking_response = requests.post(OLLAMA_API_ENDPOINT, json=thinking_payload, timeout=600)
            thinking_response.raise_for_status()
            thinking_result = thinking_response.json().get("response", "").strip()
            if thinking_result:
                if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                    print(f"[DEBUG] {THINKING_CONFIG['display']['prefix'].strip()} {thinking_result} {THINKING_CONFIG['display']['suffix'].strip()}")
                elif not is_debug_mode() and THINKING_CONFIG["display"]["show_in_release"]:
                    print(f"{THINKING_CONFIG['display']['prefix'].strip()} {thinking_result} {THINKING_CONFIG['display']['suffix'].strip()}")
            elif is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                 print(f"[DEBUG] CoT: No thinking process generated.")
            response_prompt = RESPONSE_WITH_THINKING_PROMPT_TEMPLATE.format(
                thinking_process=thinking_result, user_prompt=prompt
            )
            final_payload = {
                "model": model_name, "prompt": response_prompt, "stream": False,
                "options": {"temperature": DEFAULT_TEMPERATURE_RESPONSE, "num_predict": max_tokens}
            }
            if is_debug_mode():
                print(f"[DEBUG] Chain of thought - Response phase starting")
                print(f"[DEBUG] Response prompt: {response_prompt[:200]}...")
            final_response = requests.post(OLLAMA_API_ENDPOINT, json=final_payload, timeout=600)
            final_response.raise_for_status()
            final_result = final_response.json().get("response", "").strip()
            if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                print(f"[DEBUG] CoT Final Response: {final_result[:200]}...")
            return final_result
        except requests.exceptions.RequestException as e:
            print(f"Error during chain of thought process: {e}")
            return None

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}] if use_chat_api else None,
        "prompt": "" if use_chat_api else prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens}
    }
    if use_chat_api: payload["think"] = True
    api_endpoint = OLLAMA_CHAT_API_ENDPOINT if use_chat_api else OLLAMA_API_ENDPOINT

    try:
        if is_debug_mode():
            print(f"[DEBUG] Sending request to Ollama with model: {model_name}, prompt: '{prompt[:100]}...'")
            if enable_thinking: print(f"[DEBUG] Native thinking enabled for model {model_name}")
        else: print(f"Sending request to Ollama with model: {model_name}, prompt: '{prompt[:50]}...'")
        response = requests.post(api_endpoint, json=payload, timeout=600)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error occurred: {e}")
        if is_debug_mode() and e.response is not None:
            print(f"[DEBUG] Status code: {e.response.status_code}")
            try: print(f"[DEBUG] Response body: {e.response.json()}")
            except Exception: print(f"[DEBUG] Response body could not be parsed as JSON.")
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
        if enable_thinking:
            if thinking:
                if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                    print(f"[DEBUG] {THINKING_CONFIG['display']['prefix'].strip()} {thinking} {THINKING_CONFIG['display']['suffix'].strip()}")
                elif not is_debug_mode() and THINKING_CONFIG["display"]["show_in_release"]:
                    print(f"{THINKING_CONFIG['display']['prefix'].strip()} {thinking} {THINKING_CONFIG['display']['suffix'].strip()}")
            elif is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                print(f"[DEBUG] Native thinking enabled for {model_name}, but no thinking process was returned by the model.")
        if is_debug_mode(): print(f"[DEBUG] Final content being returned: {content[:200]}...")
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
    api_endpoint_override: Optional[str] = None
) -> Optional[str]:
    enable_thinking = ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS
    enable_chain_of_thought = ENABLE_CHAIN_OF_THOUGHT and not enable_thinking
    use_chat_api = enable_thinking

    current_api_endpoint = api_endpoint_override if api_endpoint_override else OLLAMA_API_ENDPOINT
    if use_chat_api and not api_endpoint_override:
        current_api_endpoint = OLLAMA_CHAT_API_ENDPOINT


    if enable_chain_of_thought:
        thinking_prompt = THINKING_PROMPT_TEMPLATE.format(user_prompt=prompt)
        thinking_payload = {
            "model": model_name, "prompt": thinking_prompt, "stream": False,
            "options": {"temperature": DEFAULT_TEMPERATURE_THINKING, "num_predict": max_tokens}
        }
        if is_debug_mode():
            print(f"[DEBUG] Chain of thought - Thinking phase starting for model {model_name}")
            print(f"[DEBUG] Thinking prompt: {thinking_prompt[:200]}...")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600.0)) as session:
            try:
                async with session.post(OLLAMA_API_ENDPOINT, json=thinking_payload) as thinking_response:
                    thinking_response.raise_for_status()
                    thinking_data = await thinking_response.json()
                    thinking_result = thinking_data.get("response", "").strip()
                    if thinking_result:
                        if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                            print(f"[DEBUG] {THINKING_CONFIG['display']['prefix'].strip()} {thinking_result} {THINKING_CONFIG['display']['suffix'].strip()}")
                        elif not is_debug_mode() and THINKING_CONFIG["display"]["show_in_release"]:
                            print(f"{THINKING_CONFIG['display']['prefix'].strip()} {thinking_result} {THINKING_CONFIG['display']['suffix'].strip()}")
                    elif is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                        print(f"[DEBUG] Async CoT: No thinking process generated.")
                    response_prompt = RESPONSE_WITH_THINKING_PROMPT_TEMPLATE.format(
                        thinking_process=thinking_result, user_prompt=prompt
                    )
                    final_payload = {
                        "model": model_name, "prompt": response_prompt, "stream": False,
                        "options": {"temperature": DEFAULT_TEMPERATURE_RESPONSE, "num_predict": max_tokens}
                    }
                    if is_debug_mode():
                        print(f"[DEBUG] Chain of thought - Response phase starting")
                        print(f"[DEBUG] Response prompt: {response_prompt[:200]}...")
                    async with session.post(OLLAMA_API_ENDPOINT, json=final_payload) as final_response:
                        final_response.raise_for_status()
                        final_data = await final_response.json()
                        final_result = final_data.get("response", "").strip()
                        if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                             print(f"[DEBUG] Async CoT Final Response: {final_result[:200]}...")
                        return final_result
            except aiohttp.ClientError as e: print(f"HTTP error occurred in async CoT: {e}"); return None
            except json.JSONDecodeError as e: print(f"Error decoding JSON in async CoT: {e}"); return None
            except Exception as e: print(f"An unexpected error occurred in async CoT: {e}"); return None

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}] if use_chat_api else None,
        "prompt": "" if use_chat_api else prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens}
    }
    if use_chat_api: payload["think"] = True

    if is_debug_mode():
        print(f"[DEBUG] Sending async request to Ollama with model: {model_name}, prompt: '{prompt[:100]}...' to {current_api_endpoint}")
        if enable_thinking: print(f"[DEBUG] Native thinking enabled for model {model_name}")
    else: print(f"Sending async request to Ollama with model: {model_name}, prompt: '{prompt[:50]}...'")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600.0)) as session:
        try:
            async with session.post(current_api_endpoint, json=payload) as response:
                response.raise_for_status()
                response_data = await response.json()
                if is_debug_mode(): print(f"[DEBUG] Ollama async response JSON: {str(response_data)[:500]}")
                result = process_llm_response(response_data)
                if not result: return None
                content, thinking = result
                if enable_thinking:
                    if thinking:
                        if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                            print(f"[DEBUG] {THINKING_CONFIG['display']['prefix'].strip()} {thinking} {THINKING_CONFIG['display']['suffix'].strip()}")
                        elif not is_debug_mode() and THINKING_CONFIG["display"]["show_in_release"]:
                            print(f"{THINKING_CONFIG['display']['prefix'].strip()} {thinking} {THINKING_CONFIG['display']['suffix'].strip()}")
                    elif is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                        print(f"[DEBUG] Async native thinking enabled for {model_name}, but no thinking process was returned by the model.")
                if is_debug_mode(): print(f"[DEBUG] Async final content being returned: {content[:200]}...")
                return content
        except aiohttp.ClientError as e: print(f"HTTP error occurred in async call: {e}"); return None
        except json.JSONDecodeError: print("Error: Failed to parse JSON response from Ollama (async)."); return None
        except Exception as e: print(f"An unexpected error occurred during the async request: {e}"); return None

invoke_ollama_model_async = retry_with_backoff(retries=3, base_delay=1.0, max_delay=10.0, jitter=True)(invoke_ollama_model_async_internal)

class OllamaProvider:
    """
    A provider class for interacting with an Ollama service.
    This class wraps the model invocation functions.
    """
    def __init__(self, model_name: str = DEFAULT_OLLAMA_MODEL, base_url: Optional[str] = None):
        self.model = model_name
        # Ensure os is imported if you use os.path.join here
        # For now, assuming OLLAMA_API_ENDPOINT is a full URL and we derive base_url
        self.base_url = base_url or OLLAMA_API_ENDPOINT.rsplit('/api/', 1)[0]
        self.generate_endpoint = os.path.join(self.base_url, "api/generate")
        self.chat_endpoint = os.path.join(self.base_url, "api/chat")


    async def invoke_ollama_model_async(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1500
    ) -> Optional[str]:
        effective_model_name = model_name or self.model
        enable_thinking = ENABLE_THINKING and effective_model_name in THINKING_SUPPORTED_MODELS
        use_chat_api = enable_thinking
        api_to_use = self.chat_endpoint if use_chat_api else self.generate_endpoint

        return await invoke_ollama_model_async_internal(
            prompt=prompt,
            model_name=effective_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            api_endpoint_override=api_to_use
        )

    def invoke_ollama_model(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1500
    ) -> Optional[str]:
        effective_model_name = model_name or self.model
        return invoke_ollama_model(
            prompt=prompt,
            model_name=effective_model_name,
            temperature=temperature,
            max_tokens=max_tokens
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


async def main_async_test():
    print("\n--- Testing Asynchronous Ollama Client (with retries) ---")
    provider = OllamaProvider()
    print(f"Attempting to invoke model: {provider.model} via {provider.base_url} (async with retries)")
    print("Please ensure your Ollama service is running and the model is available.")
    
    test_prompt = "Why is the sky blue? Explain very concisely using async."
    
    try:
        response = await provider.invoke_ollama_model_async(test_prompt)
        
        if response:
            print(f"\n--- Ollama Response (async) ---")
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
