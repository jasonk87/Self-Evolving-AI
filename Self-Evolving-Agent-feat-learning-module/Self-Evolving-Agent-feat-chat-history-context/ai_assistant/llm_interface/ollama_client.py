# ai_assistant/llm_interface/ollama_client.py
import requests
import json
from typing import Optional, Dict, Union, Tuple
import asyncio
import aiohttp # For asynchronous HTTP requests

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
from ai_assistant.debugging.resilience import retry_with_backoff # Import the retry decorator

# Define Constants
#OLLAMA_API_ENDPOINT = "http://localhost:11434/api/generate"
#OLLAMA_CHAT_API_ENDPOINT = "http://localhost:11434/api/chat"
OLLAMA_API_ENDPOINT = "http://192.168.86.30:11434/api/generate"
OLLAMA_CHAT_API_ENDPOINT = "http://192.168.86.30:11434/api/chat"
DEFAULT_OLLAMA_MODEL = CFG_DEFAULT_MODEL

# Chain of thought prompt templates
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
    """Process LLM response and extract content and thinking."""
    if not response_data:
        return None
        
    thinking = None
    content = None
    
    if "message" in response_data:
        message = response_data["message"]
        if isinstance(message, dict):
            thinking = message.get("thinking")
            content = message.get("content")
    
    # Fallback to basic response if not in message format
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
    """
    Invokes the specified Ollama model with the given prompt, with retry logic.

    Args:
        prompt: The input prompt for the LLM.
        model_name: The name of the Ollama model to use (e.g., "qwen3:latest").
        temperature: Controls randomness. Lower is more deterministic.
        max_tokens: Maximum number of tokens to generate.

    Returns:
        The LLM's response string, or None if an error occurs.
        When thinking is enabled and supported, the response will include both 
        the thinking process and final answer separated by "...done thinking\\n\\n"
    """
    enable_thinking = ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS
    enable_chain_of_thought = ENABLE_CHAIN_OF_THOUGHT and not enable_thinking
    use_chat_api = enable_thinking

    if enable_chain_of_thought:
        # Step 1: Generate thinking process
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
            
            # Display thinking process based on configuration
            if thinking_result:
                if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                    print(f"[DEBUG] {THINKING_CONFIG['display']['prefix'].strip()} {thinking_result} {THINKING_CONFIG['display']['suffix'].strip()}")
                elif not is_debug_mode() and THINKING_CONFIG["display"]["show_in_release"]:
                    print(f"{THINKING_CONFIG['display']['prefix'].strip()} {thinking_result} {THINKING_CONFIG['display']['suffix'].strip()}")
            elif is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                 print(f"[DEBUG] CoT: No thinking process generated.")

            # Step 2: Generate final response incorporating the thinking
            # Note: The original prompt is included again here, which is standard for this CoT template.
            response_prompt = RESPONSE_WITH_THINKING_PROMPT_TEMPLATE.format(
                thinking_process=thinking_result,
                user_prompt=prompt
            )

            final_payload = {
                "model": model_name,
                "prompt": response_prompt,
                "stream": False,
                "options": {
                    "temperature": DEFAULT_TEMPERATURE_RESPONSE,
                    "num_predict": max_tokens
                }
            }

            if is_debug_mode():
                print(f"[DEBUG] Chain of thought - Response phase starting")
                print(f"[DEBUG] Response prompt: {response_prompt[:200]}...")

            final_response = requests.post(OLLAMA_API_ENDPOINT, json=final_payload, timeout=600)
            final_response.raise_for_status()
            final_result = final_response.json().get("response", "").strip()

            if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]: # Also show final result in debug
                print(f"[DEBUG] CoT Final Response: {final_result[:200]}...")

            return final_result # Return only the final result for history

        except requests.exceptions.RequestException as e:
            print(f"Error during chain of thought process: {e}")
            return None

    # Regular processing for thinking-supported models or when chain of thought is disabled
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}] if use_chat_api else None,
        "prompt": "" if use_chat_api else prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens
        }
    }

    if use_chat_api:
        payload["think"] = True

    api_endpoint = OLLAMA_CHAT_API_ENDPOINT if use_chat_api else OLLAMA_API_ENDPOINT

    try:
        if is_debug_mode():
            print(f"[DEBUG] Sending request to Ollama with model: {model_name}, prompt: '{prompt[:100]}...'")
            if enable_thinking:
                print(f"[DEBUG] Native thinking enabled for model {model_name}")
        else:
            print(f"Sending request to Ollama with model: {model_name}, prompt: '{prompt[:50]}...'")

        response = requests.post(api_endpoint, json=payload, timeout=600)
        response.raise_for_status()

    except requests.exceptions.HTTPError as e:
        print(f"HTTP error occurred: {e}")
        if is_debug_mode() and e.response is not None:
            print(f"[DEBUG] Status code: {e.response.status_code}")
            try:
                print(f"[DEBUG] Response body: {e.response.json()}")
            except Exception:
                print(f"[DEBUG] Response body could not be parsed as JSON.")
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
        
        if not result:
            return None

        content, thinking = result
        
        if enable_thinking: # Only if native thinking is generally enabled
            if thinking: # If the model actually provided thinking
                if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                    print(f"[DEBUG] {THINKING_CONFIG['display']['prefix'].strip()} {thinking} {THINKING_CONFIG['display']['suffix'].strip()}")
                elif not is_debug_mode() and THINKING_CONFIG["display"]["show_in_release"]:
                    print(f"{THINKING_CONFIG['display']['prefix'].strip()} {thinking} {THINKING_CONFIG['display']['suffix'].strip()}")
            elif is_debug_mode() and THINKING_CONFIG["display"]["show_working"]: # Thinking enabled, but model didn't provide it
                print(f"[DEBUG] Native thinking enabled for {model_name}, but no thinking process was returned by the model.")
        
        if is_debug_mode():
            print(f"[DEBUG] Final content being returned: {content[:200]}...")

        return content # Return only the content for history

    except json.JSONDecodeError:
        print("Error: Failed to parse JSON response from Ollama.")
        print(f"Raw response text: {response.text}")
        return None

