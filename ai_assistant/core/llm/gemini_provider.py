from typing import Dict, Any, List, Optional
from ai_assistant.core.llm.provider import LLMProvider
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async

class GeminiProvider(LLMProvider):
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
        # Wrap the existing gemini client functionality
        return await invoke_gemini_model_async(
            prompt=prompt,
            system_instruction=system_instruction,
            history=history,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            images=images
        )

    @property
    def provider_name(self) -> str:
        return "gemini"
