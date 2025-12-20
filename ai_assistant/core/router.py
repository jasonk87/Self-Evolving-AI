import logging
from typing import Optional, Dict, Any
from ai_assistant.core.enums import ExecutionMode
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async, invoke_split_brain_async
from ai_assistant.config import DEFAULT_MODEL

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """You are the AI Assistant's Execution Router. Your job is to classify the user's intent into one of three execution modes.

Modes:
1. DIRECT: Zero overhead. Simple input -> output. No tools, no complex logic.
   - Use for: Summarization, formatting, simple small talk, greetings, translation, simple Q&A without realtime data.
   - Examples: "Summarize this", "Fix typo", "Hello", "Translate this to Spanish".

2. FAST_REACT: Standard ReAct loop (Think -> Act -> Observe). Single-threaded, self-correcting.
   - Use for: Standard coding tasks, active chat, quick lookups, tool usage, debugging simple errors.
   - Examples: "Write a script", "Check weather", "Debug this error", "Find file X".

3. THINKING_PRO: Deep Iterative Branching. Heavy engine.
   - Use for: Complex architecture, deep debugging, high-risk tasks, refactoring entire systems, planning large projects.
   - Examples: "Design a system", "Refactor entire backend", "Research and plan a new feature".

Instructions:
- Analyze the user's prompt and context.
- Output ONLY the mode name: DIRECT, FAST_REACT, or THINKING_PRO.
- Do not output any other text.
"""

class TaskRouter:
    def __init__(self, llm_model: str = "gemini-2.0-flash"):
        self.llm_model = llm_model

    async def determine_mode(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> ExecutionMode:
        """
        Determines the execution mode based on the prompt and context.
        """
        # Quick heuristic checks for obvious cases to save latency
        prompt_lower = prompt.lower().strip()

        # Very simple greetings or small talk -> DIRECT
        if prompt_lower in ["hi", "hello", "hey", "hola", "ping"]:
            return ExecutionMode.DIRECT

        # If user explicitly asks for "think about" or "plan", might lean towards THINKING_PRO
        if "architect" in prompt_lower or "design system" in prompt_lower or "deep analysis" in prompt_lower:
            # We still let the LLM confirm, but this is a hint.
            pass

        try:
            # Construct the prompt for the router
            context_str = ""
            if context:
                # Summarize context if needed, for now just simple indicator
                context_str = f"\nContext Keys: {', '.join(context.keys())}"

            full_prompt = f"{ROUTER_PROMPT}\n\nUser Prompt: {prompt}{context_str}\n\nMode:"

            # Use Split Brain for smarter routing
            response, _ = await invoke_split_brain_async(
                prompt=full_prompt,
                model_name=self.llm_model,
                temperature=0.0, # Zero temp for deterministic classification
                max_tokens=200, # Allow enough tokens for Chain of Thought if present, though we ignore it
                context_text=f"Routing decision."
            )

            if response:
                mode_str = response.strip().upper()
                # Handle potential extra chars if LLM is chatty (though we asked it not to be)
                if "THINKING_PRO" in mode_str:
                    return ExecutionMode.THINKING_PRO
                elif "FAST_REACT" in mode_str:
                    return ExecutionMode.FAST_REACT
                elif "DIRECT" in mode_str:
                    return ExecutionMode.DIRECT

            # Default fallback if LLM response is unclear or fails
            logger.warning(f"Router LLM returned unclear response: {response}. Defaulting to FAST_REACT.")
            return ExecutionMode.FAST_REACT

        except Exception as e:
            logger.error(f"Error in TaskRouter: {e}. Defaulting to FAST_REACT.")
            return ExecutionMode.FAST_REACT
