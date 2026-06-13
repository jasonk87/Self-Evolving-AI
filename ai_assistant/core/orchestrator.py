"""Dynamic orchestrator for handling complex user interactions."""

import re
import os
import sys

# Ensure project root is in sys.path for stand-alone execution
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Tuple

from ai_assistant.core.router import TaskRouter
import ai_assistant.config as config
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async
from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.utils.display_utils import CLIColors, color_text
from ai_assistant.core.events import EventEmitter
from ai_assistant.core.tool_lifecycle import mark_tool_quarantined
from ai_assistant.llm_interface.exceptions import BudgetExceededError
from ai_assistant.core.models.state import ExecutionState

# Legacy imports to keep signature compatible
from ..planning.planning import PlannerAgent
from ..planning.execution import ExecutionAgent 
from ..learning.learning import LearningAgent
from ..execution.action_executor import ActionExecutor
from .task_manager import TaskManager, ActiveTaskStatus, ActiveTaskType
from .notification_manager import NotificationManager
from ..planning.hierarchical_planner import HierarchicalPlanner
from ai_assistant.memory.episodic_manager import EpisodicMemoryManager
from ai_assistant.utils.token_counter import estimate_tokens, truncate_to_token_limit
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Constants
MAX_REACT_STEPS = 10
MAX_ACTION_PROMPT_TOKENS = 120000  # Conservative limit, Gemini handles more but optimizing costs


@dataclass
class AnswerQualityGateResult:
    accepted: bool
    reason: str
    retry_observation: Optional[str] = None
    answered_request: bool = True
    used_available_context: bool = True
    unresolved_uncertainty: bool = False
    should_retrieve_more_context: bool = False
    too_generic: bool = False


class AnswerQualityGate:
    """Deterministic first-pass quality gate for final answers in the ReAct loop."""

    _EVIDENCE_SEEKING_TERMS = {
        "check", "look", "find", "search", "read", "list", "run", "open",
        "inspect", "verify", "current", "latest", "today", "status", "health",
        "file", "files", "project", "github", "branch", "test", "tests",
        "image", "photo", "screenshot", "screen", "why", "what happened",
        "what is going on", "how many",
    }
    _UNCERTAINTY_TERMS = {
        "i don't know", "i do not know", "not sure", "maybe", "probably",
        "i can't tell", "cannot tell", "don't have enough", "do not have enough",
    }
    _GENERIC_ANSWERS = {
        "ok", "okay", "sure", "done", "got it", "working on it", "i'll check",
        "i will check", "i'll look into it", "i will look into it", "sounds good",
        "task completed", "completed",
    }
    _CASUAL_CONVERSATION_TERMS = {
        "hi", "hello", "hey", "thanks", "thank you", "appreciate it",
        "good morning", "good afternoon", "good evening", "how are you",
        "what's up", "whats up",
    }
    _SUBSTANTIVE_REQUEST_TERMS = {
        "explain", "summarize", "compare", "write", "draft", "create", "build",
        "fix", "debug", "review", "analyze", "plan", "design", "implement",
        "tell me", "help me", "show me",
    }

    def evaluate(
        self,
        *,
        user_prompt: str,
        answer: str,
        context: str,
        execution_history: str,
        remaining_cycles: int,
    ) -> AnswerQualityGateResult:
        prompt_text = self._normalize(user_prompt)
        answer_text = self._normalize(answer)
        answer_key = re.sub(r"[^a-z0-9']+", " ", answer_text).strip()
        has_context = bool(str(context or "").strip())
        has_tool_observation = "result:" in self._normalize(execution_history)
        has_evidence = has_context or has_tool_observation
        needs_evidence = any(term in prompt_text for term in self._EVIDENCE_SEEKING_TERMS)
        casual_conversation = any(term in prompt_text for term in self._CASUAL_CONVERSATION_TERMS)
        substantive_request = any(term in prompt_text for term in self._SUBSTANTIVE_REQUEST_TERMS)
        unresolved = any(term in answer_text for term in self._UNCERTAINTY_TERMS)
        word_count = len(answer_text.split())
        too_generic = (
            answer_key in self._GENERIC_ANSWERS
            or (word_count <= 3 and needs_evidence and not has_evidence)
            or any(answer_key.startswith(term) for term in self._GENERIC_ANSWERS if len(term.split()) > 1)
        )

        if not answer_text:
            return self._reject("empty_final_answer", "Final answer was empty.")

        if needs_evidence and not has_evidence:
            return self._reject(
                "context_or_tool_needed",
                "The user request appears to require tool/context evidence before answering.",
                answered_request=not too_generic,
                used_available_context=False,
                should_retrieve_more_context=True,
                too_generic=too_generic,
            )

        if unresolved and needs_evidence and remaining_cycles > 0:
            return self._reject(
                "unresolved_uncertainty",
                "The answer still contains unresolved uncertainty and more cycles are available.",
                unresolved_uncertainty=True,
                should_retrieve_more_context=True,
                too_generic=too_generic,
            )

        if too_generic and not has_evidence and substantive_request and not casual_conversation:
            return self._reject(
                "too_generic",
                "The answer is too generic for the user's request.",
                answered_request=False,
                used_available_context=has_context,
                too_generic=True,
            )

        return AnswerQualityGateResult(
            accepted=True,
            reason="accepted",
            answered_request=True,
            used_available_context=has_evidence or not needs_evidence,
            unresolved_uncertainty=unresolved,
            should_retrieve_more_context=False,
            too_generic=too_generic,
        )

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(str(text or "").casefold().strip().split())

    @staticmethod
    def _reject(
        reason: str,
        observation: str,
        *,
        answered_request: bool = False,
        used_available_context: bool = False,
        unresolved_uncertainty: bool = False,
        should_retrieve_more_context: bool = False,
        too_generic: bool = False,
    ) -> AnswerQualityGateResult:
        return AnswerQualityGateResult(
            accepted=False,
            reason=reason,
            retry_observation=(
                f"AnswerQualityGate rejected the final answer: {observation} "
                "Continue the ReAct loop. Use an appropriate tool or available context before finalizing."
            ),
            answered_request=answered_request,
            used_available_context=used_available_context,
            unresolved_uncertainty=unresolved_uncertainty,
            should_retrieve_more_context=should_retrieve_more_context,
            too_generic=too_generic,
        )

