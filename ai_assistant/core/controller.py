import logging
from typing import Dict, Any, Optional, Tuple, List
import asyncio

from ai_assistant.core.models.state import ExecutionState
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

        state.current_status = "planning"

        try:
            logger.info(f"SystemController: Routing request for session {session_id}")

            # 2. Delegate to the Orchestrator
            # Temporarily, we wrap the existing orchestrator method. In the future,
            # the orchestrator will natively accept and mutate this ExecutionState object.
            success, response_message, collected_images = await self.orchestrator.process_prompt(
                prompt=prompt,
                conversation_history=conversation_history,
                session_id=session_id,
                images=images,
                context_source=context_source
            )

            # 3. Update the State based on the outcome
            if success:
                state.current_status = "completed"
            else:
                state.current_status = "failed"
                state.errors.append("Orchestrator reported failure.")

            # Since the orchestrator currently returns a string, we store it in tool_results
            # as a synthetic final output for now. When the orchestrator is refactored,
            # it will populate state.tool_results directly.
            state.tool_results.append({
                "action_name": "orchestrator_final_answer",
                "success": success,
                "result": response_message,
                "collected_images": collected_images
            })

        except Exception as e:
            logger.error(f"SystemController: Critical failure during execution: {e}", exc_info=True)
            state.current_status = "failed"
            state.errors.append(f"Critical System Error: {str(e)}")

        return state
