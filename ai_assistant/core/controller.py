import logging
from typing import Any, Dict, Optional, List

from opentelemetry import trace
from ai_assistant.core.models.state import ExecutionState
from ai_assistant.core.orchestrator import DynamicOrchestrator
from ai_assistant.core.logging_config import correlation_id_var
from ai_assistant.utils.token_counter import estimate_tokens

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

class SystemController:
    """
    Core Controller for the AI Assistant.

    Acts as the single entry point for incoming user requests. It instantiates the
    Unified State Pipeline (ExecutionState), routes the request through the
    DynamicOrchestrator, and guarantees a consistent, strongly-typed response
    back to the client UI.
    """

    def __init__(self, orchestrator: DynamicOrchestrator, context_compressor: Optional[Any] = None):
        self.orchestrator = orchestrator
        self.context_compressor = context_compressor

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
            context_limits={"max_tokens": getattr(self.orchestrator, 'MAX_ACTION_PROMPT_TOKENS', 120000)}
        )

        # Inject the correlation ID from the ExecutionState into the ContextVar
        token = correlation_id_var.set(state.correlation_id)

        try:
            state.current_status = "planning"

            prepared_history = conversation_history
            if self.context_compressor:
                try:
                    prepared_history = await self.context_compressor.prepare_history(
                        session_id,
                        conversation_history,
                    )
                    state.context_limits["history_messages_before"] = len(conversation_history or [])
                    state.context_limits["history_messages_after"] = len(prepared_history or [])
                    state.context_limits["history_tokens_before"] = sum(
                        estimate_tokens(str(message.get("content") or "")) + 8
                        for message in conversation_history or []
                        if isinstance(message, dict)
                    )
                    state.context_limits["history_tokens_after"] = sum(
                        estimate_tokens(str(message.get("content") or "")) + 8
                        for message in prepared_history or []
                        if isinstance(message, dict)
                    )
                except Exception as compression_error:
                    logger.error(
                        "Context compression failed for session %s; using original history: %s",
                        session_id,
                        compression_error,
                        exc_info=True,
                    )
                    prepared_history = conversation_history

            with tracer.start_as_current_span("handle_user_request") as span:
                span.set_attribute("correlation_id", state.correlation_id)
                if session_id:
                    span.set_attribute("session_id", session_id)

                try:
                    logger.info(f"SystemController: Routing request for session {session_id}")

                    # 2. Delegate to the Orchestrator
                    # The orchestrator accepts and directly mutates the ExecutionState object.
                    state = await self.orchestrator.process_prompt(
                        state=state,
                        conversation_history=prepared_history,
                        session_id=session_id,
                        images=images,
                        context_source=context_source
                    )

                except Exception as e:
                    logger.error(f"SystemController: Critical failure during execution: {e}", exc_info=True)
                    span.record_exception(e)
                    state.current_status = "failed"
                    state.errors.append(f"Critical System Error: {str(e)}")

            return state
        finally:
            correlation_id_var.reset(token)
