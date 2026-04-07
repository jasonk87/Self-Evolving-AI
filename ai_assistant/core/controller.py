import logging
from typing import Dict, Any, Optional, Tuple, List
import asyncio

from ai_assistant.core.models.state import ExecutionState, ExecutionStatus
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async
from ai_assistant.core.orchestrator import DynamicOrchestrator

logger = logging.getLogger(__name__)

class SystemController:
    """
    Core Controller for the AI Assistant.

    Acts as the single entry point for incoming user requests. It instantiates the
    Unified State Pipeline (ExecutionState), routes the request through the
    DynamicOrchestrator, and guarantees a consistent, strongly-typed response
    back to the client UI.
    """

    def __init__(self, orchestrator: DynamicOrchestrator):
        self.orchestrator = orchestrator

    async def handle_user_request(
        self,
        prompt: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        session_id: Optional[str] = None,
        images: Optional[List[str]] = None,
        context_source: str = "USER"
    ) -> ExecutionState:
        """
        Processes a user request through the Unified State Pipeline.

        Args:
            prompt (str): The user's input message.
            conversation_history (List[Dict]): Historical chat context.
            session_id (str): The active session identifier.
            images (List[str]): Base64 encoded images.
            context_source (str): Indicates the source of the context (e.g., USER or SYSTEM).

        Returns:
            ExecutionState: The fully processed state object containing the final
                            answer, tools executed, and any errors encountered.
        """
        # 1. Initialize the Unified State Pipeline
        # We start by capturing the prompt and establishing initial context limits.
        state = ExecutionState(
            original_user_prompt=prompt,
            context_limits={"max_tokens": getattr(self.orchestrator, 'MAX_STRATEGIST_PROMPT_TOKENS', 120000)}
        )

        state.current_status = ExecutionStatus.PLANNING

        try:
            logger.info(f"SystemController: Routing request for session {session_id}")

            # 2. Analyze intent via TaskRouter
            from ai_assistant.core.router import TaskRouter
            from ai_assistant.core.enums import ExecutionMode
            router = TaskRouter()
            mode = await router.determine_mode(prompt)

            if mode == ExecutionMode.DIRECT:
                # Fast Path: Simple Chat/Q&A - Bypass heavy tool loops
                logger.info("SystemController: Fast Path (DIRECT mode) selected. Bypassing tool orchestrator.")
                state.current_status = ExecutionStatus.TOOL_EXECUTION

                # Format a minimal history
                hist_str = ""
                if conversation_history:
                    for msg in conversation_history[-3:]: # only last 3 for speed
                        hist_str += f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}\n"

                chat_prompt = f"User: {prompt}\n\nRespond conversationally as 'Weebo', the AI assistant. Do not use tools.\nHistory:\n{hist_str}"

                response = await invoke_gemini_model_async(chat_prompt, "chat")
                state.final_answer = response
                state.current_status = ExecutionStatus.COMPLETED
            else:
                # 3. Delegate to the Orchestrator for complex tasks
                # The orchestrator accepts and directly mutates the ExecutionState object.
                state = await self.orchestrator.process_prompt(
                    state=state,
                    conversation_history=conversation_history,
                    session_id=session_id,
                    images=images,
                    context_source=context_source
                )

        except Exception as e:
            logger.error(f"SystemController: Critical failure during execution: {e}", exc_info=True)
            state.current_status = ExecutionStatus.FAILED
            state.errors.append(f"Critical System Error: {str(e)}")

        return state
