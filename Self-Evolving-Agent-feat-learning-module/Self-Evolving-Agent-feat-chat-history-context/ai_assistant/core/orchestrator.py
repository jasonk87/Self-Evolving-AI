"""Dynamic orchestrator for handling complex user interactions."""

import re # Added for regex matching of filenames
import os # Added for path joining in context gathering simulation
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from ..planning.planning import PlannerAgent
from ..planning.execution import ExecutionAgent 
from ..memory.event_logger import log_event
from ..core.reflection import global_reflection_log, analyze_last_failure
from ..learning.learning import LearningAgent
from ..tools.tool_system import tool_system_instance
from ..config import is_debug_mode
from ..utils.display_utils import CLIColors, color_text # Added import
from ..execution.action_executor import ActionExecutor # Added import
from ..memory.persistent_memory import load_learned_facts # Added import
from .task_manager import TaskManager # Added import
import uuid # Added import

class DynamicOrchestrator:
    """
    Orchestrates the dynamic planning and execution of user prompts.
    Handles multi-step tasks, maintains context, and adapts plans based on feedback.
    """

    def __init__(self, 
                 planner: PlannerAgent,
                 executor: ExecutionAgent,
                 learning_agent: LearningAgent,
                 action_executor: ActionExecutor, # Added action_executor
                 task_manager: Optional[TaskManager] = None): # New parameter
        self.planner = planner
        self.executor = executor
        self.learning_agent = learning_agent
        self.action_executor = action_executor
        self.task_manager = task_manager # Store TaskManager instance
        self.context: Dict[str, Any] = {}
        self.current_goal: Optional[str] = None
        self.current_plan: Optional[List[Dict[str, Any]]] = None

    def _generate_execution_summary(self, plan: Optional[List[Dict[str, Any]]], results: List[Any]) -> str:
        if not plan: # plan can be None if initial planning failed
            return "\n\nNo actions were planned or taken."

        summary_lines = [
            color_text("\n\nHere's a summary of what I did:", CLIColors.SYSTEM_MESSAGE)
        ]

        if not plan: # Double check, though outer check should catch it
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
                        CLIColors.FAILURE # Assuming CLIColors.FAILURE exists (e.g., red)
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
                        CLIColors.SUCCESS # Assuming CLIColors.SUCCESS exists (e.g., green)
                    )
            else:
                outcome_str = color_text("No result recorded for this step.", CLIColors.WARNING) # Assuming CLIColors.WARNING
            
            args_display_parts = [f"'{str(arg_val)[:25]}{'...' if len(str(arg_val)) > 25 else ''}'" if not (isinstance(arg_val, list) or isinstance(arg_val, dict)) else f"{type(arg_val).__name__}(len:{len(arg_val)})" for arg_val in args]
            summary_lines.append(
                f"- Ran '{color_text(tool_name, CLIColors.TOOL_NAME)}' " # Assuming CLIColors.TOOL_NAME
                f"with arguments ({color_text(', '.join(args_display_parts), CLIColors.TOOL_ARGS)}): " # Assuming CLIColors.TOOL_ARGS
                f"{outcome_str}") # outcome_str is already colored
        return "\n".join(summary_lines)

    async def process_prompt(self, prompt: str) -> Tuple[bool, str]:
        """
        Process a user prompt by creating and executing a dynamic plan.
        Returns (success, response_message)
        """
        try:
            self.current_goal = prompt
            # Get richer tool information, including schemas
            available_tools_rich = tool_system_instance.list_tools_with_sources()

            # Log the start of processing
            log_event(
                event_type="ORCHESTRATOR_START_PROCESSING",
                description=f"Starting to process prompt: {prompt}",
                source="DynamicOrchestrator.process_prompt",
                metadata={"goal": prompt}
            )

            # --- Fact Retrieval ---
            all_learned_facts = load_learned_facts()
            relevant_facts_for_prompt = []
            if all_learned_facts:
                prompt_keywords = set(prompt.lower().split())
                # Keyword matching
                for fact_entry in all_learned_facts:
                    if len(relevant_facts_for_prompt) >= 7: # Hard cap to avoid too many facts early
                        break
                    fact_text_lower = fact_entry.get("text", "").lower()
                    if any(keyword in fact_text_lower for keyword in prompt_keywords):
                        relevant_facts_for_prompt.append(fact_entry)

                # Category and recency based selection
                preferred_categories = ["user_preference", "project_context", "general_knowledge"]
                additional_facts_count = 0
                max_additional_category_facts = 2

                # Iterate in reverse for recent facts (assuming they are appended)
                for fact_entry in reversed(all_learned_facts):
                    if len(relevant_facts_for_prompt) >= 7: # Check hard cap again
                        break
                    if additional_facts_count >= max_additional_category_facts:
                        break

                    is_already_added = any(rf['fact_id'] == fact_entry['fact_id'] for rf in relevant_facts_for_prompt)
                    if not is_already_added and fact_entry.get("category") in preferred_categories:
                         relevant_facts_for_prompt.append(fact_entry)
                         additional_facts_count += 1

                # Ensure a final hard cap on the total number of facts passed if many keyword matches occurred
                MAX_FACTS_FOR_PROMPT = 7
                if len(relevant_facts_for_prompt) > MAX_FACTS_FOR_PROMPT:
                    # Prioritize (e.g., by keeping keyword-matched ones first, then recent category ones)
                    # For now, simple truncation of the combined list if it exceeded.
                    # A more sophisticated approach would be to sort by relevance/recency before truncation.
                    # Current logic might already favor keyword matches if they fill up to MAX_FACTS_FOR_PROMPT.
                    # If category facts were added, they are from the end (most recent).
                    # If keyword search added many, then category might add few or none.
                    # This logic ensures we don't over-truncate if keyword search was sparse.

                    # To ensure a mix, let's take a slice from keyword matches and a slice from category matches
                    # This is getting complex for this simple heuristic. Let's simplify the cap application:
                    # If, after both keyword and category addition, we exceed, we truncate.
                    # The current logic first adds keyword matches (up to a limit of 7 implicitly by the outer loop),
                    # then adds category matches (up to 2 more, if space allows and not duplicate).
                    # So, the list could grow up to 7+2=9 in some cases. Then truncate to 7.
                    # This is still a bit rough. A better way is to collect all candidates then rank/filter.
                    # For this subtask, let's stick to the provided logic:
                    # Keyword matching (limited to 5 initially within its loop)
                    # Category matching (limited to 2 additional)
                    # Final hard cap (if total exceeds 7)

                    # Re-evaluating the provided logic snippet:
                    # The example showed keyword matching with a limit of 5.
                    # Then adding up to 2 from preferred categories.
                    # Then a hard cap of 7.
                    # Let's refine to match this intent more closely.

                # Refined selection logic:
                keyword_matched_facts = []
                prompt_keywords = set(prompt.lower().split())
                for fact_entry in all_learned_facts:
                    fact_text_lower = fact_entry.get("text", "").lower()
                    if any(keyword in fact_text_lower for keyword in prompt_keywords):
                        if len(keyword_matched_facts) < 5: # Limit keyword-matched
                            keyword_matched_facts.append(fact_entry)

                relevant_facts_for_prompt = list(keyword_matched_facts) # Start with keyword matches

                category_matched_facts_count = 0
                # Iterate in reverse for recency for category-based selection
                for fact_entry in reversed(all_learned_facts):
                    if len(relevant_facts_for_prompt) >= 7: # Check overall cap
                        break
                    if category_matched_facts_count >= 2: # Max 2 additional from categories
                        break

                    is_already_added = any(rf['fact_id'] == fact_entry['fact_id'] for rf in relevant_facts_for_prompt)
                    if not is_already_added and fact_entry.get("category") in ["user_preference", "project_context", "general_knowledge"]:
                        relevant_facts_for_prompt.append(fact_entry)
                        category_matched_facts_count +=1

                # Ensure final hard cap
                MAX_FACTS_FOR_PROMPT = 7
                if len(relevant_facts_for_prompt) > MAX_FACTS_FOR_PROMPT:
                    # This implies keyword matches were many. Prioritize them.
                    # A more sophisticated sort/filter might be needed if this truncation is too naive.
                    # For now, if keyword_matched_facts itself was > 7 (due to initial limit being 5, then adding category ones)
                    # this will truncate. If keyword_matched_facts was < 5, and category pushed it over 7, it truncates.
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
            
            # Try to find a .py filename in the prompt
            py_file_match = re.search(r"([\w_/-]+\.py)", prompt) # Regex for a .py file (e.g., my_tool.py, utils/helpers.py)

            # Keywords for intent
            project_keywords = ["project", "game", "app", "application", "webapp"]
            tool_action_keywords = ["update", "fix", "modify", "add to", "change", "enhance", "improve", "debug"]
            tool_entity_keywords = ["tool", "function", "script", "file"]

            is_project_request = any(pk in prompt_lower for pk in project_keywords)
            is_tool_action_request = any(tak in prompt_lower for tak in tool_action_keywords)
            is_tool_entity_request = any(tek in prompt_lower for tek in tool_entity_keywords)

            # Path construction helpers
            ai_assistant_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            simulated_project_base = os.path.join(ai_assistant_dir, "ai_generated_projects") # For multi-file projects
            custom_tools_base = os.path.join(ai_assistant_dir, "custom_tools") # For single tool files

            if py_file_match and (is_tool_action_request or is_tool_entity_request):
                # Likely a request to modify a specific tool file
                tool_filename_from_prompt = py_file_match.group(1)
                # Use os.path.basename to prevent path traversal if user provides a complex path
                safe_tool_basename = os.path.basename(tool_filename_from_prompt)
                
                # Attempt to locate the tool file in known directories (e.g., custom_tools)
                # This could be expanded to search other tool directories if needed.
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
            
            # If no specific .py tool file context was loaded, check for project-level context (like hangman)
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

            # --- END NEW: Contextualization Phase ---

            # Create initial plan
            if is_debug_mode():
                print(f"DynamicOrchestrator: Creating initial plan for: {prompt}")

            # Combine project context and learned facts for the planner
            final_context_for_planner = project_context_summary if project_context_summary else ""
            if learned_facts_section_str:
                if final_context_for_planner:
                    final_context_for_planner += "\n\n" + learned_facts_section_str
                else:
                    final_context_for_planner = learned_facts_section_str

            self.current_plan = await self.planner.create_plan_with_llm(
                goal_description=prompt,
                available_tools=available_tools_rich, # Pass richer tool info
                project_context_summary=final_context_for_planner,
                project_name_for_context=project_name_for_context
            )
            if not self.current_plan:
                # If initial plan creation fails, self.current_plan is None or empty
                # _generate_execution_summary will handle None plan
                summary_for_no_plan = self._generate_execution_summary(self.current_plan, [])
                return False, f"Could not create a plan for the given prompt.{summary_for_no_plan}"

            # Execute plan with potential replanning
            if is_debug_mode():
                print(f"DynamicOrchestrator: Executing plan with {len(self.current_plan)} steps")

            # MODIFIED: execute_plan now returns final_plan_attempted and results_of_final_attempt
            final_plan_attempted, results_of_final_attempt = await self.executor.execute_plan(
                prompt,
                self.current_plan, # This is the initial plan
                tool_system_instance,
                self.planner,
                self.learning_agent,
                task_manager=self.task_manager # Pass it here
            )
            # Update self.current_plan to the plan that was actually run for logging/context
            self.current_plan = final_plan_attempted

            # Process results, including handling staged self-modifications
            processed_results = []
            overall_success_of_plan = True # Assume success initially

            if not results_of_final_attempt and final_plan_attempted :
                overall_success_of_plan = False
            elif not final_plan_attempted and not results_of_final_attempt:
                overall_success_of_plan = False

            for i, res_item in enumerate(results_of_final_attempt):
                if isinstance(res_item, dict) and res_item.get("action_type_for_executor") == "PROPOSE_TOOL_MODIFICATION":
                    action_details = res_item.get("action_details_for_executor")
                    if action_details:
                        proposed_action_for_ae = {
                            "action_type": "PROPOSE_TOOL_MODIFICATION", # This is the type ActionExecutor expects
                            "details": action_details,
                            "source_insight_id": f"planner_staged_mod_{str(uuid.uuid4())[:8]}"
                        }
                        log_event(
                            event_type="ORCHESTRATOR_DISPATCH_TO_ACTION_EXECUTOR",
                            description=f"Dispatching staged self-modification to ActionExecutor for tool: {action_details.get('tool_name', 'unknown_tool')}",
                            source="DynamicOrchestrator.process_prompt",
                            metadata={"action_details": action_details}
                        )
                        # Ensure ActionExecutor is available
                        if not self.action_executor: # Should have been set in __init__
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

            # If the plan was non-empty but results are shorter than plan (e.g. critical failure mid-plan before processing all steps)
            if final_plan_attempted and len(processed_results) < len(final_plan_attempted): # pragma: no cover
                overall_success_of_plan = False


            # Update context with processed results
            self.context.update({
                'last_results': processed_results,
                'last_success': overall_success_of_plan,
                'completed_goal': prompt if overall_success_of_plan else None
            })

            # Log completion
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

            # Format response message using processed_results and overall_success_of_plan
            response = ""
            if overall_success_of_plan and self.current_plan and len(self.current_plan) == 1:
                # Check if the single step was a staged action that succeeded
                if isinstance(processed_results[0], dict) and "action_executor_result" in processed_results[0]:
                    response = f"Successfully completed the task. {processed_results[0]['summary']}"
                else:
                    tool_name_single = self.current_plan[0].get('tool_name', 'the tool')
                    result_single_str = str(processed_results[0])[:500] if processed_results else "No specific result."
                    response = f"Successfully completed the task by running '{tool_name_single}'. Result: {result_single_str}"
            else:
                if overall_success_of_plan:
                    response = "Successfully completed the multi-step task. "
                    if processed_results:
                        # Summarize final step, which might be from ActionExecutor
                        final_res_item = processed_results[-1]
                        if isinstance(final_res_item, dict) and "summary" in final_res_item:
                             result_str = final_res_item["summary"]
                        else:
                             result_str = str(final_res_item)[:200]
                        response += f"The final step's result: {result_str}"
                    else: # Should not happen if success is true and plan was multi-step
                        response += "No specific result from the final step." # pragma: no cover
                else: # Failure
                    response = "Could not complete the task. "
                    if processed_results:
                        last_error_str = "Details of the steps are in the summary below." # Default
                        for res_item in processed_results: # Find first error
                            if isinstance(res_item, Exception):
                                last_error_str = f"An error occurred: {type(res_item).__name__}: {str(res_item)[:200]}"
                                break
                            if isinstance(res_item, dict):
                                if "action_executor_result" in res_item and res_item["action_executor_result"] is False:
                                    last_error_str = f"A self-modification step reported: {res_item.get('summary', 'Failed')}"
                                    break
                                if res_item.get("error") or res_item.get("ran_successfully") is False:
                                    err_detail = res_item.get("stderr", res_item.get("error", "Unknown error from tool"))
                                    last_error_str = f"A tool reported an error: {str(err_detail)[:200]}"
                                    break
                        response += f"{last_error_str}"
                    else: # No results from final attempt, but it failed
                        response += "No specific results or errors from the last attempt." # pragma: no cover

                execution_summary = self._generate_execution_summary(self.current_plan, processed_results)
                response += execution_summary
            return overall_success_of_plan, response

        except Exception as e:
            error_msg = f"Error during orchestration: {str(e)}"
            log_event(
                event_type="ORCHESTRATOR_ERROR",
                description=error_msg,
                source="DynamicOrchestrator.process_prompt",
                metadata={"error": str(e), "goal": prompt}
            )
            return False, error_msg

    async def get_current_progress(self) -> Dict[str, Any]:
        """Get the current progress and context of task execution."""
        return {
            'current_goal': self.current_goal,
            'current_plan': self.current_plan,
            'context': self.context,
            'last_success': self.context.get('last_success')
        }
