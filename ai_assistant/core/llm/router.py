from typing import Dict, List, Optional, Tuple
from ai_assistant import config
from ai_assistant.core.llm.provider import LLMProvider
from ai_assistant.core.llm.gemini_provider import GeminiProvider
from ai_assistant.core.llm.ollama_provider import OllamaProvider
from ai_assistant.core.llm.deepseek_provider import DeepseekProvider

class ModelRouter:
    """Routes tasks to the appropriate LLM provider and direct-call mode."""

    def __init__(self):
        self._providers: Dict[str, LLMProvider] = {
            "gemini": GeminiProvider(),
            "ollama": OllamaProvider(),
            "deepseek": DeepseekProvider()
        }
        self.model = getattr(config, "DEFAULT_MODEL", "deepseek-v4-pro")
        self.DEFAULT_MODEL = self.model

    def get_route(self, task_name: str) -> Tuple[LLMProvider, str, str, str]:
        """
        Returns (provider_instance, model_name, execution_mode, endpoint_url)
        """
        profiles = getattr(config, 'TASK_PROFILES', {})

        # Fallback to chat if specific task isn't configured
        profile = profiles.get(task_name) or profiles.get("chat") or {}

        provider_name = profile.get("provider", getattr(config, 'DEFAULT_LLM_PROVIDER', 'gemini'))

        # Fallbacks for specific missing attributes
        model = profile.get("model", getattr(config, 'DEFAULT_MODEL', 'gemini-2.5-flash-lite'))
        mode = profile.get("mode", "DIRECT")
        endpoint = profile.get("endpoint")

        provider = self._providers.get(provider_name.lower())

        # Absolute fallback if provider doesn't exist
        if not provider:
            provider = self._providers["gemini"]

        return provider, model, mode, endpoint

    async def generate_response(
        self,
        prompt: str,
        task_name: str = "chat",
        system_instruction: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        images: Optional[List[str]] = None,
    ) -> str:
        provider, routed_model, _, endpoint = self.get_route(task_name)
        return await provider.generate_response(
            prompt,
            system_instruction=system_instruction,
            history=history,
            model_name=model_name or routed_model,
            temperature=temperature,
            max_tokens=max_tokens,
            images=images,
            endpoint_url=endpoint,
        )

    async def invoke_ollama_model_async(
        self,
        prompt: str,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        task_name: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        """Compatibility method for legacy callers while they migrate."""
        return await self.generate_response(
            prompt,
            task_name=task_name or "coding",
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )

# Singleton instance
model_router = ModelRouter()