async def invoke_ollama_model_async(
    prompt: str,
    model_name: str = DEFAULT_OLLAMA_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    api_endpoint: str = OLLAMA_API_ENDPOINT
) -> Optional[str]:
    """
    Invokes the specified Ollama model asynchronously with the given prompt, with retry logic.
    When thinking is enabled and supported, the response will include both 
    the thinking process and final answer separated by "...done thinking\\n\\n"
    """
    enable_thinking = ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS
    enable_chain_of_thought = ENABLE_CHAIN_OF_THOUGHT and not enable_thinking
    use_chat_api = enable_thinking

    if enable_chain_of_thought:
        # Step 1: Generate thinking process
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
                    # Step 2: Generate final response incorporating the thinking
                    response_prompt = RESPONSE_WITH_THINKING_PROMPT_TEMPLATE.format(
                        thinking_process=thinking_result,
                        user_prompt=prompt
                    )

                    final_payload = {
                        "model": model_name,
                        "prompt": response_prompt,
                        "stream": False,
                        "options": {
                            "temperature": DEFAULT_TEMPERATURE_RESPONSE,
                            "num_predict": max_tokens
                        }
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

                        return final_result # Return only the final result for history

            except aiohttp.ClientError as e:
                print(f"HTTP error occurred in async CoT: {e}")
                return None
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON in async CoT: {e}")
                return None
            except Exception as e:
                print(f"An unexpected error occurred in async CoT: {e}")
                return None

    if use_chat_api:
        api_endpoint = OLLAMA_CHAT_API_ENDPOINT

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}] if use_chat_api else None,
        "prompt": "" if use_chat_api else prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens
        }
    }

    if use_chat_api:
        payload["think"] = True

    if is_debug_mode():
        print(f"[DEBUG] Sending async request to Ollama with model: {model_name}, prompt: '{prompt[:100]}...'")
        if enable_thinking:
            print(f"[DEBUG] Native thinking enabled for model {model_name}")
    else:
        print(f"Sending async request to Ollama with model: {model_name}, prompt: '{prompt[:50]}...'")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600.0)) as session:
        try:
            async with session.post(api_endpoint, json=payload) as response:
                response.raise_for_status()
                response_data = await response.json()

                if is_debug_mode():
                    print(f"[DEBUG] Ollama async response JSON: {str(response_data)[:500]}")

                result = process_llm_response(response_data)
                
                if not result:
                    return None

                content, thinking = result
                
                if enable_thinking: # Only if native thinking is generally enabled
                    if thinking: # If the model actually provided thinking
                        if is_debug_mode() and THINKING_CONFIG["display"]["show_working"]:
                            print(f"[DEBUG] {THINKING_CONFIG['display']['prefix'].strip()} {thinking} {THINKING_CONFIG['display']['suffix'].strip()}")
                        elif not is_debug_mode() and THINKING_CONFIG["display"]["show_in_release"]:
                            print(f"{THINKING_CONFIG['display']['prefix'].strip()} {thinking} {THINKING_CONFIG['display']['suffix'].strip()}")
                    elif is_debug_mode() and THINKING_CONFIG["display"]["show_working"]: # Thinking enabled, but model didn't provide it
                        print(f"[DEBUG] Async native thinking enabled for {model_name}, but no thinking process was returned by the model.")

                if is_debug_mode():
                    print(f"[DEBUG] Async final content being returned: {content[:200]}...")

                return content # Return only the content for history

        except aiohttp.ClientError as e:
            print(f"HTTP error occurred in async call: {e}")
            return None
        except json.JSONDecodeError:
            print("Error: Failed to parse JSON response from Ollama (async).")
            return None
        except Exception as e:
            print(f"An unexpected error occurred during the async request: {e}")
            return None