class DynamicOrchestrator:
    """
    Orchestrates the dynamic planning and execution of user prompts.
    Uses a direct single-call ReAct loop.
    """

    def __init__(self, 
                 planner: PlannerAgent,
                 executor: ExecutionAgent,
                 learning_agent: LearningAgent,
                 action_executor: ActionExecutor,
                 task_manager: Optional[TaskManager] = None,
                 notification_manager: Optional[NotificationManager] = None,
                 hierarchical_planner: Optional[HierarchicalPlanner] = None,
                 memory_manager: Optional[Any] = None):

        self.planner = planner
        self.executor = executor
        self.learning_agent = learning_agent
        self.action_executor = action_executor
        self.task_manager = task_manager
        self.notification_manager = notification_manager
        self.hierarchical_planner = hierarchical_planner
        self.memory_manager = memory_manager
        self.episodic_manager = EpisodicMemoryManager()

        # Inject memory manager into planners if not already set
        if self.planner and self.memory_manager and hasattr(self.planner, 'memory_manager') and self.planner.memory_manager is None:
            self.planner.memory_manager = self.memory_manager

        if self.hierarchical_planner and self.memory_manager:
            self.hierarchical_planner.memory_manager = self.memory_manager

        self.router = TaskRouter()
        self.context: Dict[str, Any] = {}
        self.current_goal: Optional[str] = None
        self.current_plan: Optional[List[Dict[str, Any]]] = None
        self.blocked_tools: Dict[str, Dict[str, Any]] = {}
        self.failure_counts: Dict[str, int] = {}

        self.quarantine_file = os.path.join(project_root, 'data', 'quarantine_state.json')
        self._load_quarantine_state()

    def _load_quarantine_state(self):
        """Loads persistent quarantine state."""
        try:
            if os.path.exists(self.quarantine_file):
                with open(self.quarantine_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.blocked_tools = state.get("blocked_tools", {})
                    self.failure_counts = state.get("failure_counts", {})
                    for alias in ("google_search", "search_web", "web_search"):
                        self.blocked_tools.pop(alias, None)
                    self.failure_counts = {
                        key: value
                        for key, value in self.failure_counts.items()
                        if not key.startswith(("google_search|", "search_web|", "web_search|"))
                    }
        except Exception as e:
            logger.error(f"Failed to load quarantine state: {e}")

    def _save_quarantine_state(self):
        """Saves persistent quarantine state."""
        try:
            os.makedirs(os.path.dirname(self.quarantine_file), exist_ok=True)
            with open(self.quarantine_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "blocked_tools": self.blocked_tools,
                    "failure_counts": self.failure_counts
                }, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save quarantine state: {e}")

    async def process_prompt(self, state: ExecutionState, conversation_history: Optional[List[Dict[str, str]]] = None, session_id: Optional[str] = None, images: Optional[List[str]] = None, context_source: str = "USER") -> ExecutionState:
        """
        Process a user prompt using the direct ReAct architecture.
        Accepts and mutates an ExecutionState object.
        """
        with tracer.start_as_current_span("orchestrator.process_prompt") as span:
            span.set_attribute("original_prompt_length", len(state.original_user_prompt))
            return await self._process_prompt_internal(state, conversation_history, session_id, images, context_source)

    async def _process_prompt_internal(self, state: ExecutionState, conversation_history: Optional[List[Dict[str, str]]], session_id: Optional[str], images: Optional[List[str]], context_source: str) -> ExecutionState:
        try:
            self.current_goal = state.original_user_prompt
            
            # 1. Vision Analysis (Common for all modes if images exist)
            prompt_with_context = await self._enrich_prompt_with_vision(state.original_user_prompt, images)

            # 2. Context Gathering (RAG, Project Context)
            full_context_str, context_metadata = await self._gather_context(prompt_with_context)

            logger.info(f"DynamicOrchestrator: Starting direct ReAct cycle for prompt: {state.original_user_prompt[:50]}...")
            print(color_text("--> Strategy: Direct ReAct", CLIColors.SYSTEM_MESSAGE))

            # 3. Execute direct ReAct cycle
            await self._execute_universal_cycle(state, prompt_with_context, full_context_str, conversation_history, session_id, context_source, images)
            return state

        except BudgetExceededError as e:
            logger.warning(f"Budget exceeded: {e}")
            badge = '<span class="ai-metric ai-status-bad">Daily Budget Exceeded</span>' if "Daily" in str(e) else '<span class="ai-metric ai-status-warning">Category Budget Exceeded</span>'
            html = f"""```html-dynamic
            <div class="ai-card">
              <h3>System Alert</h3>
              <p>{badge} {str(e)}</p>
              <p>LLM execution has been halted to prevent further charges.</p>
            </div>
            ```"""
            # We bypass the LLM for rephrasing here because the LLM is blocked!
            state.current_status = "failed"
            state.errors.append("Budget Exceeded")
            state.tool_results.append({
                "action_name": "orchestrator_final_answer",
                "success": False,
                "result": f"I cannot complete your request because the system budget has been reached.\n{html}"
            })
            return state
        except Exception as e:
            logger.error(f"Error in process_prompt: {e}", exc_info=True)
            state.current_status = "failed"
            state.errors.append(f"An unexpected error occurred: {str(e)}")
            return state

        finally:
            # 5. Session Level Summarization (Rolling)
            if session_id and self.task_manager:
                 try:
                     asyncio.create_task(self._update_session_summary(session_id, conversation_history))
                 except Exception as e:
                     logger.error(f"Failed to trigger session summary: {e}")

    def _build_ui_feedback_task_details(self) -> Dict[str, Any]:
        """Builds a valid scope-contract payload for ephemeral UI feedback tasks."""
        return {
            "scope_type": "session",
            "capability_profile": "ops_diagnostics",
            "retention_policy": "keep_summary_only",
            "worker_profile": "ops_assistant_worker",
            "source": "ui_feedback",
            "execution_surface": "chat_tool_cycle",
        }

    def _build_collected_image(self, image: Any, source_tool: str) -> Dict[str, Any]:
        """Attach provenance to images returned by tools before they reach chat."""
        if isinstance(image, dict):
            normalized = dict(image)
            normalized.setdefault("source", source_tool)
            normalized.setdefault("label", self._image_label_for_source(source_tool))
            return normalized
        return {
            "src": image,
            "source": source_tool,
            "label": self._image_label_for_source(source_tool),
        }

    @staticmethod
    def _image_label_for_source(source_tool: str) -> str:
        if source_tool in {"take_screenshot", "analyze_visuals"}:
            return "Visual Capture"
        if source_tool in {"search_web", "search_google_first", "google_search", "search_duckduckgo", "web_search"}:
            return "Search Capture"
        if source_tool in {"web_search_images"}:
            return "Image Result"
        return "Tool Image"

    def _build_tool_failure_signature(self, tool_name: str, error: Exception) -> str:
        """Build a compact signature for repetitive tool failures in a single cycle run."""
        err_type = type(error).__name__
        err_msg = str(error or "")[:120]
        return f"{tool_name}|{err_type}|{err_msg}"

    def get_blocked_tools(self) -> Dict[str, Dict[str, Any]]:
        """Return the current list of blocked tools, respecting cooldowns."""
        self._check_cooldowns()
        return self.blocked_tools

    def unblock_tool(self, tool_name: str) -> bool:
        """Manually unblock a quarantined tool."""
        if tool_name in self.blocked_tools:
            del self.blocked_tools[tool_name]

            # Optionally clear failure counts associated with this tool
            keys_to_delete = [k for k in self.failure_counts if k.startswith(f"{tool_name}|")]
            for k in keys_to_delete:
                del self.failure_counts[k]

            self._save_quarantine_state()
            EventEmitter.emit("quarantine_update", {"blocked_tools": self.blocked_tools})
            return True
        return False

    def _check_cooldowns(self, cooldown_seconds: int = 900):
        """Check if any quarantined tools have passed their cooldown period (default 15 mins)."""
        import time
        now = time.time()
        expired_tools = []
        for tool_name, info in self.blocked_tools.items():
            if now - info.get("timestamp", 0) > cooldown_seconds:
                expired_tools.append(tool_name)

        for tool_name in expired_tools:
            del self.blocked_tools[tool_name]

        if expired_tools:
            self._save_quarantine_state()
            EventEmitter.emit("quarantine_update", {"blocked_tools": self.blocked_tools})

    def _register_tool_failure(
        self,
        tool_name: str,
        error: Exception,
        threshold: Optional[int] = None,
        context_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Track repeated failures and activate a per-tool circuit breaker when threshold is hit."""
        # Detect parameter validation errors
        error_type_name = type(error).__name__
        if error_type_name == "ToolValidationError" or "ToolValidationError" in str(type(error)):
            return {
                "signature": f"{tool_name}|Validation|{error}",
                "count": 0,
                "activated": False,
                "blocked_reason": None,
            }

        if threshold is None:
            threshold = config.QUARANTINE_FAILURE_THRESHOLD
        signature = self._build_tool_failure_signature(tool_name, error)
        count = self.failure_counts.get(signature, 0) + 1
        self.failure_counts[signature] = count

        import time
        activated = False
        if count >= threshold:
            activated = True
            if tool_name not in self.blocked_tools:
                reason_str = f"Repeated identical failure ({count}x): {signature}"
                self.blocked_tools[tool_name] = {
                    "signature": signature,
                    "count": count,
                    "reason": reason_str,
                    "timestamp": time.time(),
                    "context_data": context_data or {}
                }
                self._save_quarantine_state()
                EventEmitter.emit("quarantine_update", {"blocked_tools": self.blocked_tools})
                mark_tool_quarantined(
                    tool_name,
                    reason=reason_str,
                    error_signature=signature,
                    metadata={"count": count, "context_data": context_data or {}},
                )

                # Proactive Self-Healing Trigger
                if getattr(self, 'learning_agent', None):
                    try:
                        from ai_assistant.core.reflection import ActionableInsight, InsightType
                        insight = ActionableInsight(
                            type=InsightType.TOOL_BUG_SUSPECTED,
                            description=f"Tool '{tool_name}' was quarantined automatically by the circuit breaker due to repeated failures. Error signature: {signature}",
                            source_reflection_entry_ids=[],
                            related_tool_name=tool_name,
                            priority=1 # Highest priority
                        )
                        self.learning_agent.add_insight(insight)
                        logger.info(f"Triggered Proactive Self-Healing Insight for quarantined tool: {tool_name}")
                    except Exception as he:
                        logger.error(f"Failed to generate self-healing insight for {tool_name}: {he}")
            else:
                 self.blocked_tools[tool_name]["count"] = count
                 self._save_quarantine_state()

        return {
            "signature": signature,
            "count": count,
            "activated": activated,
            "blocked_reason": self.blocked_tools.get(tool_name, {}).get("reason"),
        }

    async def _execute_universal_cycle(self, state: ExecutionState, prompt: str, context: str, history: Optional[List[Dict[str, str]]], session_id: Optional[str], context_source: str, initial_images: Optional[List[str]]) -> None:
        """
        Direct ReAct cycle: one model call chooses the next tool call or final answer.
        Mutates ExecutionState.
        """
        with tracer.start_as_current_span("orchestrator._execute_universal_cycle"):
            await self._execute_universal_cycle_internal(state, prompt, context, history, session_id, context_source, initial_images)

    async def _execute_universal_cycle_internal(self, state: ExecutionState, prompt: str, context: str, history: Optional[List[Dict[str, str]]], session_id: Optional[str], context_source: str, initial_images: Optional[List[str]]) -> None:
        # Step A: Recall Failures
        failure_warning = await self.episodic_manager.recall_failures(state.original_user_prompt)
        if failure_warning:
            print(color_text(f"--> Episodic Memory: {failure_warning}", CLIColors.WARNING))
            context = f"{failure_warning}\n\n{context}"

        current_steps = []
        max_steps = MAX_REACT_STEPS
        # Tool-returned images are assistant artifacts. User-supplied initial images
        # are already stored on the user message and should not be echoed back as a
        # misleading assistant "live step".
        collected_images = []
        tools_desc = tool_system_instance.get_tools_description()

        execution_history = ""
        final_answer = ""
        success = False
        answer_quality_gate = AnswerQualityGate()
        cycle_metadata: List[Dict[str, Any]] = []

        # Define persona guidance based on context_source
        persona_guide = ""
        if context_source == "SYSTEM":
            persona_guide = (
                "MODE: SYSTEM TASK. You are running as a background process. Do NOT be conversational. "
                "Be technical, concise, and results-oriented. For a final answer, include params.outcome "
                "as either completed or failed. Use failed when the requested work was not completed. "
                "Treat each assigned mission as one-shot work. Do not claim continuous monitoring, "
                "scheduled work, or an ongoing search unless a durable scheduler was actually configured. "
                "For research tasks, report useful verified partial results and identify missing details. "
                "Do not fail a research task solely because some requested fields could not be verified."
            )
        else:
            persona_guide = (
                "MODE: USER CHAT. You are 'Weebo', a personal AI assistant (inspired by Flubber). "
                "You are NOT a robot. Be witty, casual, proactive, and extremely conversational. "
                "Avoid generic AI phrases like 'I understand' or 'As an AI'.\n"
                "CORE DIRECTIVE: When asked to perform ongoing tracking, heavy data processing, or deep code auditing, "
                "do NOT execute it directly. Instead, automatically spawn a user-scoped persistent agent to handle "
                "the task in the background, and report back to the user when you have received their payload. Check your roster first using list_active_agents. "
                "A roster workspace marked available is NOT an active task. Never say an agent is working unless a tool result includes a queued or running durable goal. "
                "To assign an available persistent workspace, call wake_agent. To create a new background goal, call spawn_background_agent."
            )

        # Create ephemeral task for UI feedback
        current_ui_task = None
        if self.task_manager and session_id:
            try:
                # Use EPHEMERAL_AGENT_TASK or relevant type
                current_ui_task = self.task_manager.add_task(
                    description=prompt[:100], # Short desc
                    task_type=ActiveTaskType.AGENT_TOOL_EXECUTION,
                    details=self._build_ui_feedback_task_details(),
                    session_id=session_id
                )
                self.task_manager.update_task_status(
                    current_ui_task.task_id, 
                    ActiveTaskStatus.PLANNING, 
                    step_desc="Analyzing request..."
                )
            except Exception as e:
                logger.warning(f"Failed to create UI feedback task: {e}")

        try:
            import uuid
            # Generate a base session node representing the goal
            root_node_id = f"thought_root_{uuid.uuid4().hex[:8]}"
            EventEmitter.emit("thought_update", {
                "node_id": root_node_id,
                "parent_id": None,
                "role": "Critique Request",
                "thought": prompt,
                "cycle": 0
            })
            last_node_id = root_node_id

            # We loop through cycles
            for step_i in range(max_steps):
                print(color_text(f"\n--- Cycle {step_i+1}: Direct ReAct ---", CLIColors.THOUGHT))
                
                if current_ui_task:
                    self.task_manager.update_task_status(
                        current_ui_task.task_id,
                        ActiveTaskStatus.PLANNING,
                        step_desc=f"Working (Cycle {step_i+1})...",
                        progress=min(90, step_i * 10)
                    )

                # Format Chat History - Truncate if insanely large
                chat_history_str = ""
                if history:
                    for msg in history:
                        role = msg.get('role', 'unknown').upper()
                        content = str(msg.get('content', ''))
                        chat_history_str += f"{role}: {content}\n"

                    # Optimizing chat history to prevent context explosion on massive tasks
                    history_tokens = estimate_tokens(chat_history_str)
                    if history_tokens > 40000:
                        chat_history_str = truncate_to_token_limit(chat_history_str, 40000)
                        logger.warning(f"Chat history truncated. Was ~{history_tokens} tokens.")
                
                # Expose Quarantined Tools
                quarantine_info = ""
                quarantined_tools = self.get_blocked_tools()
                if quarantined_tools:
                    q_list = ", ".join([f"'{t}'" for t in quarantined_tools.keys()])
                    quarantine_info = f"\n[CRITICAL WARNING]: The following tools are currently QUARANTINED due to repeated failures: {q_list}. DO NOT attempt to use them. You MUST find an alternative approach or report the blockage to the user.\n"

                state.current_status = "planning"
                # Construct base prompt and enforce absolute limits
                action_prompt = f"""You are a tool-capable assistant. Decide the next action for this request in one step.
Goal: {state.original_user_prompt}
{persona_guide}
{quarantine_info}
Context:
{context}

Chat History:
{chat_history_str}

Execution History:
{execution_history}

Available Tools:
{tools_desc}

Return STRICT JSON only. No markdown.
Schema:
{{
  "thought": "Brief reason for the action, one sentence max",
  "type": "tool_call" OR "final_answer",
  "name": "tool_name_if_tool_call",
  "params": {{ ... arguments for the tool or {{"message": "final answer", "outcome": "completed_or_failed_for_SYSTEM_TASK"}} }}
}}

Examples:
{{
  "thought": "A web lookup is needed.",
  "type": "tool_call",
  "name": "search_web",
  "params": {{ "query": "latest python version" }}
}}

{{
  "thought": "The answer is available.",
  "type": "final_answer",
  "name": null,
  "params": {{ "message": "The result is 1200." }}
}}

Rules:
1. If the user goal requires a tool, return exactly one tool call.
2. If tool results in Execution History answer the request, return a final answer.
3. If a previous tool failed, choose a different viable tool or explain the blockage.
4. For tools that use args/kwargs, you may return params as {{"args": [...], "kwargs": {{...}}}}.
5. For SYSTEM TASK final answers, params.outcome is required and must be exactly "completed" or "failed".
"""
                total_tokens = estimate_tokens(action_prompt)
                if total_tokens > MAX_ACTION_PROMPT_TOKENS:
                    logger.warning(f"Action prompt exceeds {MAX_ACTION_PROMPT_TOKENS} tokens ({total_tokens}). Forcing truncation on history/context to fit limits safely.")
                    # Force prune history more aggressively
                    if history:
                        chat_history_str = truncate_to_token_limit(chat_history_str, 10000)
                    if execution_history:
                        execution_history = truncate_to_token_limit(execution_history, 5000)

                    # Reconstruct
                    action_prompt = f"""You are a tool-capable assistant. Decide the next action for this request in one step.
Goal: {state.original_user_prompt}
{persona_guide}
{quarantine_info}
Context:
{context}

Chat History:
{chat_history_str}

Execution History:
{execution_history}

Available Tools:
{tools_desc}

Return STRICT JSON only using the schema described earlier.
"""
                state.current_status = "tool_execution"
                print(color_text(f"--- Cycle {step_i+1}: Action Selection ---", CLIColors.TOOL_NAME))

                action_response = await invoke_gemini_model_async(
                    prompt=action_prompt,
                    model_name=config.DEFAULT_MODEL,
                    strategy="RAW"
                )

                parsed_response = self._parse_tool_call(action_response) # Reuse parser, effectively parsing JSON

                # Normalize the new schema to the old internal variables
                tool_call = None
                cycle_record: Dict[str, Any] = {
                    "cycle": step_i + 1,
                    "selected_type": None,
                    "tool_name": None,
                    "thought": "",
                    "quality_gate_result": None,
                    "retry_reason": None,
                }

                if parsed_response:
                    resp_type = parsed_response.get("type")
                    cycle_record["selected_type"] = resp_type
                    cycle_record["thought"] = str(parsed_response.get("thought") or "")

                    if resp_type == "final_answer":
                        # Extract message
                        params = parsed_response.get("params", {})
                        if isinstance(params, dict):
                            final_answer = params.get("message", "")
                        else:
                            final_answer = str(params)

                        success = not (
                            context_source == "SYSTEM"
                            and isinstance(params, dict)
                            and params.get("outcome") != "completed"
                        )

                        quality_result = answer_quality_gate.evaluate(
                            user_prompt=state.original_user_prompt,
                            answer=final_answer,
                            context=context,
                            execution_history=execution_history,
                            remaining_cycles=max_steps - step_i - 1,
                        )
                        cycle_record["quality_gate_result"] = asdict(quality_result)
                        if not quality_result.accepted and step_i < max_steps - 1:
                            cycle_record["retry_reason"] = quality_result.reason
                            cycle_metadata.append(cycle_record)
                            execution_history += (
                                f"Cycle {step_i+1}:\n"
                                f"Proposed Final Answer: {final_answer[:500]}\n"
                                f"Quality Gate: rejected ({quality_result.reason}). "
                                f"{quality_result.retry_observation}\n"
                            )
                            final_answer = ""
                            success = False
                            continue
                        if not quality_result.accepted:
                            state.errors.append(
                                f"Answer quality gate rejected final answer but max cycles were reached: {quality_result.reason}"
                            )

                        # Context-Aware Exit Logic
                        if context_source == "SYSTEM":
                            logger.info(f"System Task Completed. Output: {final_answer[:100]}...")
                        cycle_metadata.append(cycle_record)
                        break

                    elif resp_type == "tool_call":
                        # Adapt to tool execution format
                        tool_call = {
                            "action": parsed_response.get("name"),
                            "thought": parsed_response.get("thought"),
                            # Handle params mapping to args/kwargs
                        }
                        params = parsed_response.get("params", {})
                        cycle_record["tool_name"] = tool_call.get("action")

                        # Support explicit args/kwargs structure if LLM used it
                        if "args" in params and isinstance(params["args"], list):
                            tool_call["args"] = params["args"]
                            tool_call["kwargs"] = params.get("kwargs", {})
                        else:
                            # Treat flat params as kwargs
                            tool_call["args"] = []
                            tool_call["kwargs"] = params

                    # Support legacy fallback if LLM ignored strict instructions (Re-Act style)
                    elif "action" in parsed_response:
                         tool_call = parsed_response
                         cycle_record["selected_type"] = "tool_call"
                         cycle_record["tool_name"] = parsed_response.get("action")
                         cycle_record["thought"] = str(parsed_response.get("thought") or parsed_response.get("reason") or "")

                if tool_call:
                    # Execute Tool
                    tool_name = tool_call.get("action")
                    args = tool_call.get("args", [])
                    kwargs = tool_call.get("kwargs", {})
                    thought = tool_call.get("thought", "")
                    cycle_record["selected_type"] = "tool_call"
                    cycle_record["tool_name"] = tool_name
                    cycle_record["thought"] = thought

                    # Emit action event for UI
                    action_node_id = f"thought_action_{uuid.uuid4().hex[:8]}"
                    EventEmitter.emit("thought_update", {
                        "node_id": action_node_id,
                        "parent_id": last_node_id,
                        "role": "Action",
                        "thought": f"Action: {tool_name}\nReasoning: {thought}",
                        "cycle": step_i + 1
                    })
                    last_node_id = action_node_id

                    print(color_text(f"Action: {thought}", CLIColors.THOUGHT))
                    print(color_text(f"Running Tool: {tool_name}", CLIColors.TOOL_NAME))

                    if current_ui_task:
                        self.task_manager.update_task_status(
                            current_ui_task.task_id,
                            ActiveTaskStatus.RUNNING,
                            step_desc=f"{tool_name}: {thought[:40]}..."
                        )

                    # Tool Execution Logic (with self-healing + circuit breaker)
                    execution_success = False
                    result_str = ""
                    max_retries = 2

                    if tool_name in self.blocked_tools:
                        blocked_reason = self.blocked_tools.get(tool_name, {}).get("reason", "tool temporarily blocked")
                        result_str = f"Circuit breaker active for tool '{tool_name}': {blocked_reason}"
                        state.errors.append(result_str)
                    else:
                        for attempt in range(max_retries + 1):
                            try:
                                if tool_name in {"spawn_background_agent", "wake_agent"} and session_id and "session_id" not in kwargs:
                                    kwargs["session_id"] = session_id

                                # `execute_tool` now returns a validated dictionary based on BaseActionResponse
                                tool_response = await tool_system_instance.execute_tool(
                                    tool_name,
                                    args=tuple(args),
                                    kwargs=kwargs,
                                    task_manager=self.task_manager,
                                    notification_manager=self.notification_manager,
                                    action_executor=self.action_executor
                                )

                                result = tool_response.get("result")

                                if not tool_response.get("success"):
                                    raise Exception(tool_response.get("error_message") or str(result))

                                if isinstance(result, dict) and 'images' in result:
                                    new_images = result.get('images', [])
                                    if new_images:
                                        for img in new_images:
                                            collected_image = self._build_collected_image(img, tool_name)
                                            if collected_image not in collected_images:
                                                collected_images.append(collected_image)

                                    # Omit large image strings from the LLM execution history
                                    result_for_llm = dict(result)
                                    result_for_llm['images'] = f"[{len(new_images)} image(s) captured and saved to context]"
                                    result_str = str(result_for_llm)
                                else:
                                    result_str = str(result)

                                if isinstance(result, dict) and result.get('status') == 'PAUSED':
                                    final_answer = result_str
                                    success = True
                                    print(color_text(f"--> Paused for user: {result.get('question')}", CLIColors.SYSTEM_MESSAGE))
                                    break

                                execution_success = True
                                state.tool_results.append({
                                    "action_name": tool_name,
                                    "success": True,
                                    "result": result_str
                                })
                                break
                            except Exception as e:
                                context_data = {
                                    "goal": state.original_user_prompt,
                                    "args": args,
                                    "kwargs": kwargs,
                                    "execution_history_excerpt": execution_history[-1000:] if execution_history else ""
                                }
                                failure_meta = self._register_tool_failure(tool_name, e, context_data=context_data)
                                if failure_meta.get("activated"):
                                    result_str = f"Circuit breaker activated for tool '{tool_name}': {failure_meta.get('blocked_reason')}"
                                    print(color_text(f"⛔ {result_str}", CLIColors.WARNING))
                                    state.errors.append(result_str)
                                    break

                                # Skip execution retries for parameter signature/validation errors
                                error_type_name = type(e).__name__
                                if error_type_name == "ToolValidationError" or "ToolValidationError" in str(type(e)):
                                    result_str = f"Error: {str(e)}"
                                    state.errors.append(result_str)
                                    state.tool_results.append({
                                        "action_name": tool_name,
                                        "success": False,
                                        "error_message": result_str
                                    })
                                    break

                                if attempt < max_retries:
                                    print(color_text(f"⚠️ Tool '{tool_name}' failed. Retrying...", CLIColors.WARNING))
                                    state.errors.append(f"Tool {tool_name} failed: {e}")
                                    # Optional: auto-repair logic could go here
                                    await asyncio.sleep(1)
                                else:
                                    result_str = f"Error: {str(e)}"
                                    state.errors.append(result_str)
                                    state.tool_results.append({
                                        "action_name": tool_name,
                                        "success": False,
                                        "error_message": result_str
                                    })

                    # Emit Tool Result node for Visual Cortex
                    tool_result_node_id = f"thought_res_{uuid.uuid4().hex[:8]}"
                    EventEmitter.emit("thought_update", {
                        "node_id": tool_result_node_id,
                        "parent_id": last_node_id,
                        "role": "Tool",
                        "thought": result_str[:250] + ("..." if len(result_str) > 250 else ""),
                        "cycle": step_i + 1
                    })
                    last_node_id = tool_result_node_id

                    # Append to history
                    step_record = f"Cycle {step_i+1}:\nAction: {tool_name}\nReason: {thought}\nResult: {result_str[:1000]}\n"
                    execution_history += step_record
                    current_steps.append({
                        "cycle": step_i + 1,
                        "reason": thought,
                        "tool": tool_name,
                        "result": result_str
                    })
                    cycle_metadata.append(cycle_record)
                    if execution_success and tool_name in {"spawn_background_agent", "wake_agent"}:
                        final_answer = result_str
                        success = True
                        break
                    continue

                # The model did not use a tool or final-answer schema. Treat as conversational response or retry if it looks like broken JSON.
                
                # Safety check: If response looks like JSON but wasn't parsed, DO NOT treat as final answer.
                is_suspicious_json = action_response.strip().startswith("{") or \
                                     action_response.strip().lower().startswith("json") or \
                                     '"type":' in action_response

                if is_suspicious_json:
                     print(color_text("⚠️ Invalid JSON detected. Forcing retry.", CLIColors.WARNING))
                     state.errors.append(f"Cycle {step_i+1}: Action output invalid JSON")
                     execution_history += f"Cycle {step_i+1}: Action output invalid JSON. Retrying.\n"
                     cycle_record["selected_type"] = "invalid_json"
                     cycle_record["retry_reason"] = "invalid_json"
                     cycle_metadata.append(cycle_record)
                     # Continue loop (retry)
                     continue

                # If it's just chatting, treat as final answer.
                if not action_response or not action_response.strip():
                    # Fallback for empty model response
                    logger.warning("Model returned empty response. using fallback.")
                    final_answer = "Task Completed. (No text response generated)"
                else:
                    final_answer = action_response
                cycle_record["selected_type"] = "final_answer"
                cycle_record["thought"] = "Model returned plain text final answer."
                quality_result = answer_quality_gate.evaluate(
                    user_prompt=state.original_user_prompt,
                    answer=final_answer,
                    context=context,
                    execution_history=execution_history,
                    remaining_cycles=max_steps - step_i - 1,
                )
                cycle_record["quality_gate_result"] = asdict(quality_result)
                if not quality_result.accepted and step_i < max_steps - 1:
                    cycle_record["retry_reason"] = quality_result.reason
                    cycle_metadata.append(cycle_record)
                    execution_history += (
                        f"Cycle {step_i+1}:\n"
                        f"Proposed Final Answer: {final_answer[:500]}\n"
                        f"Quality Gate: rejected ({quality_result.reason}). "
                        f"{quality_result.retry_observation}\n"
                    )
                    final_answer = ""
                    continue
                if not quality_result.accepted:
                    state.errors.append(
                        f"Answer quality gate rejected final answer but max cycles were reached: {quality_result.reason}"
                    )
                cycle_metadata.append(cycle_record)
                success = True
                break

            if not success and not final_answer:
                final_answer = "Maximum cycles reached."
                state.errors.append("Maximum cycles reached without final answer.")

            # Record Experience
            tools_used_names = [step['tool'] for step in current_steps if 'tool' in step]
            outcome = "SUCCESS" if success else "FAILURE"
            asyncio.create_task(self.episodic_manager.record_experience(
                prompt=state.original_user_prompt,
                plan=current_steps,
                outcome=outcome,
                tools_used=tools_used_names
            ))

            state.tool_results.append({
                "action_name": "orchestrator_final_answer",
                "success": success,
                "result": final_answer,
                "collected_images": collected_images,
                "react_cycle_metadata": cycle_metadata,
            })
            state.context_limits["react_cycle_metadata"] = cycle_metadata

            if success:
                state.current_status = "completed"
            else:
                state.current_status = "failed"

        finally:
            # Clean up ephemeral task
            if current_ui_task:
                 try:
                    self.task_manager.update_task_status(
                        current_ui_task.task_id,
                        ActiveTaskStatus.COMPLETED_SUCCESSFULLY if success else ActiveTaskStatus.FAILED_UNKNOWN,
                        reason=None if success else final_answer[:500],
                        step_desc="Response ready."
                    )
                 except Exception:
                     pass

    def _parse_tool_call(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Extracts JSON tool call from text.
        """
        try:
            # Clean up common prefixes
            cleaned_text = text.strip()
            if cleaned_text.lower().startswith("json"):
                cleaned_text = cleaned_text[4:].strip()
            
            # 1. Attempt refined regex for backticks
            # Matches ```json { ... } ``` or ``` { ... } ```
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))

            # 2. Attempt to find the first valid brace pair
            start_index = cleaned_text.find("{")
            if start_index != -1:
                balance = 0
                for i in range(start_index, len(cleaned_text)):
                    char = cleaned_text[i]
                    if char == "{":
                        balance += 1
                    elif char == "}":
                        balance -= 1
                        if balance == 0:
                            # Found the closing brace
                            candidate_json = cleaned_text[start_index:i+1]
                            try:
                                return json.loads(candidate_json)
                            except json.JSONDecodeError:
                                # Fallback: Try ast.literal_eval
                                import ast
                                try:
                                    return ast.literal_eval(candidate_json)
                                except Exception:
                                    pass
                            break # Stop after first candidate
        except Exception:
            pass
        return None

    async def _enrich_prompt_with_vision(self, prompt: str, images: Optional[List[str]]) -> str:
        """Analyze images and append context to prompt."""
        if not images:
            return prompt

        try:
            print(f"DynamicOrchestrator: Analyzing {len(images)} images...")
            from ai_assistant.core.vision_service import VisionService
            vision_service = VisionService()
            analysis_result = await vision_service.analyze_visuals(images[0], context=prompt)

            if analysis_result:
                summary = f"\n[Visual Analysis]: {analysis_result.get('suggestion', 'No suggestion')} Issues: {', '.join(analysis_result.get('issues', []))}"
                return prompt + summary
        except Exception as e:
            logger.error(f"Vision analysis failed: {e}")

        return prompt

    async def _gather_context(self, prompt: str) -> Tuple[str, Dict[str, Any]]:
        """
        Gathers RAG facts and Project context, optimizing for tokens.
        """
        context_parts = []
        metadata = {}

        # 1. RAG
        if self.memory_manager:
            try:
                # Dynamically adjust RAG K based on prompt size roughly
                prompt_tokens = estimate_tokens(prompt)
                k = 20 if prompt_tokens < 10000 else 10

                rag_results = await self.memory_manager.retrieve_relevant_context(prompt, k=k)
                if rag_results:
                    facts = [f"- {res.get('text', '')}" for res in rag_results]

                    # Truncate total RAG facts if extremely long
                    rag_text = "Learned Facts:\n" + "\n".join(facts)
                    if estimate_tokens(rag_text) > 8000:
                         rag_text = truncate_to_token_limit(rag_text, 8000)
                         metadata['rag_truncated'] = True

                    context_parts.append(rag_text)
                    metadata['rag_count'] = len(rag_results)
            except Exception as e:
                logger.error(f"RAG failed: {e}")

        # 2. Project Context (Simplified & Proactive)
        prompt_lower = prompt.lower()
        file_mentions = re.findall(r'[\w./-]+\.py', prompt)

        if file_mentions:
            context_parts.append("Potential File Context:")
            from ai_assistant.custom_tools.file_system_tools import read_text_from_file

            for fname in file_mentions:
                # Naive resolution: check if it exists in current dir or basic project structure
                # We reuse the logic in self_modification to find likely paths even if partial
                # Note: This is read-only peek for context

                # Check absolute or cwd relative
                if os.path.exists(fname):
                    try:
                        content = read_text_from_file(fname)
                        if not content.startswith("Error"):
                            context_parts.append(f"--- Content of {fname} ---\n{content}\n--- End of {fname} ---")
                            metadata[f'file_context_{fname}'] = "Loaded"
                    except Exception:
                        pass
                # Check module path like behavior if it looks like a module but has .py
                # (handled loosely by re above)

        if "project" in prompt_lower or ".py" in prompt_lower:
            context_parts.append("Note: If specific files were not loaded above, use file tools to explore the codebase.")
            metadata['project_context_hint'] = True

        return "\n\n".join(context_parts), metadata

    async def _update_session_summary(self, session_id: str, history: Optional[List[Dict[str, str]]]):
        """
        Updates the episodic memory for the current session.
        """
        if not history or not self.memory_manager:
            return

        # 1. Find existing episode for this session
        episodes = self.memory_manager.get_all_episodes()
        existing_episode = next((e for e in episodes if e.get("session_id") == session_id), None)
        
        # 2. Summarize History
        try:
            chat_text = ""
            recent_history = history[-30:] 
            for msg in recent_history:
                role = msg.get('role', 'unknown').upper()
                content = str(msg.get('content', ''))[:500]
                chat_text += f"{role}: {content}\n"
                
            prompt = f"""Summarize this chat session into a high-level narrative.
Focus on the overall goal and progress.
Chat History:
{chat_text}

Output ONLY the summary text."""

            summary = await invoke_gemini_model_async(prompt, model_name=config.DEFAULT_MODEL)
            if not summary:
                return

            if existing_episode:
                self.memory_manager.update_episode(
                    episode_id=existing_episode['episode_id'],
                    summary=summary,
                    title=existing_episode.get('title') 
                )
            else:
                title_prompt = f"Generate a short (3-5 words) title for this chat:\n{summary}"
                title = await invoke_gemini_model_async(title_prompt, model_name=config.DEFAULT_MODEL)
                title = title.strip().replace('"', '') if title else "Chat Session"
                
                self.memory_manager.add_episode(
                    summary=summary,
                    title=title,
                    session_id=session_id
                )
                
        except Exception as e:
            logger.error(f"Error in _update_session_summary: {e}")

    async def get_current_progress(self) -> Dict[str, Any]:
        """Get the current progress and context of task execution."""
        return {
            'current_goal': self.current_goal,
            'current_plan': self.current_plan,
            'context': self.context,
            'last_success': self.context.get('last_success')
        }
