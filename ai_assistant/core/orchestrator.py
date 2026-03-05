"""Dynamic orchestrator for handling complex user interactions."""

import re
import os
import sys

# Ensure project root is in sys.path for stand-alone execution
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncio
import uuid
import json
import logging
from typing import Dict, List, Optional, Any, Tuple

from ai_assistant.core.enums import ExecutionMode
from ai_assistant.core.router import TaskRouter
import ai_assistant.config as config
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async
from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.utils.display_utils import CLIColors, color_text
from ai_assistant.memory.event_logger import log_event
from ai_assistant.core.events import EventEmitter

# Legacy imports to keep signature compatible
from ..planning.planning import PlannerAgent
from ..planning.execution import ExecutionAgent 
from ..learning.learning import LearningAgent
from ..execution.action_executor import ActionExecutor
from .task_manager import TaskManager, ActiveTaskStatus, ActiveTaskType
from .notification_manager import NotificationManager
from ..planning.hierarchical_planner import HierarchicalPlanner
from ..utils.conversational_helpers import summarize_tool_result_conversationally
from ai_assistant.memory.episodic_manager import EpisodicMemoryManager

logger = logging.getLogger(__name__)

# Constants
MAX_REACT_STEPS = 10

class DynamicOrchestrator:
    """
    Orchestrates the dynamic planning and execution of user prompts.
    Implements a Universal Bicameral Architecture (Strategist -> Operator).
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

        # Inject memory manager into planner if not already set
        if self.planner and self.memory_manager and hasattr(self.planner, 'memory_manager') and self.planner.memory_manager is None:
            self.planner.memory_manager = self.memory_manager

        self.router = TaskRouter()
        self.context: Dict[str, Any] = {}
        self.current_goal: Optional[str] = None
        self.current_plan: Optional[List[Dict[str, Any]]] = None
        self.blocked_tools: Dict[str, Dict[str, Any]] = {}
        self.failure_counts: Dict[str, int] = {}

    async def process_prompt(self, prompt: str, conversation_history: Optional[List[Dict[str, str]]] = None, session_id: Optional[str] = None, images: Optional[List[str]] = None, context_source: str = "USER") -> Tuple[bool, str, Optional[List[str]]]:
        """
        Process a user prompt using the Universal Bicameral Brain architecture.
        Returns (success, response_message, images)
        """
        try:
            self.current_goal = prompt
            
            # 1. Vision Analysis (Common for all modes if images exist)
            prompt_with_context = await self._enrich_prompt_with_vision(prompt, images)

            # 2. Context Gathering (RAG, Project Context)
            full_context_str, context_metadata = await self._gather_context(prompt_with_context)

            logger.info(f"DynamicOrchestrator: Starting Universal Cycle for prompt: {prompt[:50]}...")
            print(color_text(f"--> Strategy: Universal Bicameral", CLIColors.SYSTEM_MESSAGE))

            # 3. Execute Universal Cycle
            return await self._execute_universal_cycle(prompt_with_context, full_context_str, conversation_history, session_id, context_source)

        except Exception as e:
            logger.error(f"Error in process_prompt: {e}", exc_info=True)
            return False, f"An unexpected error occurred: {str(e)}", None

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
        }

    def _build_tool_failure_signature(self, tool_name: str, error: Exception) -> str:
        """Build a compact signature for repetitive tool failures in a single cycle run."""
        err_type = type(error).__name__
        err_msg = str(error or "")[:120]
        return f"{tool_name}|{err_type}|{err_msg}"

    def get_blocked_tools(self) -> Dict[str, Dict[str, Any]]:
        """Return the current list of blocked tools."""
        return self.blocked_tools

    def unblock_tool(self, tool_name: str) -> bool:
        """Manually unblock a quarantined tool."""
        if tool_name in self.blocked_tools:
            del self.blocked_tools[tool_name]

            # Optionally clear failure counts associated with this tool
            keys_to_delete = [k for k in self.failure_counts if k.startswith(f"{tool_name}|")]
            for k in keys_to_delete:
                del self.failure_counts[k]

            EventEmitter.emit("quarantine_update", {"blocked_tools": self.blocked_tools})
            return True
        return False

    def _register_tool_failure(
        self,
        tool_name: str,
        error: Exception,
        threshold: int = 3,
    ) -> Dict[str, Any]:
        """Track repeated failures and activate a per-tool circuit breaker when threshold is hit."""
        signature = self._build_tool_failure_signature(tool_name, error)
        count = self.failure_counts.get(signature, 0) + 1
        self.failure_counts[signature] = count

        import time
        activated = False
        if count >= threshold:
            activated = True
            if tool_name not in self.blocked_tools:
                self.blocked_tools[tool_name] = {
                    "signature": signature,
                    "count": count,
                    "reason": f"Repeated identical failure ({count}x): {signature}",
                    "timestamp": time.time()
                }
                EventEmitter.emit("quarantine_update", {"blocked_tools": self.blocked_tools})
            else:
                 self.blocked_tools[tool_name]["count"] = count

        return {
            "signature": signature,
            "count": count,
            "activated": activated,
            "blocked_reason": self.blocked_tools.get(tool_name, {}).get("reason"),
        }

    async def _execute_universal_cycle(self, prompt: str, context: str, history: Optional[List[Dict[str, str]]], session_id: Optional[str], context_source: str) -> Tuple[bool, str, Optional[List[str]]]:
        """
        The Universal Bicameral Cycle: Strategist (Think) -> Operator (Act) -> Loop.
        """
        # Step A: Recall Failures
        failure_warning = await self.episodic_manager.recall_failures(prompt)
        if failure_warning:
            print(color_text(f"--> Episodic Memory: {failure_warning}", CLIColors.WARNING))
            context = f"{failure_warning}\n\n{context}"

        current_steps = []
        max_steps = MAX_REACT_STEPS
        collected_images = []
        tools_desc = tool_system_instance.get_tools_description()

        # Initial Strategist Prompt
        execution_history = ""
        final_answer = ""
        success = False

        # Define persona guidance based on context_source
        persona_guide = ""
        if context_source == "SYSTEM":
            persona_guide = "MODE: SYSTEM TASK. You are running as a background process. Do NOT be conversational. Be technical, concise, and results-oriented. If you finish, output the status/log as the FINAL ANSWER."
        else:
            persona_guide = "MODE: USER CHAT. You are 'Weebo', a personal AI assistant (inspired by Flubber). You are NOT a robot. Be witty, casual, proactive, and extremely conversational. Avoid generic AI phrases like 'I understand' or 'As an AI'."

        # Create ephemeral task for UI feedback
        current_ui_task = None
        if self.task_manager and session_id:
            try:
                # Use EPHEMERAL_AGENT_TASK or relevant type
                current_ui_task = self.task_manager.add_task(
                    description=prompt[:100], # Short desc
                    task_type=ActiveTaskType.EPHEMERAL_AGENT_TASK,
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
                print(color_text(f"\n--- Cycle {step_i+1}: Strategist (Thinking) ---", CLIColors.THOUGHT))
                
                if current_ui_task:
                    self.task_manager.update_task_status(
                        current_ui_task.task_id,
                        ActiveTaskStatus.PLANNING,
                        step_desc=f"Thinking (Cycle {step_i+1})...",
                        progress=min(90, step_i * 10)
                    )

                # Format Chat History - FULL HISTORY (No truncation)
                chat_history_str = ""
                if history:
                    for msg in history:
                        role = msg.get('role', 'unknown').upper()
                        content = str(msg.get('content', ''))
                        chat_history_str += f"{role}: {content}\n"
                
                # Phase 1: Strategist (Think)
                strategist_prompt = f"""You are the Strategist. Your goal is to analyze the user request and plan the next best action using a ReAct (Reasoning + Acting) approach.
