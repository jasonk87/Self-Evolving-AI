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
        self.context: Dict[str, Any] = {}
        self.current_goal: Optional[str] = None
        self.current_plan: Optional[List[Dict[str, Any]]] = None

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

    async def process_prompt(self, prompt: str) -> Tuple[bool, str]:
        """
        Process a user prompt by creating and executing a dynamic plan.
        Returns (success, response_message)
        """
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
                project_name_for_context=project_name_for_context
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
                technical_error_msg = self.context.get('last_error_info', "Could not create a plan for the given prompt.")
                user_friendly_response = technical_error_msg
                summary_for_no_plan = self._generate_execution_summary(self.current_plan, [])

                if self.action_executor and self.action_executor.code_service and self.action_executor.code_service.llm_provider:
                    try:
                        user_friendly_error = await rephrase_error_message_conversationally(
                            technical_error_message=technical_error_msg,
                            original_user_query=prompt,
                            llm_provider=self.action_executor.code_service.llm_provider
                        )
                        user_friendly_response = user_friendly_error
                    except Exception as e_rephrase: # pragma: no cover
                        logger.error(f"Error rephrasing plan creation failure: {e_rephrase}", exc_info=True)
                return False, user_friendly_response + summary_for_no_plan

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
                       (isinstance(res_item, dict) and (res_item.get("error") or res_item.get("ran_successfully") is False)):
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
                        logger.info(f"Attempting to generate conversational summary for goal: {prompt}")
                        conversational_response = await summarize_tool_result_conversationally(
                            original_user_query=prompt,
                            executed_plan_steps=self.current_plan if self.current_plan else [],
                            tool_results=processed_results,
                            overall_success=overall_success_of_plan,
                            llm_provider=self.action_executor.code_service.llm_provider
                        )
                        logger.info(f"Conversational summary generated for goal '{prompt}'.")
                    except Exception as e_summary: # pragma: no cover
                        logger.error(f"Error generating conversational summary: {e_summary}", exc_info=True)
                        conversational_response = None
            else: # pragma: no cover
                logger.warning("LLM provider not available via ActionExecutor/CodeService for conversational summary.")

            if conversational_response:
                response = conversational_response
            else:
                fallback_response_parts = []
                if overall_success_of_plan:
                    fallback_response_parts.append("Successfully completed the task.")
                    if self.current_plan and len(self.current_plan) == 1 and processed_results:
                        if isinstance(processed_results[0], dict) and "action_executor_result" in processed_results[0]:
                            fallback_response_parts.append(processed_results[0]['summary'])
                        else:
                            result_single_str = str(processed_results[0])[:200] if processed_results else "No specific result."
                            fallback_response_parts.append(f"Result: {result_single_str}")
                    elif processed_results:
                        final_res_item = processed_results[-1]
                        if isinstance(final_res_item, dict) and "summary" in final_res_item:
                            result_str = final_res_item["summary"]
                        else:
                            result_str = str(final_res_item)[:100]
                        fallback_response_parts.append(f"Final step result: {result_str}")
                else:
                    technical_error_detail = "An unspecified error occurred during task execution."
                    if processed_results:
                        for res_item in processed_results:
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
                    # This was the rephrased error before, now assigned to response directly
                    response = "I encountered an issue." # Default before rephrasing attempt
                    if self.action_executor and self.action_executor.code_service and self.action_executor.code_service.llm_provider:
                        try:
                            response = await rephrase_error_message_conversationally(
                                technical_error_message=technical_error_detail,
                                original_user_query=prompt,
                                llm_provider=self.action_executor.code_service.llm_provider
                            )
                        except Exception as e_rephrase: # pragma: no cover
                            logger.error(f"Error rephrasing execution failure: {e_rephrase}", exc_info=True)
                            # response remains "I encountered an issue."

                if not conversational_response : # Only append summary if no conversational response was generated
                    execution_summary = self._generate_execution_summary(self.current_plan, processed_results)
                    if response and not response.endswith("."): response += "." # Ensure punctuation before adding summary
                    response += execution_summary


            return overall_success_of_plan, response

        except Exception as e:
            technical_error_msg = f"Error during orchestration: {str(e)}"
            user_friendly_response = technical_error_msg
            log_event(
                event_type="ORCHESTRATOR_ERROR",
                description=technical_error_msg,
                source="DynamicOrchestrator.process_prompt",
                metadata={"error": str(e), "goal": prompt}
            )
            if self.action_executor and self.action_executor.code_service and self.action_executor.code_service.llm_provider:
                try:
                    user_friendly_error = await rephrase_error_message_conversationally(
                        technical_error_message=str(e),
                        original_user_query=prompt,
                        llm_provider=self.action_executor.code_service.llm_provider
                    )
                    user_friendly_response = user_friendly_error
                except Exception as e_rephrase: # pragma: no cover
                    logger.error(f"Error rephrasing top-level orchestrator error: {e_rephrase}", exc_info=True)
            return False, user_friendly_response

    async def get_current_progress(self) -> Dict[str, Any]:
        """Get the current progress and context of task execution."""
        return {
            'current_goal': self.current_goal,
            'current_plan': self.current_plan,
            'context': self.context,
            'last_success': self.context.get('last_success')
        }
