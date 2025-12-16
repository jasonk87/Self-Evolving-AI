"""Dynamic orchestrator for handling complex user interactions."""

import re
import os
import asyncio
import uuid
import json
import logging
from typing import Dict, List, Optional, Any, Tuple

from ai_assistant.core.enums import ExecutionMode
from ai_assistant.core.router import TaskRouter
from ai_assistant.config import DEFAULT_EXECUTION_MODE, is_debug_mode
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async, invoke_parallel_thinking
from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.utils.display_utils import CLIColors, color_text
from ai_assistant.memory.event_logger import log_event

# Legacy imports to keep signature compatible
from ..planning.planning import PlannerAgent
from ..planning.execution import ExecutionAgent 
from ..learning.learning import LearningAgent
from ..execution.action_executor import ActionExecutor
from .task_manager import TaskManager
from .notification_manager import NotificationManager
from ..planning.hierarchical_planner import HierarchicalPlanner
from ..utils.conversational_helpers import summarize_tool_result_conversationally

logger = logging.getLogger(__name__)

# Constants
MAX_REACT_STEPS = 10

class DynamicOrchestrator:
    """
    Orchestrates the dynamic planning and execution of user prompts.
    Implements a Tri-State Execution Architecture (Direct, Fast ReAct, Thinking Pro).
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

        # Inject memory manager into planner if not already set
        if self.planner and self.memory_manager and hasattr(self.planner, 'memory_manager') and self.planner.memory_manager is None:
            self.planner.memory_manager = self.memory_manager

        self.router = TaskRouter()
        self.context: Dict[str, Any] = {}
        self.current_goal: Optional[str] = None
        self.current_plan: Optional[List[Dict[str, Any]]] = None

    async def process_prompt(self, prompt: str, conversation_history: Optional[List[Dict[str, str]]] = None, session_id: Optional[str] = None, images: Optional[List[str]] = None) -> Tuple[bool, str]:
        """
        Process a user prompt by routing it to the appropriate execution engine.
        Returns (success, response_message)
        """
        try:
            self.current_goal = prompt
            
            # 1. Vision Analysis (Common for all modes if images exist)
            prompt_with_context = await self._enrich_prompt_with_vision(prompt, images)

            # 2. Context Gathering (RAG, Project Context)
            # We do this before routing because context might influence routing (e.g. complexity)
            # But strictly, DIRECT mode shouldn't need heavy context.
            # Let's do a lightweight check or just gather it. For now, gather it as it helps even in Fast mode.
            full_context_str, context_metadata = await self._gather_context(prompt_with_context)

            # 3. Determine Mode
            mode = ExecutionMode.FAST_REACT # Default
            if DEFAULT_EXECUTION_MODE == "AUTO":
                mode = await self.router.determine_mode(prompt_with_context, context=context_metadata)
            else:
                try:
                    mode = ExecutionMode[DEFAULT_EXECUTION_MODE]
                except KeyError:
                    mode = ExecutionMode.FAST_REACT

            logger.info(f"DynamicOrchestrator: Routing to {mode.value} for prompt: {prompt[:50]}...")
            print(color_text(f"--> Mode Selected: {mode.value}", CLIColors.SYSTEM_MESSAGE))

            # 4. Dispatch
            if mode == ExecutionMode.DIRECT:
                return await self._run_direct_mode(prompt_with_context, conversation_history)
            elif mode == ExecutionMode.FAST_REACT:
                return await self._run_fast_react_mode(prompt_with_context, full_context_str, conversation_history, session_id)
            elif mode == ExecutionMode.THINKING_PRO:
                return await self._run_thinking_pro_mode(prompt_with_context, full_context_str, conversation_history, session_id)
            else:
                # Fallback
                return await self._run_fast_react_mode(prompt_with_context, full_context_str, conversation_history, session_id)

        except Exception as e:
            logger.error(f"Error in process_prompt: {e}", exc_info=True)
            return False, f"An unexpected error occurred: {str(e)}"

    async def _run_direct_mode(self, prompt: str, history: Optional[List[Dict[str, str]]]) -> Tuple[bool, str]:
        """
        Engine 1: Direct Mode (Non-ReAct). Zero overhead.
        """
        # Construct simple conversation context
        messages = []
        if history:
            # Flatten history to text or use as is if client supports it.
            # Our gemini client mainly takes a string prompt, so we append.
            pass

        # Simple generation
        response = await invoke_gemini_model_async(
            prompt=prompt,
            model_name="gemini-2.0-flash", # Use fast model
            temperature=0.7
        )

        if response:
            return True, response
        return False, "Failed to generate response in Direct Mode."

    async def _run_fast_react_mode(self, prompt: str, context: str, history: Optional[List[Dict[str, str]]], session_id: Optional[str]) -> Tuple[bool, str]:
        """
        Engine 2: Fast ReAct Mode. Standard loop (Think -> Act -> Observe).
        """
        return await self._execute_react_loop(
            prompt,
            context,
            history,
            session_id,
            use_parallel_thinking=False,
            model_name="gemini-2.0-flash"
        )

    async def _run_thinking_pro_mode(self, prompt: str, context: str, history: Optional[List[Dict[str, str]]], session_id: Optional[str]) -> Tuple[bool, str]:
        """
        Engine 3: Thinking Pro Mode. Parallel Branching ReAct.
        """
        return await self._execute_react_loop(
            prompt,
            context,
            history,
            session_id,
            use_parallel_thinking=True,
            model_name="gemini-2.0-flash-exp" # Use stronger model for thinking
        )

    async def _execute_react_loop(self, prompt: str, context: str, history: Optional[List[Dict[str, str]]], session_id: Optional[str], use_parallel_thinking: bool, model_name: str) -> Tuple[bool, str]:
        """
        Shared ReAct loop logic.
        """
        current_steps = []
        max_steps = MAX_REACT_STEPS

        tools_desc = tool_system_instance.get_tools_description()

        system_prompt = f"""You are a capable AI Assistant.
