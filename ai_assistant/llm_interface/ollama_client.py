# ai_assistant/llm_interface/ollama_client.py
import requests
import json
from typing import Optional, Dict, Union, Tuple, Any, List
import asyncio
import aiohttp
import os

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
    if content is None: # Fallback for non-chat or if content not in message
        content = response_data.get("response", "").strip()
    if not content:
        return None
    return (content, thinking)

@retry_with_backoff(retries=3, base_delay=1.0, max_delay=10.0, jitter=True)
def invoke_ollama_model(
    prompt: str, # If messages_history is provided, this 'prompt' is treated as a system message
    model_name: str = DEFAULT_OLLAMA_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    messages_history: Optional[List[Dict[str, str]]] = None # New parameter
) -> Optional[str]:
    enable_thinking = ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS
    enable_chain_of_thought = ENABLE_CHAIN_OF_THOUGHT and not enable_thinking and not messages_history

    # Determine if chat API should be used
    # Use chat API if model supports native thinking OR if messages_history is provided.
    use_chat_api = enable_thinking or bool(messages_history)
    api_endpoint = OLLAMA_CHAT_API_ENDPOINT if use_chat_api else OLLAMA_API_ENDPOINT

    if enable_chain_of_thought: # This implies not use_chat_api and no messages_history
        thinking_prompt_text = THINKING_PROMPT_TEMPLATE.format(user_prompt=prompt)
        thinking_payload = {"model": model_name, "prompt": thinking_prompt_text, "stream": False, "options": {"temperature": DEFAULT_TEMPERATURE_THINKING, "num_predict": max_tokens}}
        # ... (rest of CoT logic as before) ...
        try:
            thinking_response = requests.post(api_endpoint, json=thinking_payload, timeout=600) # api_endpoint is OLLAMA_API_ENDPOINT here
            thinking_response.raise_for_status()
            thinking_result = thinking_response.json().get("response", "").strip()
            # ... (logging CoT) ...
            response_prompt_text = RESPONSE_WITH_THINKING_PROMPT_TEMPLATE.format(thinking_process=thinking_result, user_prompt=prompt)
            final_payload = {"model": model_name, "prompt": response_prompt_text, "stream": False, "options": {"temperature": DEFAULT_TEMPERATURE_RESPONSE, "num_predict": max_tokens}}
            final_response = requests.post(api_endpoint, json=final_payload, timeout=600) # api_endpoint is OLLAMA_API_ENDPOINT here
            final_response.raise_for_status()
            return final_response.json().get("response", "").strip()
        except requests.exceptions.RequestException as e:
            print(f"Error during chain of thought process: {e}")
            return None

    # Regular call (either chat or generate endpoint)
    payload_messages: Optional[List[Dict[str, str]]] = None
    payload_prompt: Optional[str] = None

    if use_chat_api:
        if messages_history:
            payload_messages = messages_history
            if prompt and prompt.strip(): # Treat 'prompt' as system message if history exists
                payload_messages = [{"role": "system", "content": prompt}] + messages_history
        else: # No history, but use_chat_api (e.g. for a thinking model's first turn)
            payload_messages = [{"role": "user", "content": prompt}]
    else: # Not using chat_api (generate endpoint)
        payload_prompt = prompt

    payload: Dict[str, Any] = {
        "model": model_name,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens}
    }
    if payload_messages is not None: # For /api/chat
        payload["messages"] = payload_messages
        if enable_thinking: # Only add 'think' if model supports it and we are using chat
            payload["think"] = True
    elif payload_prompt is not None: # For /api/generate
        payload["prompt"] = payload_prompt
    else: # Should not happen if logic is correct
        print(f"Error: Neither messages nor prompt was set for model {model_name}.")
        return None

    try:
        # ... (logging request) ...
        response = requests.post(api_endpoint, json=payload, timeout=600)
        response.raise_for_status()
    # ... (exception handling as before) ...
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error occurred: {e}")
        if is_debug_mode() and e.response is not None:
            print(f"[DEBUG] Status code: {e.response.status_code}")
            try: print(f"[DEBUG] Response body: {e.response.json()}")
            except Exception: print(f"[DEBUG] Response body could not be parsed as JSON.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error invoking Ollama model '{model_name}': {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during the request: {e}")
        return None

    try:
        parsed_response = response.json()
        # ... (process_llm_response and logging as before) ...
        result = process_llm_response(parsed_response)
        if not result: return None
        content, thinking = result
        if enable_thinking and use_chat_api: # only log native thinking if it was requested
            if thinking:
                # ... (log thinking) ...
                pass
            elif is_debug_mode(): # ... (log no thinking returned) ...
                pass
        return content
    except json.JSONDecodeError: # ... (handle JSON error) ...
        print("Error: Failed to parse JSON response from Ollama.")
        print(f"Raw response text: {response.text}")
        return None
    return None # Should be unreachable if logic is correct

