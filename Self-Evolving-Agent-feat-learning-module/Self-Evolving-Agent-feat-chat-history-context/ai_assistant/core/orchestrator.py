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

class DynamicOrchestrator:
    """
    Orchestrates the dynamic planning and execution of user prompts.
    Handles multi-step tasks, maintains context, and adapts plans based on feedback.
    """

    def __init__(self, 
                 planner: PlannerAgent,
                 executor: ExecutionAgent,
                 learning_agent: LearningAgent):
        self.planner = planner
        self.executor = executor
        self.learning_agent = learning_agent
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
            available_tools = tool_system_instance.list_tools()

            # Log the start of processing
            log_event(
                event_type="ORCHESTRATOR_START_PROCESSING",
                description=f"Starting to process prompt: {prompt}",
                source="DynamicOrchestrator.process_prompt",
                metadata={"goal": prompt}
            )

            # --- NEW: Contextualization Phase (Simulated) ---
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

            self.current_plan = await self.planner.create_plan_with_llm(
                goal_description=prompt,
                available_tools=available_tools,
                project_context_summary=project_context_summary, # Pass gathered context
                project_name_for_context=project_name_for_context # Pass project name for context
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
                self.learning_agent
            )
            # Update self.current_plan to the plan that was actually run for logging/context
            self.current_plan = final_plan_attempted

            # Process results
            success = True
            if not results_of_final_attempt and final_plan_attempted : # Plan existed but no results
                success = False
            elif not final_plan_attempted and not results_of_final_attempt: # No plan, no results
                success = False # Or True if prompt was trivial and needed no plan? For now, False.
            else: # Has results, check them
                for res_item in results_of_final_attempt:
                    if isinstance(res_item, Exception):
                        success = False
                        break
                    if isinstance(res_item, dict):
                        if res_item.get("error") or res_item.get("ran_successfully") is False:
                            success = False
                            break
            # If the plan was non-empty but results are shorter than plan (e.g. critical failure mid-plan)
            if final_plan_attempted and len(results_of_final_attempt) < len(final_plan_attempted):
                success = False

            # Update context with results
            self.context.update({
                'last_results': results_of_final_attempt,
                'last_success': success,
                'completed_goal': prompt if success else None
            })

            # Log completion
            # self.current_plan is now final_plan_attempted
            num_steps_in_final_plan = len(self.current_plan) if self.current_plan else 0
            if not self.current_plan and not results_of_final_attempt and not final_plan_attempted:
                # This case means initial planning likely failed and returned empty plan
                # and orchestrator didn't return early.
                # Let's ensure num_steps is 0 if self.current_plan is None.
                num_steps_in_final_plan = 0

            log_event(
                event_type="ORCHESTRATOR_COMPLETE_PROCESSING",
                description=f"Completed processing prompt: {prompt}",
                source="DynamicOrchestrator.process_prompt",
                metadata={
                    "goal": prompt,
                    "success": success,
                    "num_steps": num_steps_in_final_plan
                }
            )

            # Format response message
            response = ""
            if success and self.current_plan and len(self.current_plan) == 1:
                tool_name_single = self.current_plan[0].get('tool_name', 'the tool')
                result_single_str = str(results_of_final_attempt[0])[:500] if results_of_final_attempt else "No specific result."
                response = f"Successfully completed the task by running '{tool_name_single}'. Result: {result_single_str}"
                # For single successful step, this response is the summary.
            else:
                if success:
                    response = "Successfully completed the multi-step task. "
                    if results_of_final_attempt:
                        result_str = str(results_of_final_attempt[-1])[:200]
                        response += f"The final step's result: {result_str}"
                    else: # Should not happen if success is true and plan was multi-step
                        response += "No specific result from the final step."
                else: # Failure
                    response = "Could not complete the task. "
                    if results_of_final_attempt:
                        last_error_str = "Details of the steps are in the summary below." # Default
                        for res_item in results_of_final_attempt: # Find first error
                            if isinstance(res_item, Exception):
                                last_error_str = f"An error occurred: {type(res_item).__name__}: {str(res_item)[:200]}"
                                break
                            if isinstance(res_item, dict) and (res_item.get("error") or res_item.get("ran_successfully") is False):
                                err_detail = res_item.get("stderr", res_item.get("error", "Unknown error from tool"))
                                last_error_str = f"A tool reported an error: {str(err_detail)[:200]}"
                                break
                        response += f"{last_error_str}"
                    else: # No results from final attempt, but it failed
                        response += "No specific results or errors from the last attempt."
                # Append detailed summary for multi-step tasks or any failure
                execution_summary = self._generate_execution_summary(self.current_plan, results_of_final_attempt)
                response += execution_summary
            return success, response

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