Goal: {prompt}

Context:
{context}

Available Tools:
{tools_desc}

Instructions:
1. Analyze the goal and context.
2. Decide on the next step.
3. IMPORTANT: If the goal is conversational or a simple greeting (e.g., "Hello", "How are you?"), responding directly is the correct action. Do NOT use tools to "wait" for input.
4. OUTPUT FORMAT:
   - If you need to use a tool, output a JSON block:
     ```json
     {{
       "action": "tool_name",
       "args": [arg1, arg2],
       "kwargs": {{ "key": "value" }},
       "thought": "Reasoning for this action"
     }}
     ```
   - If you have the final answer or are done, output:
     FINAL ANSWER: [Your Answer]

5. Loop until you achieve the goal or hit the limit.
"""

        execution_history = ""
        final_answer = ""
        success = False

        for step_i in range(max_steps):
            step_prompt = f"{system_prompt}\n\nExecution History:\n{execution_history}\n\nStep {step_i+1}:"

            if use_parallel_thinking:
                response = await invoke_parallel_thinking(
                    prompt=step_prompt,
                    model_name=model_name,
                    num_branches=3
                )
            else:
                response = await invoke_gemini_model_async(
                    prompt=step_prompt,
                    model_name=model_name
                )

            if not response:
                return False, "AI stopped responding."

            # Parse Response
            tool_call = self._parse_tool_call(response)

            if "FINAL ANSWER:" in response:
                final_answer = response.split("FINAL ANSWER:")[-1].strip()
                success = True
                break

            if tool_call:
                # Execute Tool
                tool_name = tool_call.get("action")
                args = tool_call.get("args", [])
                kwargs = tool_call.get("kwargs", {})
                thought = tool_call.get("thought", "")

                print(color_text(f"Step {step_i+1}: {thought}", CLIColors.THOUGHT))
                print(color_text(f"Running Tool: {tool_name}", CLIColors.TOOL_NAME))

                try:
                    # Convert args/kwargs if needed
                    result = await tool_system_instance.execute_tool(
                        tool_name,
                        args=tuple(args),
                        kwargs=kwargs,
                        task_manager=self.task_manager,
                        notification_manager=self.notification_manager,
                        action_executor=self.action_executor
                    )
                    result_str = str(result)
                except Exception as e:
                    result_str = f"Error: {str(e)}"

                # Append to history
                step_record = f"Step {step_i+1}:\nThought: {thought}\nAction: {tool_name}({args}, {kwargs})\nResult: {result_str[:1000]}\n"
                execution_history += step_record
                current_steps.append({
                    "tool_name": tool_name,
                    "args": args,
                    "result": result_str
                })

            else:
                # No tool call found, assume text response or query
                # If the model didn't say FINAL ANSWER but just talked, treat as answer
                final_answer = response
                success = True
                break

        if not success and not final_answer:
            final_answer = "Maximum steps reached without definitive completion."

        return success, final_answer

    def _parse_tool_call(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Extracts JSON tool call from text.
        """
        try:
            # Look for ```json ... ``` or just { ... }
            json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if not json_match:
                json_match = re.search(r"(\{.*\})", text, re.DOTALL)

            if json_match:
                json_str = json_match.group(1)
                return json.loads(json_str)
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
        Gathers RAG facts and Project context.
        """
        context_parts = []
        metadata = {}

        # 1. RAG
        if self.memory_manager:
            try:
                rag_results = await self.memory_manager.retrieve_relevant_context(prompt, k=3)
                if rag_results:
                    facts = [f"- {res.get('text', '')}" for res in rag_results]
                    context_parts.append("Learned Facts:\n" + "\n".join(facts))
                    metadata['rag_count'] = len(rag_results)
            except Exception as e:
                logger.error(f"RAG failed: {e}")

        # 2. Project Context (Simplified from original)
        prompt_lower = prompt.lower()
        if "project" in prompt_lower or ".py" in prompt_lower:
            # This is a basic placeholder for the complex project context gathering in the original
            # In a full refactor, we'd extract the ProjectContextManager into a separate class
            # For now, we rely on tools to read files if the model decides to.
            # But we can add a hint.
            context_parts.append("Note: If this is a project request, use file tools to explore the codebase.")
            metadata['project_context_hint'] = True

        return "\n\n".join(context_parts), metadata

    async def get_current_progress(self) -> Dict[str, Any]:
        """Get the current progress and context of task execution."""
        return {
            'current_goal': self.current_goal,
            'current_plan': self.current_plan, # Might be None in new modes
            'context': self.context,
            'last_success': self.context.get('last_success')
        }