async def invoke_ollama_model_async_internal(
    prompt: str, # If messages_history is provided, this 'prompt' is treated as a system message
    model_name: str = DEFAULT_OLLAMA_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    api_endpoint_override: Optional[str] = None,
    messages_history: Optional[List[Dict[str, str]]] = None
) -> Optional[str]:
    enable_thinking = ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS
    enable_chain_of_thought = ENABLE_CHAIN_OF_THOUGHT and not enable_thinking and not messages_history

    use_chat_api_flag = enable_thinking or bool(messages_history)
    current_api_endpoint = OLLAMA_CHAT_API_ENDPOINT if use_chat_api_flag else OLLAMA_API_ENDPOINT
    if api_endpoint_override:
        current_api_endpoint = api_endpoint_override
        use_chat_api_flag = (current_api_endpoint == OLLAMA_CHAT_API_ENDPOINT)


    if enable_chain_of_thought: # This implies not use_chat_api_flag and no messages_history
        thinking_prompt_text = THINKING_PROMPT_TEMPLATE.format(user_prompt=prompt)
        thinking_payload = {"model": model_name, "prompt": thinking_prompt_text, "stream": False, "options": {"temperature": DEFAULT_TEMPERATURE_THINKING, "num_predict": max_tokens}}
        # ... (rest of async CoT logic as before, using OLLAMA_API_ENDPOINT for its requests) ...
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600.0)) as session:
            try:
                async with session.post(OLLAMA_API_ENDPOINT, json=thinking_payload) as thinking_response: # CoT always uses generate
                    thinking_response.raise_for_status()
                    thinking_data = await thinking_response.json()
                    thinking_result = thinking_data.get("response", "").strip()
                    # ... (log CoT) ...
                    response_prompt_text = RESPONSE_WITH_THINKING_PROMPT_TEMPLATE.format(thinking_process=thinking_result, user_prompt=prompt)
                    final_payload = {"model": model_name, "prompt": response_prompt_text, "stream": False, "options": {"temperature": DEFAULT_TEMPERATURE_RESPONSE, "num_predict": max_tokens}}
                    async with session.post(OLLAMA_API_ENDPOINT, json=final_payload) as final_response: # CoT always uses generate
                        final_response.raise_for_status()
                        final_data = await final_response.json()
                        return final_data.get("response", "").strip()
            except Exception as e: print(f"Error during async CoT: {e}"); return None


    # Regular async call (either chat or generate endpoint)
    payload_messages_async: Optional[List[Dict[str, str]]] = None
    payload_prompt_async: Optional[str] = None

    if use_chat_api_flag:
        if messages_history:
            payload_messages_async = messages_history
            if prompt and prompt.strip(): # Treat 'prompt' as system message if history exists
                payload_messages_async = [{"role": "system", "content": prompt}] + messages_history
        else: # No history, but use_chat_api (e.g. for a thinking model's first turn)
            payload_messages_async = [{"role": "user", "content": prompt}]
    else: # Not using chat_api (generate endpoint)
        payload_prompt_async = prompt

    payload: Dict[str, Any] = {
        "model": model_name,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens}
    }
    if payload_messages_async is not None: # For /api/chat
        payload["messages"] = payload_messages_async
        if enable_thinking: # Only add 'think' if model supports it and we are using chat
            payload["think"] = True
    elif payload_prompt_async is not None: # For /api/generate
        payload["prompt"] = payload_prompt_async
    else: # Should not happen
        print(f"Error: Neither messages nor prompt was set for async model {model_name}.")
        return None

    # ... (logging request) ...
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600.0)) as session:
        try:
            async with session.post(current_api_endpoint, json=payload) as response:
                response.raise_for_status()
                response_data = await response.json()
                # ... (process_llm_response and logging as before) ...
                result = process_llm_response(response_data)
                if not result: return None
                content, thinking = result
                if enable_thinking and use_chat_api_flag: # only log native thinking if it was requested
                    if thinking:
                        # ... (log thinking) ...
                        pass
                    elif is_debug_mode(): # ... (log no thinking returned) ...
                        pass
                return content
        # ... (exception handling as before) ...
        except aiohttp.ClientError as e: print(f"HTTP error occurred in async call: {e}"); return None
        except json.JSONDecodeError: print("Error: Failed to parse JSON response from Ollama (async)."); return None
        except Exception as e: print(f"An unexpected error occurred during the async request: {e}"); return None
    return None # Should be unreachable

