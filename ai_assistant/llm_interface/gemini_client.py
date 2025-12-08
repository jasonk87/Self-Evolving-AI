# ai_assistant/llm_interface/gemini_client.py
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import os
import json
import asyncio
from typing import Optional, Dict, List, Any, Union, Tuple
from ai_assistant.config import (
    DEFAULT_MODEL,
    GEMINI_API_KEY,
    is_debug_mode,
    ENABLE_THINKING,
    THINKING_SUPPORTED_MODELS,
    ENABLE_CHAIN_OF_THOUGHT,
    DEFAULT_TEMPERATURE_THINKING,
    DEFAULT_TEMPERATURE_RESPONSE,
    THINKING_CONFIG
)
from ai_assistant.debugging.resilience import retry_with_backoff

# Configure Gemini API
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("WARNING: GEMINI_API_KEY is not set in config or environment variables.")

# Safety settings - allow most content as this is a dev tool
SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

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

def process_llm_response(text: str) -> Optional[Tuple[str, Optional[str]]]:
    if not text:
        return None
    # Gemini 2.0 Flash Exp doesn't natively return separate thinking/content fields in the same way
    # some Ollama models do via JSON, unless we prompt for it specifically.
    # For now, we assume the model returns the content directly.
    # If we implemented CoT manually, we handle it separately.
    return (text, None)

def _prepare_gemini_history(
    messages_history: List[Dict[str, str]],
    prompt: str
) -> Tuple[List[Dict[str, Any]], Optional[str], str]:
    """
    Prepares the history, system instruction, and current message for Gemini.

    Args:
        messages_history: The full history of messages including the last user message.
        prompt: The system instruction (in legacy Ollama usage) or user prompt (if no history).

    Returns:
        Tuple containing:
        - chat_history: List of messages for Gemini history (excluding the last one).
        - system_instruction: The combined system instruction.
        - current_message_content: The content of the last message to be sent now.
    """
    chat_history = []
    system_instruction_parts = []

    # In legacy Ollama usage, 'prompt' is the system instruction when history is present.
    if prompt and prompt.strip():
        system_instruction_parts.append(prompt)

    current_message_content = "..." # Fallback

    # We need to split history into past history and current message
    if not messages_history:
        # Should not happen if this function is called when messages_history is truthy
        return [], "\n".join(system_instruction_parts) if system_instruction_parts else None, prompt

    # The last message is the one we want to send now
    current_msg_dict = messages_history[-1]
    past_history = messages_history[:-1]

    current_message_content = current_msg_dict.get("content", "")

    for msg in past_history:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            system_instruction_parts.append(content)
        elif role == "user":
            chat_history.append({"role": "user", "parts": [content]})
        elif role == "assistant":
            chat_history.append({"role": "model", "parts": [content]})

    system_instruction = "\n".join(system_instruction_parts) if system_instruction_parts else None

    return chat_history, system_instruction, current_message_content


@retry_with_backoff(retries=3, base_delay=1.0, max_delay=10.0, jitter=True)
def invoke_gemini_model(
    prompt: str,
    model_name: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    messages_history: Optional[List[Dict[str, str]]] = None
) -> Optional[str]:

    if not GEMINI_API_KEY:
        print("Error: Gemini API key not configured.")
        return None

    enable_thinking = ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS
    enable_chain_of_thought = ENABLE_CHAIN_OF_THOUGHT and not enable_thinking and not messages_history

    try:
        model_kwargs = {}
        # Chain of thought logic for single prompt
        if enable_chain_of_thought:
            model = genai.GenerativeModel(model_name)
            thinking_prompt_text = THINKING_PROMPT_TEMPLATE.format(user_prompt=prompt)

            thinking_response = model.generate_content(
                thinking_prompt_text,
                generation_config=genai.types.GenerationConfig(temperature=DEFAULT_TEMPERATURE_THINKING, max_output_tokens=max_tokens),
                safety_settings=SAFETY_SETTINGS
            )
            thinking_result = thinking_response.text.strip()

            response_prompt_text = RESPONSE_WITH_THINKING_PROMPT_TEMPLATE.format(thinking_process=thinking_result, user_prompt=prompt)
            final_response = model.generate_content(
                response_prompt_text,
                generation_config=genai.types.GenerationConfig(temperature=DEFAULT_TEMPERATURE_RESPONSE, max_output_tokens=max_tokens),
                safety_settings=SAFETY_SETTINGS
            )
            return final_response.text.strip()

        # Normal execution
        if messages_history:
            chat_history, system_instruction, current_message = _prepare_gemini_history(messages_history, prompt)

            if system_instruction:
                 model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
            else:
                 model = genai.GenerativeModel(model_name)

            chat = model.start_chat(history=chat_history)
            response = chat.send_message(
                current_message,
                generation_config=genai.types.GenerationConfig(temperature=temperature, max_output_tokens=max_tokens if max_tokens > 0 else None),
                safety_settings=SAFETY_SETTINGS
            )
            return response.text.strip()
        else:
            # Single prompt
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=temperature, max_output_tokens=max_tokens if max_tokens > 0 else None),
                safety_settings=SAFETY_SETTINGS
            )
            return response.text.strip()

    except Exception as e:
        print(f"Error invoking Gemini model '{model_name}': {e}")
        return None

