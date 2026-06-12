from typing import Dict, List, Optional
from ai_assistant.core.llm.provider import LLMProvider
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async

class OllamaProvider(LLMProvider):
    async def generate_response(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        images: Optional[List[str]] = None,
        endpoint_url: Optional[str] = None
    ) -> str:
        # Wrap the existing ollama client functionality
        # Merge system instructions and history into prompt for Ollama since its signature is simpler
        combined_prompt = prompt
        if system_instruction:
             combined_prompt = f"{system_instruction}\n\n{prompt}"

        # Optional: Append history as context
        if history:
             hist_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history])
             combined_prompt = f"Previous Conversation:\n{hist_str}\n\n{combined_prompt}"

        response = await invoke_ollama_model_async(
            prompt=combined_prompt,
            model_name=model_name or "llama3",
            temperature=temperature,
            api_endpoint_override=endpoint_url
        )
        return response or ""

    @property
    def provider_name(self) -> str:
        return "ollama"