invoke_ollama_model_async = retry_with_backoff(retries=3, base_delay=1.0, max_delay=10.0, jitter=True)(invoke_ollama_model_async_internal)

class OllamaProvider:
    def __init__(self, model_name: str = DEFAULT_OLLAMA_MODEL, base_url: Optional[str] = None):
        self.model = model_name
        self.base_url = base_url or OLLAMA_API_ENDPOINT.rsplit('/api/', 1)[0]
        self.generate_endpoint = f"{self.base_url}/api/generate"
        self.chat_endpoint = f"{self.base_url}/api/chat"

    async def invoke_ollama_model_async(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1500,
        messages_history: Optional[List[Dict[str, str]]] = None # Added
    ) -> Optional[str]:
        effective_model_name = model_name or self.model
        # Determine endpoint based on history or thinking model, passed to internal
        use_chat_api = (ENABLE_THINKING and effective_model_name in THINKING_SUPPORTED_MODELS) or bool(messages_history)
        api_to_use = self.chat_endpoint if use_chat_api else self.generate_endpoint

        return await invoke_ollama_model_async_internal(
            prompt=prompt,
            model_name=effective_model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            api_endpoint_override=api_to_use, # Pass determined endpoint
            messages_history=messages_history
        )

    def invoke_ollama_model( # Synchronous version needs similar logic update
        self,
        prompt: str,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1500,
        messages_history: Optional[List[Dict[str, str]]] = None # Added
    ) -> Optional[str]:
        # This synchronous version's internal logic needs to mirror the async_internal payload construction
        effective_model_name = model_name or self.model
        enable_thinking = ENABLE_THINKING and effective_model_name in THINKING_SUPPORTED_MODELS
        enable_chain_of_thought = ENABLE_CHAIN_OF_THOUGHT and not enable_thinking and not messages_history

        use_chat_api = enable_thinking or bool(messages_history)
        api_endpoint = self.chat_endpoint if use_chat_api else self.generate_endpoint

        if enable_chain_of_thought: # This implies not use_chat_api and no messages_history
            # ... (CoT logic as in original file) ...
            thinking_prompt_text = THINKING_PROMPT_TEMPLATE.format(user_prompt=prompt)
            thinking_payload = {"model": effective_model_name, "prompt": thinking_prompt_text, "stream": False, "options": {"temperature": DEFAULT_TEMPERATURE_THINKING, "num_predict": max_tokens}}
            try:
                thinking_response = requests.post(self.generate_endpoint, json=thinking_payload, timeout=600)
                thinking_response.raise_for_status()
                thinking_result = thinking_response.json().get("response", "").strip()
                response_prompt_text = RESPONSE_WITH_THINKING_PROMPT_TEMPLATE.format(thinking_process=thinking_result, user_prompt=prompt)
                final_payload = {"model": effective_model_name, "prompt": response_prompt_text, "stream": False, "options": {"temperature": DEFAULT_TEMPERATURE_RESPONSE, "num_predict": max_tokens}}
                final_response = requests.post(self.generate_endpoint, json=final_payload, timeout=600)
                final_response.raise_for_status()
                return final_response.json().get("response", "").strip()
            except requests.exceptions.RequestException as e:
                print(f"Error during sync chain of thought process: {e}")
                return None

        payload_messages: Optional[List[Dict[str, str]]] = None
        payload_prompt: Optional[str] = None

        if use_chat_api:
            if messages_history:
                payload_messages = messages_history
                if prompt and prompt.strip():
                    payload_messages = [{"role": "system", "content": prompt}] + messages_history
            else:
                payload_messages = [{"role": "user", "content": prompt}]
        else:
            payload_prompt = prompt

        payload_sync: Dict[str, Any] = {
            "model": effective_model_name,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens}
        }
        if payload_messages is not None:
            payload_sync["messages"] = payload_messages
            if enable_thinking: payload_sync["think"] = True
        elif payload_prompt is not None:
            payload_sync["prompt"] = payload_prompt
        else:
            print(f"Error: Sync - Neither messages nor prompt was set for model {effective_model_name}.")
            return None

        try:
            response = requests.post(api_endpoint, json=payload_sync, timeout=600)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Error invoking Ollama model '{effective_model_name}' (sync): {e}")
            return None

        try:
            parsed_response = response.json()
            result = process_llm_response(parsed_response)
            if not result: return None
            content, _ = result # Ignoring thinking part for sync wrapper for now
            return content
        except json.JSONDecodeError:
            print(f"Error: Failed to parse JSON response from Ollama (sync). Raw: {response.text}")
            return None
        return None # Should be unreachable


    async def list_models_async(self) -> List[Dict[str, Any]]:
        list_endpoint = os.path.join(self.base_url, "api/tags") # Corrected: use self.base_url
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60.0)) as session:
            try:
                async with session.get(list_endpoint) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return data.get("models", [])
            except Exception as e:
                print(f"Error listing models (async): {e}")
                return []