async def invoke_gemini_model_async_internal(
    prompt: str,
    model_name: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    messages_history: Optional[List[Dict[str, str]]] = None
) -> Optional[str]:
    # Async wrapper for Gemini.
    if not GEMINI_API_KEY:
        print("Error: Gemini API key not configured.")
        return None

    enable_thinking = ENABLE_THINKING and model_name in THINKING_SUPPORTED_MODELS
    enable_chain_of_thought = ENABLE_CHAIN_OF_THOUGHT and not enable_thinking and not messages_history

    try:
        if enable_chain_of_thought:
            model = genai.GenerativeModel(model_name)
            thinking_prompt_text = THINKING_PROMPT_TEMPLATE.format(user_prompt=prompt)

            thinking_response = await model.generate_content_async(
                thinking_prompt_text,
                generation_config=genai.types.GenerationConfig(temperature=DEFAULT_TEMPERATURE_THINKING, max_output_tokens=max_tokens),
                safety_settings=SAFETY_SETTINGS
            )
            thinking_result = thinking_response.text.strip()

            response_prompt_text = RESPONSE_WITH_THINKING_PROMPT_TEMPLATE.format(thinking_process=thinking_result, user_prompt=prompt)
            final_response = await model.generate_content_async(
                response_prompt_text,
                generation_config=genai.types.GenerationConfig(temperature=DEFAULT_TEMPERATURE_RESPONSE, max_output_tokens=max_tokens),
                safety_settings=SAFETY_SETTINGS
            )
            return final_response.text.strip()

        # Normal execution
        if messages_history:
            chat_history, system_instruction, current_message = _prepare_gemini_history(messages_history, prompt)

            if system_instruction:
                 model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
            else:
                 model = genai.GenerativeModel(model_name)

            chat = model.start_chat(history=chat_history)
            response = await chat.send_message_async(
                current_message,
                generation_config=genai.types.GenerationConfig(temperature=temperature, max_output_tokens=max_tokens if max_tokens > 0 else None),
                safety_settings=SAFETY_SETTINGS
            )
            return response.text.strip()
        else:
            model = genai.GenerativeModel(model_name)
            response = await model.generate_content_async(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=temperature, max_output_tokens=max_tokens if max_tokens > 0 else None),
                safety_settings=SAFETY_SETTINGS
            )
            return response.text.strip()

    except Exception as e:
        print(f"Error invoking Gemini model '{model_name}' (async): {e}")
        if is_debug_mode():
             import traceback
             traceback.print_exc()
        return None

invoke_gemini_model_async = retry_with_backoff(retries=3, base_delay=1.0, max_delay=10.0, jitter=True)(invoke_gemini_model_async_internal)


class GeminiProvider:
    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model = model_name

    async def invoke_ollama_model_async(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1500,
        messages_history: Optional[List[Dict[str, str]]] = None
    ) -> Optional[str]:
        # Alias for compatibility with code expecting OllamaProvider
        return await invoke_gemini_model_async(
            prompt=prompt,
            model_name=model_name or self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages_history=messages_history
        )

    def invoke_ollama_model(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1500,
        messages_history: Optional[List[Dict[str, str]]] = None
    ) -> Optional[str]:
        # Alias for compatibility
        return invoke_gemini_model(
            prompt=prompt,
            model_name=model_name or self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages_history=messages_history
        )

    async def list_models_async(self) -> List[Dict[str, Any]]:
        try:
             # GenAI SDK list_models returns an iterator
             models = []
             for m in genai.list_models():
                 if 'generateContent' in m.supported_generation_methods:
                     models.append({"name": m.name, "modified_at": "N/A", "size": 0, "digest": "N/A"})
             return models
        except Exception as e:
            print(f"Error listing Gemini models: {e}")
            return []