# Applying the decorator to the async function
invoke_ollama_model_async = retry_with_backoff(retries=3, base_delay=1.0, max_delay=10.0, jitter=True)(invoke_ollama_model_async)


async def main_async_test():
    print("\n--- Testing Asynchronous Ollama Client (with retries) ---")
    print(f"Attempting to invoke model: {DEFAULT_OLLAMA_MODEL} via {OLLAMA_API_ENDPOINT} (async with retries)")
    print("Please ensure your Ollama service is running and the model is available.")
    
    test_prompt = "Why is the sky blue? Explain very concisely using async."
    
    # Simulate failure for retry testing (e.g., by stopping Ollama temporarily)
    # For now, we'll just call it and see if normal operation or retries occur.
    try:
        response = await invoke_ollama_model_async(test_prompt)
        
        if response:
            print(f"\n--- Ollama Response (async) ---")
            print(response)
            print("-----------------------------")
        else:
            print("\n--- Failed to get response from Ollama (async) after retries ---")
            print("Check console for specific errors (HTTP, connection, timeout, etc.).")
            print("Possible reasons:")
            print("1. Ollama service is not running or not accessible at the endpoint.")
            print(f"2. The model '{DEFAULT_OLLAMA_MODEL}' is not available. Try 'ollama pull {DEFAULT_OLLAMA_MODEL}'.")
    except Exception as e:
        print(f"\n--- An error occurred during async test after potential retries: {e} ---")
        print("This might be the final exception after all retries failed.")


if __name__ == '__main__':
    print("--- Testing Ollama Client (with retries) ---")
    # Test synchronous version
    print(f"Attempting to invoke model: {DEFAULT_OLLAMA_MODEL} via {OLLAMA_API_ENDPOINT} (sync with retries)")
    print(f"Please ensure your Ollama service is running and the model is available (e.g., run 'ollama pull {DEFAULT_OLLAMA_MODEL}').")
    
    sync_test_prompt = "Why is the sun hot? Explain concisely."
    
    try:
        sync_response_content = invoke_ollama_model(sync_test_prompt)
        if sync_response_content:
            print("\n--- Ollama Response (sync) ---")
            print(sync_response_content)
            print("-----------------------")
        else:
            print("\n--- Failed to get response from Ollama (sync) after retries ---")
    except Exception as e:
        print(f"\n--- An error occurred during sync test after potential retries: {e} ---")

    # Run asynchronous test
    asyncio.run(main_async_test())

    print("\n--- Ollama Client Test Finished ---")
