from typing import Dict, Any, Tuple
from ai_assistant import config
from ai_assistant.core.llm.provider import LLMProvider
from ai_assistant.core.llm.gemini_provider import GeminiProvider
from ai_assistant.core.llm.ollama_provider import OllamaProvider

class ModelRouter:
    """Routes tasks to the appropriate LLM provider and mode based on config.TASK_PROFILES."""

    def __init__(self):
        self._providers: Dict[str, LLMProvider] = {
            "gemini": GeminiProvider(),
            "ollama": OllamaProvider()
        }

    def get_route(self, task_name: str) -> Tuple[LLMProvider, str, str, str]:
        """
        Returns (provider_instance, model_name, execution_mode, endpoint_url)
        """
        profiles = getattr(config, 'TASK_PROFILES', {})

        # Fallback to chat if specific task isn't configured
        profile = profiles.get(task_name) or profiles.get("chat") or {}

        provider_name = profile.get("provider", getattr(config, 'DEFAULT_LLM_PROVIDER', 'gemini'))

        # Fallbacks for specific missing attributes
        model = profile.get("model", getattr(config, 'DEFAULT_MODEL', 'gemini-2.0-flash'))
        mode = profile.get("mode", "BICAMERAL")
        endpoint = profile.get("endpoint")

        provider = self._providers.get(provider_name.lower())

        # Absolute fallback if provider doesn't exist
        if not provider:
            provider = self._providers["gemini"]

        return provider, model, mode, endpoint

# Singleton instance
model_router = ModelRouter()
