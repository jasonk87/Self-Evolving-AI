"""Dynamic orchestrator for handling complex user interactions."""

import re
import os
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from ..planning.planning import PlannerAgent
from ..planning.execution import ExecutionAgent 
from ..memory.event_logger import log_event
from ..core.reflection import global_reflection_log, analyze_last_failure
from ..learning.learning import LearningAgent
from ..tools.tool_system import tool_system_instance
from ..config import is_debug_mode
from ..utils.display_utils import CLIColors, color_text
from ..execution.action_executor import ActionExecutor
from ..memory.persistent_memory import load_learned_facts
from .task_manager import TaskManager
from .notification_manager import NotificationManager
from ..utils.conversational_helpers import summarize_tool_result_conversationally, rephrase_error_message_conversationally
from ..llm_interface.ollama_client import OllamaProvider
from ..planning.hierarchical_planner import HierarchicalPlanner
import uuid
import logging

logger = logging.getLogger(__name__)

class DynamicOrchestrator:
    """
    Orchestrates the dynamic planning and execution of user prompts.
    Handles multi-step tasks, maintains context, and adapts plans based on feedback.
    """

    def __init__(self, 
                 planner: PlannerAgent,
                 executor: ExecutionAgent,
                 learning_agent: LearningAgent,
                 action_executor: ActionExecutor,
                 task_manager: Optional[TaskManager] = None,
                 notification_manager: Optional[NotificationManager] = None,
                 hierarchical_planner: Optional[HierarchicalPlanner] = None):
        self.planner = planner
        self.executor = executor
        self.learning_agent = learning_agent
        self.action_executor = action_executor
        self.task_manager = task_manager
        self.notification_manager = notification_manager
        self.hierarchical_planner = hierarchical_planner
        self.context: Dict[str, Any] = {} # For last_results, etc.
        self.current_goal: Optional[str] = None
        self.current_plan: Optional[List[Dict[str, Any]]] = None
        self.conversation_history: List[Dict[str, str]] = [] # New: For chat history

    def _generate_execution_summary(self, plan: Optional[List[Dict[str, Any]]], results: List[Any]) -> str:
        if not plan:
            return "\n\nNo actions were planned or taken."

        summary_lines = [
            color_text("\n\nHere's a summary of what I did:", CLIColors.SYSTEM_MESSAGE)
        ]

        if not plan:
             summary_lines.append("No plan was executed.")
             return "\n".join(summary_lines)

        for i, step in enumerate(plan):
            tool_name = step.get("tool_name", "Unknown Tool")
            args = step.get("args", ())

            outcome_str = ""
            if i < len(results):
                result_item = results[i]
                if isinstance(result_item, Exception):
                    outcome_str = color_text(
                        f"Failed (Error: {type(result_item).__name__}: {str(result_item)[:100]})",
                        CLIColors.FAILURE
                    )
                elif isinstance(result_item, dict) and result_item.get("error"):
                    outcome_str = color_text(
                        f"Failed (Reported Error: {str(result_item.get('error'))[:100]})",
                        CLIColors.FAILURE
                    )
                elif isinstance(result_item, dict) and result_item.get("ran_successfully") is False:
                    err_detail = result_item.get("stderr", result_item.get("error", "Unknown error from tool"))
                    outcome_str = color_text(
                        f"Failed (Return Code: {result_item.get('return_code')}, Detail: {str(err_detail)[:100]})",
                        CLIColors.FAILURE
                    )
                else:
                    outcome_str = color_text(
                        f"Succeeded (Result: {str(result_item)[:100]}{'...' if len(str(result_item)) > 100 else ''})",
                        CLIColors.SUCCESS
                    )
            else:
                outcome_str = color_text("No result recorded for this step.", CLIColors.WARNING)
            
            args_display_parts = [f"'{str(arg_val)[:25]}{'...' if len(str(arg_val)) > 25 else ''}'" if not (isinstance(arg_val, list) or isinstance(arg_val, dict)) else f"{type(arg_val).__name__}(len:{len(arg_val)})" for arg_val in args]
            summary_lines.append(
                f"- Ran '{color_text(tool_name, CLIColors.TOOL_NAME)}' "
                f"with arguments ({color_text(', '.join(args_display_parts), CLIColors.TOOL_ARGS)}): "
                f"{outcome_str}")
        return "\n".join(summary_lines)

    async def process_prompt(self, prompt: str, user_id: Optional[str] = None) -> Tuple[bool, str]: # Added user_id
        """
        Process a user prompt by creating and executing a dynamic plan.
        Accepts an optional user_id for context.
        Returns (success, response_message)
        """
        # Add user's prompt to history
        self.conversation_history.append({"role": "user", "content": prompt})

        # Truncate history before processing (keeps most recent, plus current prompt)
        from ai_assistant.config import CONVERSATION_HISTORY_TURNS # Import here or at top
        max_history_messages = CONVERSATION_HISTORY_TURNS * 2
        if len(self.conversation_history) > max_history_messages:
            # Keep the last 'max_history_messages' items.
            # If current prompt was just added, it's already included in this slice.
            self.conversation_history = self.conversation_history[-max_history_messages:]

        response_message_for_history: Optional[str] = None # To store the final AI response

        try:
            self.current_goal = prompt
            available_tools_rich = tool_system_instance.list_tools_with_sources()

            log_event(
                event_type="ORCHESTRATOR_START_PROCESSING",
                description=f"Starting to process prompt: {prompt}",
                source="DynamicOrchestrator.process_prompt",
                metadata={"goal": prompt}
            )

            # --- Fact Retrieval ---
            all_learned_facts = load_learned_facts()
            relevant_facts_for_prompt: List[Dict[str, Any]] = [] # Ensure type

            if all_learned_facts:
                # Refined selection logic:
                keyword_matched_facts: List[Dict[str, Any]] = [] # Ensure type
                prompt_keywords_set = set(prompt.lower().split()) # Ensure type
                for fact_entry in all_learned_facts:
                    fact_text_lower = fact_entry.get("text", "").lower()
                    if any(keyword in fact_text_lower for keyword in prompt_keywords_set):
                        if len(keyword_matched_facts) < 5:
                            keyword_matched_facts.append(fact_entry)

                relevant_facts_for_prompt = list(keyword_matched_facts)

                category_matched_facts_count = 0
                preferred_categories = ["user_preference", "project_context", "general_knowledge"] # Moved definition here
                for fact_entry in reversed(all_learned_facts):
                    if len(relevant_facts_for_prompt) >= 7:
                        break
                    if category_matched_facts_count >= 2:
                        break

                    is_already_added = any(rf['fact_id'] == fact_entry['fact_id'] for rf in relevant_facts_for_prompt)
                    if not is_already_added and fact_entry.get("category") in preferred_categories:
                         relevant_facts_for_prompt.append(fact_entry)
                         category_matched_facts_count +=1

                MAX_FACTS_FOR_PROMPT = 7
                if len(relevant_facts_for_prompt) > MAX_FACTS_FOR_PROMPT:
                    relevant_facts_for_prompt = relevant_facts_for_prompt[:MAX_FACTS_FOR_PROMPT]

            learned_facts_section_str = ""
            if relevant_facts_for_prompt:
                facts_str_list = [f"- {fact.get('text', '')} (Category: {fact.get('category', 'N/A')}, Source: {fact.get('source', 'N/A')})" for fact in relevant_facts_for_prompt]
                learned_facts_section_str = "\nRelevant Learned Facts:\n" + "\n".join(facts_str_list)
            # --- End Fact Retrieval ---

            # --- Contextualization Phase (Simulated for Project Files) ---
            project_context_summary = None
            project_name_for_context = None
            prompt_lower = prompt.lower()
            
            py_file_match = re.search(r"([\w_/-]+\.py)", prompt)

            ai_assistant_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            simulated_project_base = os.path.join(ai_assistant_dir, "ai_generated_projects")
            custom_tools_base = os.path.join(ai_assistant_dir, "custom_tools")

            if py_file_match:
                tool_filename_from_prompt = py_file_match.group(1)
                safe_tool_basename = os.path.basename(tool_filename_from_prompt)
                abs_path_to_tool_file = os.path.join(custom_tools_base, safe_tool_basename)

                if os.path.exists(abs_path_to_tool_file) and os.path.isfile(abs_path_to_tool_file):
                    project_name_for_context = f"Tool: {safe_tool_basename}"
                    if is_debug_mode():
                        print(f"DynamicOrchestrator: Detected request related to '{project_name_for_context}'. Attempting to gather context from {abs_path_to_tool_file}.")
                    
                    content = await tool_system_instance.execute_tool("read_text_from_file", args=(abs_path_to_tool_file,))
                    if not content.startswith("Error:"):
                        project_context_summary = f"Context for {project_name_for_context}:\n### START FILE: {safe_tool_basename} ###\n{content}\n### END FILE: {safe_tool_basename} ###"
                    else:
                        project_context_summary = f"Context for {project_name_for_context}: Error reading file: {content}"
                else:
                    if is_debug_mode():
                        print(f"DynamicOrchestrator: Tool file '{safe_tool_basename}' (from '{tool_filename_from_prompt}') mentioned, but not found at expected path '{abs_path_to_tool_file}'. No specific tool context loaded.")
            
            project_keywords = ["project", "game", "app", "application", "webapp"] # Moved definition higher
            is_project_request = any(pk in prompt_lower for pk in project_keywords)
            tool_action_keywords = ["update", "fix", "modify", "add to", "change", "enhance", "improve", "debug"]
            is_tool_action_request = any(tak in prompt_lower for tak in tool_action_keywords)

            if project_context_summary is None and is_project_request and ("hangman game" in prompt_lower and is_tool_action_request):
                project_name_for_context = "myhangmangame" 
                if is_debug_mode():
                    print(f"DynamicOrchestrator: Detected request related to project '{project_name_for_context}'. Attempting to gather context.")
                
                context_parts = [f"Project: {project_name_for_context}\nFile Structure & Content (simplified for example):"]
                
                files_to_read_in_project = {
                    "src/game.py": os.path.join(simulated_project_base, project_name_for_context, "src", "game.py"),
                    "src/graphics.py": os.path.join(simulated_project_base, project_name_for_context, "src", "graphics.py"),
                    "src/user_input.py": os.path.join(simulated_project_base, project_name_for_context, "src", "user_input.py")
                }

                for rel_path, abs_path_to_read in files_to_read_in_project.items():
                    if os.path.exists(abs_path_to_read) and os.path.isfile(abs_path_to_read):
                        content = await tool_system_instance.execute_tool("read_text_from_file", args=(abs_path_to_read,))
                        if not content.startswith("Error:"):
                            context_parts.append(f"\n### START FILE: {rel_path} ###\n{content[:1000]}{'...' if len(content) > 1000 else ''}\n### END FILE: {rel_path} ###")
                        else:
                            context_parts.append(f"\n### FILE: {rel_path} - Error reading: {content} ###")
                    else:
                        context_parts.append(f"\n### FILE: {rel_path} - Not found at {abs_path_to_read} ###")
                project_context_summary = "\n".join(context_parts)
            # --- END Contextualization Phase ---

            if is_debug_mode():
                print(f"DynamicOrchestrator: Creating initial plan for: {prompt}")

            final_context_for_planner = project_context_summary if project_context_summary else ""
            if learned_facts_section_str:
                if final_context_for_planner:
                    final_context_for_planner += "\n\n" + learned_facts_section_str
                else:
                    final_context_for_planner = learned_facts_section_str

            self.current_plan = await self.planner.create_plan_with_llm(
                goal_description=prompt,
                available_tools=available_tools_rich,
                project_context_summary=final_context_for_planner,
                project_name_for_context=project_name_for_context,
                conversation_history=self.conversation_history # Pass history
            )

            use_hierarchical_planner = False
            if not self.current_plan:
                complex_keywords = ["project", "develop", "create a game", "build an app", "design a system", "implement a feature", "refactor module"]
                prompt_lower_for_check = prompt.lower()
                if any(keyword in prompt_lower_for_check for keyword in complex_keywords):
                    if self.hierarchical_planner:
                        use_hierarchical_planner = True
                        if is_debug_mode():
                            print("DynamicOrchestrator: Initial plan empty for complex prompt, attempting Hierarchical Planning.")
                    else:
                        if is_debug_mode():
                            print("DynamicOrchestrator: HierarchicalPlanner not available, cannot attempt complex planning for empty initial plan.")

            if use_hierarchical_planner and self.hierarchical_planner:
                log_event(
                    event_type="ORCHESTRATOR_HIERARCHICAL_PLANNING_TRIGGERED",
                    description=f"Hierarchical planning triggered for goal: {prompt}",
                    source="DynamicOrchestrator.process_prompt",
                    metadata={"original_prompt": prompt}
                )
                if is_debug_mode():
                    print(f"DEBUG: Hierarchical Planner invoked for: {prompt}")

                generated_project_plan = await self.hierarchical_planner.generate_full_project_plan(
                    user_goal=prompt,
                    project_context=final_context_for_planner
                )

                if not generated_project_plan:
                    logger.warning(f"HierarchicalPlanner failed to generate a project plan for goal: {prompt}")
                    self.current_plan = []
                    self.context['last_error_info'] = "Hierarchical planner failed to produce a detailed project plan."
                else:
                    if not self.task_manager:
                        logger.critical("TaskManager not available. Cannot create ActiveTask for hierarchical project execution.")
                        self.current_plan = []
                        self.context['last_error_info'] = "TaskManager not available, cannot execute complex project."
                    else:
                        active_hierarchical_task = self.task_manager.add_task(
                            description=f"Executing hierarchical project: {prompt[:100]}...", # Corrected order
                            task_type=ActiveTaskType.HIERARCHICAL_PROJECT_EXECUTION,
                            details={
                                "project_plan": generated_project_plan,
                                "user_goal": prompt,
                                "project_name": project_name_for_context or "Unnamed Project"
                            }
                        )
                        hierarchical_task_id = active_hierarchical_task.task_id

                        self.current_plan = [{
                            "tool_name": "execute_project_plan",
                            "args": {
                                "project_plan": generated_project_plan,
                                "parent_task_id": hierarchical_task_id,
                                "task_manager_instance": self.task_manager
                            },
                            "description": f"Execute the multi-step project plan for: {prompt[:70]}...",
                            "reasoning": "Hierarchical planner generated a detailed project breakdown, now executing it."
                        }]
                        log_event(
                            event_type="ORCHESTRATOR_HIERARCHICAL_PLAN_READY",
                            description=f"Hierarchical plan created for '{prompt}', ready for execution via execute_project_plan tool.",
                            source="DynamicOrchestrator.process_prompt",
                            metadata={"num_steps_in_project_plan": len(generated_project_plan), "parent_task_id": hierarchical_task_id}
                        )

            elif not self.current_plan:
                # The planner returned an empty plan. This could be a conversational prompt
                # or a task the planner genuinely couldn't handle.
                # Let's attempt to generate a direct, conversational response as a primary strategy.
                if self.action_executor and self.action_executor.code_service and self.action_executor.code_service.llm_provider:
                    try:
                        logger.info(f"Orchestrator: No plan created for '{prompt}'. Attempting to generate a direct conversational response.")

                        # The llm_provider is the ollama_client module itself
                        llm_module = self.action_executor.code_service.llm_provider
                        # We need get_model_for_task from config
                        from ai_assistant.config import get_model_for_task
                        model = get_model_for_task("conversational_response")

                        # New system message style prompt for conversational fallback
                        # The actual user's last message is part of self.conversation_history
                        conv_prompt_system_message = """You are a helpful and friendly AI assistant. The user's message is the last one in the provided conversation history. Please continue the conversation naturally and helpfully. If the user asked a question, try to answer it. If they made a statement, acknowledge it or ask a relevant follow-up. If you cannot help with the specific request, politely say so. Keep your response concise."""

                        if self.conversation_history: # Ensure history is not empty
                            messages_for_llm = self.conversation_history
                            conversational_response = await llm_module.invoke_ollama_model_async(
                                prompt=conv_prompt_system_message, # This will act as system prompt if ollama_client is updated
                                model_name=model,
                                messages_history=messages_for_llm
                            )
                        else:
                            # Fallback if history is somehow empty (should not happen if user prompt was added)
                            logger.warning("Conversational fallback attempted with empty history. This is unexpected.")
                            conversational_response = await llm_module.invoke_ollama_model_async(
                                prompt=f"User said: {prompt}. Respond briefly and conversationally.", # Simple prompt
                                model_name=model
                            )

                        if conversational_response and conversational_response.strip():
                            log_event(
                                event_type="ORCHESTRATOR_CONVERSATIONAL_FALLBACK",
                                description=f"No plan for '{prompt}', generated direct response.",
                                source="DynamicOrchestrator.process_prompt",
                                metadata={"response": conversational_response}
                            )
                            response_message_for_history = conversational_response
                            # Return True since we successfully handled the prompt conversationally
                            return True, response_message_for_history
                        else:
                            # Raise an error to be caught by the outer Exception or specific handling
                            logger.warning(f"Conversational model returned empty/None for '{prompt}'. Falling through.")
                            # No specific error to raise that would become last_error_info directly,
                            # so the generic message will be used.
                            # To make it more specific, we could set self.context['last_error_info'] here.
                            self.context['last_error_info'] = "Conversational model returned an empty response."


                    except Exception as e_conv:
                        logger.error(f"Failed to generate conversational fallback for '{prompt}': {e_conv}", exc_info=True)
                        # Fallthrough to the original error handling logic below if conversational response fails
                        # Store the conversational error to potentially make the error message more informative
                        self.context['last_error_info'] = f"Conversational fallback attempt failed: {str(e_conv)}"

                # This block now serves as the fallback if the conversational attempt fails or was not possible.
                technical_error_msg = self.context.get('last_error_info', "I couldn't create a plan for your request, and I was also unable to generate a conversational response.")
                user_friendly_response_final = technical_error_msg # Default to technical message
                summary_for_no_plan = self._generate_execution_summary(self.current_plan, []) # current_plan is None here

                # Attempt to rephrase this final error message
                if self.action_executor and self.action_executor.code_service and self.action_executor.code_service.llm_provider:
                    try:
                        rephrased_content = await rephrase_error_message_conversationally(
                            technical_error_message=technical_error_msg, # Use the potentially updated technical_error_msg
                            original_user_query=prompt,
                            llm_provider=self.action_executor.code_service.llm_provider
                        )
                        if rephrased_content: # Only use if rephrasing returned something non-empty
                            user_friendly_response_final = rephrased_content
                    except Exception as e_rephrase: # pragma: no cover
                        logger.error(f"Error rephrasing final plan-creation failure: {e_rephrase}", exc_info=True)
                        # user_friendly_response_final remains as it was

                response_message_for_history = user_friendly_response_final + summary_for_no_plan # Assign before return
                return False, response_message_for_history

            if is_debug_mode():
                print(f"DynamicOrchestrator: Executing plan with {len(self.current_plan)} steps")

            final_plan_attempted, results_of_final_attempt = await self.executor.execute_plan(
                prompt,
                self.current_plan,
                tool_system_instance,
                self.planner,
                self.learning_agent,
                task_manager=self.task_manager,
                notification_manager=self.notification_manager
            )
            self.current_plan = final_plan_attempted

            processed_results = []
            overall_success_of_plan = True

            if not results_of_final_attempt and final_plan_attempted :
                overall_success_of_plan = False
            elif not final_plan_attempted and not results_of_final_attempt:
                overall_success_of_plan = False

            for i, res_item in enumerate(results_of_final_attempt):
                if isinstance(res_item, dict) and res_item.get("action_type_for_executor") == "PROPOSE_TOOL_MODIFICATION":
                    action_details = res_item.get("action_details_for_executor")
                    if action_details:
                        proposed_action_for_ae = {
                            "action_type": "PROPOSE_TOOL_MODIFICATION",
                            "details": action_details,
                            "source_insight_id": f"planner_staged_mod_{str(uuid.uuid4())[:8]}"
                        }
                        log_event(
                            event_type="ORCHESTRATOR_DISPATCH_TO_ACTION_EXECUTOR",
                            description=f"Dispatching staged self-modification to ActionExecutor for tool: {action_details.get('tool_name', 'unknown_tool')}",
                            source="DynamicOrchestrator.process_prompt",
                            metadata={"action_details": action_details}
                        )
                        if not self.action_executor:
                             logger.error("ActionExecutor not initialized in DynamicOrchestrator. Cannot execute staged modification.")
                             processed_results.append({"error": "ActionExecutor not available.", "ran_successfully": False})
                             overall_success_of_plan = False
                             continue

                        ae_success = await self.action_executor.execute_action(proposed_action_for_ae)

                        processed_results.append({
                            "tool_name_original_staged": action_details.get('tool_name', 'unknown_tool_from_stage'),
                            "action_executor_result": ae_success,
                            "summary": f"Self-modification attempt for '{action_details.get('tool_name')}' {'succeeded' if ae_success else 'failed'}"
                        })
                        if not ae_success:
                            overall_success_of_plan = False
                    else: # pragma: no cover
                        processed_results.append({"error": "Invalid staged action structure from tool", "ran_successfully": False})
                        overall_success_of_plan = False
                else:
                    processed_results.append(res_item)
                    if isinstance(res_item, Exception) or \
                       (isinstance(res_item, dict) and (
                           res_item.get("error") or \
                           res_item.get("ran_successfully") is False or \
                           res_item.get("overall_status") == "failed" )):
                        overall_success_of_plan = False

            if final_plan_attempted and len(processed_results) < len(final_plan_attempted): # pragma: no cover
                overall_success_of_plan = False

            self.context.update({
                'last_results': processed_results,
                'last_success': overall_success_of_plan,
                'completed_goal': prompt if overall_success_of_plan else None
            })

            num_steps_in_final_plan = len(self.current_plan) if self.current_plan else 0

            log_event(
                event_type="ORCHESTRATOR_COMPLETE_PROCESSING",
                description=f"Completed processing prompt: {prompt}",
                source="DynamicOrchestrator.process_prompt",
                metadata={
                    "goal": prompt,
                    "success": overall_success_of_plan,
                    "num_steps": num_steps_in_final_plan
                }
            )

            response = ""
            conversational_response = None
            if self.action_executor and self.action_executor.code_service and self.action_executor.code_service.llm_provider:
                if self.current_plan or processed_results:
                    try:
                        print("ORCHESTRATOR_DEBUG: Attempting to call summarize_tool_result_conversationally")
                        conversational_response = await summarize_tool_result_conversationally(
                            original_user_query=prompt,
                            executed_plan_steps=self.current_plan if self.current_plan else [],
                            tool_results=processed_results,
                            overall_success=overall_success_of_plan,
                            llm_provider=self.action_executor.code_service.llm_provider
                        )
                        print(f"ORCHESTRATOR_DEBUG: Call to summarize_tool_result_conversationally SUCCEEDED. conversational_response='{conversational_response}'")
                    except Exception as sum_ex: # pragma: no cover
                        logger.error(f"Orchestrator: EXCEPTION during summarize_tool_result_conversationally: {sum_ex}", exc_info=True)
                        conversational_response = f"DEBUG_SUMMARIZER_CALL_EXCEPTION: Type={type(sum_ex).__name__}, Msg='{str(sum_ex)}'"
            else: # pragma: no cover
                logger.warning("LLM provider not available via ActionExecutor/CodeService for conversational summary.")
                print("ORCHESTRATOR_DEBUG: LLM provider for summarizer not available.") # Added for clarity

            print(f"ORCHESTRATOR_DEBUG_CONV_RESPONSE: Type={type(conversational_response)}, Value='{conversational_response}'")

            if conversational_response and not (isinstance(conversational_response, str) and conversational_response.startswith("DEBUG_SUMMARIZER_EXCEPTION")):
                response = conversational_response
            else: # conversational_response is None OR it's our debug exception string
                if conversational_response and conversational_response.startswith("DEBUG_SUMMARIZER_EXCEPTION"):
                    print(f"ORCHESTRATOR_DEBUG: Summarizer failed with exception, proceeding with debug string: {conversational_response}")
                else: # conversational_response was None (e.g. mock returned None, or summarizer feature off)
                    print("ORCHESTRATOR_DEBUG: conversational_response is None (or empty), proceeding to generate technical fallback.")

                execution_summary_val = "" # Default to empty string
                try:
                    # ORCHESTRATOR_DEBUG print kept, but call signature reverted
                    print("ORCHESTRATOR_DEBUG: Attempting to call _generate_execution_summary with correct ARGS")
                    execution_summary_val = self._generate_execution_summary(self.current_plan, processed_results) # Reverted call
                except Exception as es_ex:
                    logger.error(f"Error generating execution summary: {es_ex}", exc_info=True)
                    execution_summary_val = "[Execution summary generation failed]" # No leading space for placeholder
                print(f"ORCHESTRATOR_DEBUG: execution_summary_val after call = '{execution_summary_val}'")

                if overall_success_of_plan:
                    response_parts = ["Successfully completed the task."]
                    if self.current_plan and len(self.current_plan) == 1 and processed_results:
                         if not (isinstance(processed_results[0], dict) and "action_executor_result" in processed_results[0]):
                            result_single_str = str(processed_results[0])[:200] if processed_results else "No specific result."
                            response_parts.append(f"Result: {result_single_str}")
                    elif processed_results: # Not single step, but still success
                        final_res_item = processed_results[-1]
                        if isinstance(final_res_item, dict) and "summary" in final_res_item:
                            result_str = final_res_item["summary"]
                        elif not isinstance(final_res_item, Exception): # Avoid printing raw exceptions here
                            result_str = str(final_res_item)[:100]
                        else:
                            result_str = "details available in summary" # Placeholder
                        response_parts.append(f"Final step result: {result_str}")

                    response = " ".join(response_parts)
                    if response and not response.endswith(('.', '\n', '!', '?')): response += "."
                    response += execution_summary_val # Use the resilient value
                else: # overall_success_of_plan is False, and conversational_summary was None or debug string
                    technical_error_detail = "An unspecified error occurred during task execution."
                    if processed_results:
                        for res_item in processed_results: # Find the first error
                            if isinstance(res_item, Exception):
                                technical_error_detail = f"An error occurred: {type(res_item).__name__}: {str(res_item)}"
                                break
                            if isinstance(res_item, dict):
                                if "action_executor_result" in res_item and res_item["action_executor_result"] is False:
                                    technical_error_detail = f"A self-modification step reported: {res_item.get('summary', 'Failed')}"
                                    break
                                if res_item.get("error") or res_item.get("ran_successfully") is False:
                                    err_detail = res_item.get("stderr", res_item.get("error", "Unknown error from tool"))
                                    technical_error_detail = f"A tool reported an error: {str(err_detail)}"
                                    break

                    current_error_response = "Default error before rephrasing logic" # Initialize for debug
                    if self.action_executor and self.action_executor.code_service and self.action_executor.code_service.llm_provider:
                        try:
                            print("ORCHESTRATOR_DEBUG: Attempting to call rephrase_error_message_conversationally (was _handle_failed_plan_execution_or_step)")
                            # If conversational_response was the debug string, use original technical_error_detail for rephrasing
                            # Otherwise, if it was None, technical_error_detail is already set.
                            error_to_rephrase = technical_error_detail
                            if conversational_response and conversational_response.startswith("DEBUG_SUMMARIZER_EXCEPTION"):
                                # This implies the summarizer itself failed, not the plan. The error to rephrase is in conversational_response.
                                error_to_rephrase = conversational_response # Pass the debug string itself for rephrasing

                            rephrased_error_val = await rephrase_error_message_conversationally(
                                technical_error_message=error_to_rephrase,
                                original_user_query=prompt,
                                llm_provider=self.action_executor.code_service.llm_provider
                            )
                            if rephrased_error_val: # Use rephrased error if available
                                current_error_response = rephrased_error_val
                            else: # Rephraser returned None or empty
                                current_error_response = technical_error_detail # Fallback to technical error if rephrasing yields nothing
                            print(f"ORCHESTRATOR_DEBUG: Call to rephrase_error_message_conversationally SUCCEEDED. current_error_response='{current_error_response}'")
                        except Exception as hfp_ex: # pragma: no cover
                            logger.error(f"Orchestrator: EXCEPTION during rephrase_error_message_conversationally: {hfp_ex}", exc_info=True)
                            current_error_response = f"DEBUG_HANDLE_FAILED_PLAN_CALL_EXCEPTION: Type={type(hfp_ex).__name__}, Msg='{str(hfp_ex)}'"
                    else:
                        current_error_response = technical_error_detail # No LLM for rephrasing, use technical detail

                    print(f"ORCHESTRATOR_DEBUG_CURRENT_ERROR_RESPONSE_AFTER_HANDLE: Type={type(current_error_response)}, Value='{current_error_response}'")
                    response_to_build = current_error_response

                    try:
                        if response_to_build and isinstance(response_to_build, str) and response_to_build.strip() and \
                           not response_to_build.endswith(('.', '!', '?', '\n', ':')):
                            response_to_build += "."

                        if execution_summary_val:
                            response_to_build += execution_summary_val

                        response = response_to_build

                    except Exception as inner_ex:
                        logger.error(f"Orchestrator: INNER EXCEPTION during summary append: {inner_ex}", exc_info=True)
                        response = f"DEBUG_INNER_EXCEPTION_CAUGHT: Type={type(inner_ex).__name__}, Msg='{str(inner_ex)}'. SummaryValWas='{execution_summary_val}'"

            return overall_success_of_plan, response

        except Exception as e:
            # Enhanced logging for the mysterious error
            logger.error(f"DynamicOrchestrator: Top-level exception caught. Type: {type(e).__name__}, Error: {str(e)}", exc_info=True)
            logger.debug(f"DynamicOrchestrator: Full string representation of error: {repr(str(e))}")

            technical_error_msg = f"Error during orchestration: {str(e)}"
            user_friendly_response = technical_error_msg # Default response

            # Log event before attempting to rephrase
            log_event(
                event_type="ORCHESTRATOR_ERROR",
                description=f"Caught top-level exception: {type(e).__name__} - {str(e)}",
                source="DynamicOrchestrator.process_prompt",
                metadata={"error_type": type(e).__name__, "error_message": str(e), "goal": prompt, "full_error_repr": repr(str(e))}
            )

            if self.action_executor and self.action_executor.code_service and self.action_executor.code_service.llm_provider:
                try:
                    # Pass the original str(e) to rephrasing
                    rephrased_error_message = await rephrase_error_message_conversationally(
                        technical_error_message=str(e), # Use str(e) directly
                        original_user_query=prompt,
                        llm_provider=self.action_executor.code_service.llm_provider
                    )
                    if rephrased_error_message: # Check if rephrasing returned a non-empty string
                        user_friendly_response = rephrased_error_message
                    else:
                        logger.warning(f"Rephrasing returned empty for error: {str(e)}. Using technical message.")
                        # user_friendly_response remains technical_error_msg
                except Exception as e_rephrase: # pragma: no cover
                    logger.error(f"Error rephrasing top-level orchestrator error: {e_rephrase}", exc_info=True)
                    # user_friendly_response remains technical_error_msg in case of rephrasing failure
            else:
                logger.warning("LLM provider not available for error rephrasing in top-level exception handler.")

            # Defensive check: Ensure a string is always returned for the message
            if user_friendly_response is None: # Should not happen if technical_error_msg is the default
                logger.error(f"Orchestrator's user_friendly_response was None unexpectedly. Defaulting. Original error: {str(e)}")
                user_friendly_response = technical_error_msg

            return False, user_friendly_response

    async def get_current_progress(self) -> Dict[str, Any]:
        """Get the current progress and context of task execution."""
        return {
            'current_goal': self.current_goal,
            'current_plan': self.current_plan,
            'context': self.context,
            'last_success': self.context.get('last_success')
        }
