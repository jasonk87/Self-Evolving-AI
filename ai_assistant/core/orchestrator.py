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
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async, invoke_split_brain_async
from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.utils.display_utils import CLIColors, color_text
from ai_assistant.memory.event_logger import log_event

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
                strategist_response, strategist_thoughts = await invoke_split_brain_async(
                    prompt=strategist_prompt,
                    model_name=config.DEFAULT_MODEL,
                    context_text=f"Chat History Size: {len(history) if history else 0} msgs. Context Size: {len(context)} chars."
                )
                
                # Log the deep thought
                print(color_text(f"Strategist Thoughts:\n{strategist_thoughts}", CLIColors.THOUGHT))



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

Few-Shot Examples (How to Act):
Example 1 (Tool Call):
Strategist: "I need to search for 'latest python version'."
Operator:
```json
{{
  "action": "search_web",
  "args": ["latest python version"],
  "kwargs": {{}},
  "thought": "Searching for python version as planned."
}}
```

Example 3 (Visual Request):
Strategist: "The user wants a button. Use dynamic HTML."
Operator:
```json
{{
  "action": "chat_dynamic_html",
  "args": ["A button styled with CSS that says 'Click Me'"],
  "kwargs": {{}},
  "thought": "Generating styled button."
}}
```

Example 2 (Final Answer):
Strategist: "I have the info. Answer the user: It is 3.12."
Operator:
FINAL ANSWER: The latest Python version is 3.12.

Instructions:
1. Follow the Strategist's plan exactly.
2. If the plan is to use a tool, output the tool call JSON.
3. If the plan is to answer the user, output: FINAL ANSWER: [Your Answer]
4. Do NOT deviate from the plan.
5. STRICT FORMATTING: Use ONLY the JSON format shown above for tool calls. Do NOT use `tool_code` blocks, python code blocks, or any other format.
6. Check the Available Tools list carefully. If a tool requires arguments, ensure they are provided in the 'args' list or 'kwargs' dictionary.
"""
                # We append execution history to operator too so it knows what happened
                operator_prompt = f"{operator_system_prompt}\n\nExecution History:\n{execution_history}\n\nAction:"

                operator_response, operator_thoughts = await invoke_split_brain_async(
                    prompt=operator_prompt,
                    model_name=config.DEFAULT_MODEL,
                    context_text=f"Strategist Plan: {strategist_response[:100]}...\nCurrent Cycle: {step_i+1}"
                )

                # Log the deep thought
                # print(color_text(f"Operator Thoughts:\n{operator_thoughts}", CLIColors.THOUGHT))



                # Phase 3: Loop Logic
                tool_call = self._parse_tool_call(operator_response)

                if "FINAL ANSWER:" in operator_response:
                    final_answer = operator_response.split("FINAL ANSWER:")[-1].strip()
                    success = True

                    # Context-Aware Exit Logic
                    if context_source == "SYSTEM":
                        # If this is a background system task, we ensure we don't accidentally reply with a "Hello" unless it's part of the task.
                        # We trust the LLM followed the "SYSTEM TASK" persona instructions, but we can wrap the log.
                        # Since we must return a string, we return the final answer which should be the log/status.
                        logger.info(f"System Task Completed. Output: {final_answer[:100]}...")
                    break

                if tool_call:
                    # Execute Tool
                    tool_name = tool_call.get("action")
                    args = tool_call.get("args", [])
                    kwargs = tool_call.get("kwargs", {})
                    thought = tool_call.get("thought", "")

                    print(color_text(f"Operator Action: {thought}", CLIColors.THOUGHT))
                    print(color_text(f"Running Tool: {tool_name}", CLIColors.TOOL_NAME))

                    if current_ui_task:
                        self.task_manager.update_task_status(
                            current_ui_task.task_id,
                            ActiveTaskStatus.GENERATING_CODE, # Mapped roughly to execution
                            step_desc=f"{tool_name}: {thought[:40]}..."
                        )

                    # Tool Execution Logic (with self-healing)
                    execution_success = False
                    result_str = ""
                    max_retries = 2

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
                                    collected_images.extend(new_images)

                            if isinstance(result, dict) and result.get('status') == 'PAUSED':
                                result_str = str(result)
                                final_answer = result_str
                                success = True
                                print(color_text(f"--> Paused for user: {result.get('question')}", CLIColors.SYSTEM_MESSAGE))
                                break

                            result_str = str(result)
                            execution_success = True
                            break
                        except Exception as e:
                            if attempt < max_retries:
                                print(color_text(f"⚠️ Tool '{tool_name}' failed. Retrying...", CLIColors.WARNING))
                                # Optional: auto-repair logic could go here
                                await asyncio.sleep(1)
                            else:
                                result_str = f"Error: {str(e)}"

                    # Append to history
                    step_record = f"Cycle {step_i+1}:\nStrategist: {strategist_response}\nOperator Action: {tool_name}\nResult: {result_str[:1000]}\n"
                    execution_history += step_record
                    current_steps.append({
                        "cycle": step_i + 1,
                        "strategist": strategist_response,
                        "tool": tool_name,
                        "result": result_str
                    })
                else:
                    # Operator didn't use a tool or say FINAL ANSWER. Treat as a conversational response or error?
                    # If it's just chatting, treat as final answer.
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
            # 1. Attempt refined regex for backticks
            # Matches ```json { ... } ``` or ``` { ... } ```
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))

            # 2. Attempt to find the first valid brace pair (ignoring leading text like "json")
            # This is a simple stack-based parser to find the first balanced outer brace
            start_index = text.find("{")
            if start_index != -1:
                balance = 0
                for i in range(start_index, len(text)):
                    char = text[i]
                    if char == "{":
                        balance += 1
                    elif char == "}":
                        balance -= 1
                        if balance == 0:
                            # Found the closing brace
                            candidate_json = text[start_index:i+1]
                            # Try standard JSON first
                            try:
                                return json.loads(candidate_json)
                            except json.JSONDecodeError:
                                # Fallback: Try ast.literal_eval for Pythonic JSON (e.g. loops with triple quotes)
                                # This handles { "key": """value""" } which JSON can't, but LLMs often produce.
                                import ast
                                try:
                                    return ast.literal_eval(candidate_json)
                                except Exception:
                                    pass
        except Exception as e:
            # logger.warning(f"Failed to parse tool call: {e}")
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

        # 2. Project Context (Simplified)
        prompt_lower = prompt.lower()
        if "project" in prompt_lower or ".py" in prompt_lower:
            context_parts.append("Note: If this is a project request, use file tools to explore the codebase.")
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