Goal: {prompt}
{persona_guide}

Context:
{context}

Chat History:
{chat_history_str}

Execution History:
{execution_history}

Few-Shot Examples (How to Think):
Example 1:
User: "What is the weather in Tokyo?"
Strategist: "The user wants weather information. I need to check if I have a weather tool. I see 'get_weather' in the tool list. I should instruct the Operator to use it."
Plan: Call tool 'get_weather' with args=["Tokyo"].

Example 2:
User: "Calculate 25 * 48"
Strategist: "The user wants a calculation. I can use the 'python_repl' or a calculator tool. 'python_repl' is safer for complex math, but let's check tools. I see 'calculator'. Plan: Use 'calculator' with expression '25 * 48'."
Operator Result: 1200
Strategist: "The calculation is done. I have the answer. I should instruct the Operator to give the final answer."
Plan: FINAL ANSWER: The result is 1200.

Instructions:
1. Analyze the current situation based on the Goal, Context, Chat History, and Execution History.
2. VERIFY PREVIOUS STEPS: If the Execution History shows a failure or unexpected result, ANALYZE WHY. Do not repeat the same mistake. Modify your plan.
3. Determine if the goal is met.
4. If not met, plan the EXACT next step for the Operator.
5. Do NOT execute tools yourself. You only PLAN.
6. If the goal is met or you have a final answer, instruct the Operator to provide it.

