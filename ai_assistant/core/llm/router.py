import copy
import logging
from typing import Dict, List, Optional, Tuple
from ai_assistant import config
from ai_assistant.core.llm.provider import LLMProvider
from ai_assistant.core.llm.gemini_provider import GeminiProvider
from ai_assistant.core.llm.ollama_provider import OllamaProvider
from ai_assistant.core.llm.deepseek_provider import DeepseekProvider

logger = logging.getLogger(__name__)

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
        self._last_call: Dict[str, object] = {}

    def get_route(self, task_name: str) -> Tuple[LLMProvider, str, str, str]:
        """
        Returns (provider_instance, model_name, execution_mode, endpoint_url)
        """
        profiles = getattr(config, 'TASK_PROFILES', {})

        # Fallback to chat if specific task isn't configured
        profile = profiles.get(task_name) or {}

        provider_name = profile.get("provider", getattr(config, 'DEFAULT_LLM_PROVIDER', 'gemini'))

        # Fallbacks for specific missing attributes
        model = profile.get(
            "model",
            getattr(config, "TASK_MODELS", {}).get(task_name)
            or getattr(config, 'DEFAULT_MODEL', 'gemini-2.5-flash-lite'),
        )
        mode = profile.get("mode", "DIRECT")
        endpoint = profile.get("endpoint")

        provider = self._providers.get(provider_name.lower())

        # Absolute fallback if provider doesn't exist
        if not provider:
            provider = self._providers["gemini"]

        return provider, model, mode, endpoint

    def get_last_call_info(self) -> Dict[str, object]:
        """Return a safe copy of the most recent provider/model attempt."""
        return copy.deepcopy(self._last_call)

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
        provider, routed_model, mode, endpoint = self.get_route(task_name)
        selected_model = model_name or routed_model
        attempts = [(provider, selected_model, endpoint, False)]

        # Keep the configured primary route intact, but make transient provider
        # outages survivable. This does not change API-key configuration or the
        # user's selected DeepSeek models; it only provides a Gemini fallback.
        provider_name = getattr(provider, "provider_name", "").lower()
        if provider_name != "gemini":
            fallback_provider = self._providers.get("gemini")
            fallback_model = getattr(
                config, "GEMINI_VISION_FALLBACK_MODEL", "gemini-2.5-flash"
            )
            if images:
                fallback_model = getattr(
                    config, "GEMINI_VISION_FALLBACK_MODEL", fallback_model
                )
            if fallback_provider:
                # Route image requests directly to the vision-capable fallback;
                # this avoids sending unsupported image payloads to text-only
                # providers and makes the displayed model truthful.
                if images:
                    attempts = [(fallback_provider, fallback_model, None, True)]
                else:
                    attempts.append((fallback_provider, fallback_model, None, True))

        last_error = None
        for attempt_provider, attempt_model, attempt_endpoint, is_fallback in attempts:
            call_info = {
                "provider": getattr(attempt_provider, "provider_name", "unknown"),
                "model": attempt_model,
                "task": task_name,
                "mode": mode,
                "fallback": is_fallback,
            }
            self._last_call = call_info
            try:
                result = await attempt_provider.generate_response(
                    prompt,
                    system_instruction=system_instruction,
                    history=history,
                    model_name=attempt_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    images=images,
                    endpoint_url=attempt_endpoint,
                )
                if not isinstance(result, str) or not result.strip():
                    raise RuntimeError(
                        f"{call_info['provider']} returned an empty response"
                    )
                call_info["status"] = "success"
                self._last_call = call_info
                return result
            except Exception as exc:
                last_error = exc
                call_info["status"] = "failed"
                call_info["error"] = str(exc)[:240]
                self._last_call = call_info
                if not is_fallback:
                    logger.warning(
                        "Primary %s/%s failed for %s; trying fallback when available: %s",
                        call_info["provider"],
                        attempt_model,
                        task_name,
                        exc,
                    )

        raise last_error

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
