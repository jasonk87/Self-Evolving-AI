# ai_assistant/llm_interface/ollama_client.py
# This file is now a compatibility wrapper for the Gemini Client.
# It re-exports symbols from gemini_client.py using their original Ollama names
# to maintain backward compatibility with the rest of the codebase.

from ai_assistant.llm_interface.gemini_client import (
    invoke_gemini_model as invoke_ollama_model,
    invoke_gemini_model_async as invoke_ollama_model_async,
    GeminiProvider as OllamaProvider,
    process_llm_response # Re-export utility if needed
)

# Retain constants if they are used elsewhere (though config.py handles most)
# OLLAMA_API_ENDPOINT = ... # Not relevant for Gemini
DEFAULT_OLLAMA_MODEL = "gemini-2.0-flash-exp" # Updated default

# Re-exporting functions for verifying imports
__all__ = [
    "invoke_ollama_model",
    "invoke_ollama_model_async",
    "OllamaProvider",
    "process_llm_response",
    "DEFAULT_OLLAMA_MODEL"
]

if __name__ == '__main__':
    import asyncio
    print("--- Testing Gemini Client via Ollama Wrapper ---")

    async def test():
        provider = OllamaProvider()
        response = await provider.invoke_ollama_model_async("Hello, are you Gemini?")
        print(f"Response: {response}")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    loop.run_until_complete(test())