Output strictly your reasoning and the plan for the Operator.
"""
                # Use RAW strategy to avoid double-thinking (The Strategist IS the thinker)
                strategist_response = await invoke_gemini_model_async(
                    prompt=strategist_prompt,
                    model_name=config.DEFAULT_MODEL,
                    strategy="RAW"
                )
                strategist_thoughts = "Strategist reasoning is embedded in the plan."
                
                # Log the deep thought (The whole response is the thought/plan)
                # print(color_text(f"Strategist Thoughts:\n{strategist_thoughts}", CLIColors.THOUGHT))
                
                # Emit thought event for UI
                strategist_node_id = f"thought_strat_{uuid.uuid4().hex[:8]}"
                EventEmitter.emit("thought_update", {
                    "node_id": strategist_node_id,
                    "parent_id": last_node_id,
                    "role": "Strategist",
                    "thought": strategist_response, # The whole plan is the thought
                    "cycle": step_i + 1
                })
                last_node_id = strategist_node_id

                print(color_text(f"Strategist Plan: {strategist_response[:200]}...", CLIColors.THOUGHT))
                
                # Update UI with strategy excerpt
                if current_ui_task:
                    # Extract a short summary of the plan
                    plan_excerpt = strategist_response.split('\n')[0][:50]
                    self.task_manager.update_task_status(
                        current_ui_task.task_id,
                        ActiveTaskStatus.PLANNING,
                        step_desc=f"{plan_excerpt}...",
                         progress=min(90, step_i * 10 + 5)
                    )

                # Phase 2: Operator (Act)
                print(color_text(f"--- Cycle {step_i+1}: Operator (Acting) ---", CLIColors.TOOL_NAME))

                operator_system_prompt = f"""You are the Operator. You execute the Strategist's plan.
Goal: {prompt}
{persona_guide}

Available Tools:
{tools_desc}

Strategist's Plan:
{strategist_response}

MANDATORY: ALL RESPONSES MUST BE VALID JSON.
You must return a single JSON object. Do not include markdown code blocks or additional text.

Schema:
{{
  "thought": "Brief reasoning for this action",
  "type": "tool_call" OR "final_answer",
  "name": "tool_name_if_tool_call",
  "params": {{ ... arguments for tool or message content ... }}
}}

Few-Shot Examples (How to Act):
Example 1 (Tool Call):
Strategist: "I need to search for 'latest python version'."
Operator:
{{
  "thought": "Searching for python version as planned.",
  "type": "tool_call",
  "name": "search_web",
  "params": {{ "query": "latest python version" }}
}}

Example 2 (Final Answer):
Strategist: "I have the info. Answer the user: It is 3.12."
Operator:
{{
  "thought": "Answering the user.",
  "type": "final_answer",
  "name": null,
  "params": {{ "message": "The latest Python version is 3.12." }}
}}

