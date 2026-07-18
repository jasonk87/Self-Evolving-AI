from typing import Dict, List, Optional
from ai_assistant.core.llm.provider import LLMProvider
from ai_assistant.llm_interface.deepseek_client import invoke_raw_deepseek_async

class DeepseekProvider(LLMProvider):
    async def generate_response(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        model_name: Optional[str] = "deepseek-chat",
        temperature: float = 0.7,
        max_tokens: int = 8192,
        images: Optional[List[str]] = None,
        endpoint_url: Optional[str] = None
    ) -> str:
        messages = []
        
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
            
        if history:
            for msg in history:
                role = msg.get("role", "user")
                # map roles if necessary, Deepseek supports system, user, assistant
                if role not in ["system", "user", "assistant"]:
                    role = "user"
                messages.append({"role": role, "content": msg.get("content", "")})
                
        messages.append({"role": "user", "content": prompt})

        # Note: images are not currently supported in basic deepseek-chat integration,
        # they would require deepseek-vl or specific vision API endpoint if supported.

        return await invoke_raw_deepseek_async(
            messages=messages,
            model_name=model_name or "deepseek-chat",
            temperature=temperature,
            max_tokens=max_tokens
        )

    @property
    def provider_name(self) -> str:
        return "deepseek"