async def main_async_test():
    print("\n--- Testing Asynchronous Ollama Client (with retries) ---")
    provider = OllamaProvider()
    # ... (rest of test remains the same, but now uses updated provider methods) ...
    history_example = [
        {"role": "user", "content": "What was the capital of France before Paris?"},
        {"role": "assistant", "content": "Several cities served as capitals or royal residences before Paris..."},
    ]
    current_user_prompt = "And what about Germany?"
    full_history_for_call = history_example + [{"role": "user", "content": current_user_prompt}]

    try:
        # Test with system message via prompt + history
        response = await provider.invoke_ollama_model_async(
            prompt="You are an expert historian. Respond to the last user message based on the history.",
            messages_history=full_history_for_call
        )
        # ... (print response) ...
        if response: print(f"\nResponse with history+system: {response}")
        else: print("\nFailed (history+system)")
    except Exception as e: print(f"Error: {e}")


if __name__ == '__main__':
    # ... (sync test needs similar update for messages_history if it's to be tested fully) ...
    sync_provider = OllamaProvider()
    history_sync_example = [
            {"role": "user", "content": "What is the main component of the sun?"},
            {"role": "assistant", "content": "The sun is primarily composed of hydrogen..."},
    ]
    current_sync_prompt = "Is it very hot?"
    full_sync_history_for_call = history_sync_example + [{"role": "user", "content": current_sync_prompt}]
    sync_response_content = sync_provider.invoke_ollama_model(
        prompt="You are a space expert. Answer the last user question based on history.",
        messages_history=full_sync_history_for_call
    )
    if sync_response_content: print(f"\nSync Response with history+system: {sync_response_content}")
    else: print("\nSync failed (history+system)")
    
    asyncio.run(main_async_test())
    print("\n--- Ollama Client Test Finished ---")