Instructions:
1. Follow the Strategist's plan exactly.
2. Output STRICT JSON only.
3. If the plan is to use a tool, set "type" to "tool_call" and "name" to the tool name. Put arguments in "params".
   **IMPORTANT:** If a tool takes positional arguments (like `args=['val']` in Python), map them to named parameters if possible, or use a "args" list in "params" if the tool schema requires it. (Ideally, use the tool's defined parameter names).
   *Compatibility Note:* If the system expects "args" list and "kwargs" dict, structure "params" as `{{"args": [...], "kwargs": {{...}}}}` OR just flat parameters if the tool system handles mapping.
   *Current System Constraint:* The tool executor expects `args` (list) and `kwargs` (dict). You can output:
   `"params": {{ "args": ["arg1"], "kwargs": {{ "key": "val" }} }}`
   OR
   `"params": {{ "arg1": "val1", "arg2": "val2" }}` (The system will try to map these to kwargs).

4. If the plan is to answer the user, set "type" to "final_answer" and put the response string in "params": `{{"message": "..."}}`.
"""
                # We append execution history to operator too so it knows what happened
                operator_prompt = f"{operator_system_prompt}\n\nExecution History:\n{execution_history}\n\nAction (JSON):"

                # Use RAW strategy. The Operator prompt asks for JSON. Hidden thoughts are handled by <think> removal in client if present.
                operator_response = await invoke_gemini_model_async(
                    prompt=operator_prompt,
                    model_name=config.DEFAULT_MODEL,
                    strategy="RAW"
                )

                # Operator thoughts are inside the JSON "thought" field usually.

                # Phase 3: Loop Logic
                parsed_response = self._parse_tool_call(operator_response) # Reuse parser, effectively parsing JSON

                # Normalize the new schema to the old internal variables
                tool_call = None

                if parsed_response:
                    resp_type = parsed_response.get("type")

                    if resp_type == "final_answer":
                        # Extract message
                        params = parsed_response.get("params", {})
                        if isinstance(params, dict):
                            final_answer = params.get("message", "")
                        else:
                            final_answer = str(params)

                        success = True

                        # Context-Aware Exit Logic
                        if context_source == "SYSTEM":
                            logger.info(f"System Task Completed. Output: {final_answer[:100]}...")
                        break

                    elif resp_type == "tool_call":
                        # Adapt to tool execution format
                        tool_call = {
                            "action": parsed_response.get("name"),
                            "thought": parsed_response.get("thought"),
                            # Handle params mapping to args/kwargs
                        }
                        params = parsed_response.get("params", {})

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

                if tool_call:
                    # Execute Tool
                    tool_name = tool_call.get("action")
                    args = tool_call.get("args", [])
                    kwargs = tool_call.get("kwargs", {})
                    thought = tool_call.get("thought", "")

                    # Emit thought event for UI (Operator Action)
                    operator_node_id = f"thought_op_{uuid.uuid4().hex[:8]}"
                    EventEmitter.emit("thought_update", {
                        "node_id": operator_node_id,
                        "parent_id": last_node_id,
                        "role": "Operator",
                        "thought": f"Action: {tool_name}\nReasoning: {thought}",
                        "cycle": step_i + 1
                    })
                    last_node_id = operator_node_id

                    print(color_text(f"Operator Action: {thought}", CLIColors.THOUGHT))
                    print(color_text(f"Running Tool: {tool_name}", CLIColors.TOOL_NAME))

                    if current_ui_task:
                        self.task_manager.update_task_status(
                            current_ui_task.task_id,
                            ActiveTaskStatus.GENERATING_CODE, # Mapped roughly to execution
                            step_desc=f"{tool_name}: {thought[:40]}..."
                        )

                    # Tool Execution Logic (with self-healing + circuit breaker)
                    execution_success = False
                    result_str = ""
                    max_retries = 2

                    if tool_name in self.blocked_tools:
                        blocked_reason = self.blocked_tools.get(tool_name, {}).get("reason", "tool temporarily blocked")
                        result_str = f"Circuit breaker active for tool '{tool_name}': {blocked_reason}"
                    else:
                        for attempt in range(max_retries + 1):
                            try:
                                result = await tool_system_instance.execute_tool(
                                tool_name,
                                args=tuple(args),
                                kwargs=kwargs,
                                task_manager=self.task_manager,
                                notification_manager=self.notification_manager,
                                action_executor=self.action_executor
                            )

                                if isinstance(result, dict) and 'images' in result:
                                    new_images = result.get('images', [])
                                    if new_images:
                                        for img in new_images:
                                            if img not in collected_images:
                                                collected_images.append(img)

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
                                break
                            except Exception as e:
                                failure_meta = self._register_tool_failure(tool_name, e)
                                if failure_meta.get("activated"):
                                    result_str = f"Circuit breaker activated for tool '{tool_name}': {failure_meta.get('blocked_reason')}"
                                    print(color_text(f"⛔ {result_str}", CLIColors.WARNING))
                                    break

                                if attempt < max_retries:
                                    print(color_text(f"⚠️ Tool '{tool_name}' failed. Retrying...", CLIColors.WARNING))
                                    # Optional: auto-repair logic could go here
                                    await asyncio.sleep(1)
                                else:
                                    result_str = f"Error: {str(e)}"

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
                    step_record = f"Cycle {step_i+1}:\nStrategist: {strategist_response}\nOperator Action: {tool_name}\nResult: {result_str[:1000]}\n"
                    execution_history += step_record
                    current_steps.append({
                        "cycle": step_i + 1,
                        "strategist": strategist_response,
                        "tool": tool_name,
                        "result": result_str
                    })
                    # Operator didn't use a tool or say FINAL ANSWER. Treat as a conversational response or error?
                    
                    # Safety check: If response looks like JSON but wasn't parsed, DO NOT treat as final answer.
                    is_suspicious_json = operator_response.strip().startswith("{") or \
                                         operator_response.strip().lower().startswith("json") or \
                                         '"type":' in operator_response

                    if is_suspicious_json:
                         print(color_text(f"⚠️ Invalid JSON detected. Forcing retry.", CLIColors.WARNING))
                         execution_history += f"Cycle {step_i+1}: Operator output invalid JSON. Retrying.\n"
                         # Continue loop (retry)
                         continue

                    # If it's just chatting, treat as final answer.
                    if not operator_response or not operator_response.strip():
                        # Fallback for empty model response
                        logger.warning("Operator returned empty response. using fallback.")
                        final_answer = "Task Completed. (No text response generated)"
                    else:
                        final_answer = operator_response
                    success = True
                    break

            if not success and not final_answer:
                final_answer = "Maximum cycles reached."

            # Record Experience
            tools_used_names = [step['tool'] for step in current_steps if 'tool' in step]
            outcome = "SUCCESS" if success else "FAILURE"
            asyncio.create_task(self.episodic_manager.record_experience(
                prompt=prompt,
                plan=current_steps,
                outcome=outcome,
                tools_used=tools_used_names
            ))

            return success, final_answer, collected_images
        
        finally:
            # Clean up ephemeral task
            if current_ui_task:
                 try:
                    self.task_manager.update_task_status(
                        current_ui_task.task_id,
                        ActiveTaskStatus.COMPLETED_SUCCESSFULLY if success else ActiveTaskStatus.FAILED_UNKNOWN,
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
        except Exception as e:
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
        Gathers RAG facts and Project context.
        """
        context_parts = []
        metadata = {}

        # 1. RAG
        if self.memory_manager:
            try:
                rag_results = await self.memory_manager.retrieve_relevant_context(prompt, k=20)
                if rag_results:
                    facts = [f"- {res.get('text', '')}" for res in rag_results]
                    context_parts.append("Learned Facts:\n" + "\n".join(facts))
                    metadata['rag_count'] = len(rag_results)
            except Exception as e:
                logger.error(f"RAG failed: {e}")

        # 2. Project Context (Simplified & Proactive)
        prompt_lower = prompt.lower()
        file_mentions = re.findall(r'[\w./-]+\.py', prompt)

        if file_mentions:
            context_parts.append("Potential File Context:")
            from ai_assistant.core.self_modification import _resolve_file_path_robust
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

            summary = await invoke_gemini_model_async(prompt, model_name="gemini-2.0-flash")
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
                title = await invoke_gemini_model_async(title_prompt, model_name="gemini-2.0-flash")
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
