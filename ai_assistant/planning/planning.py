# Code for task planning.
from typing import Optional, Dict, Any, List
import re
import json # For parsing LLM plan string
from ai_assistant.planning.llm_argument_parser import populate_tool_arguments_with_llm
from ai_assistant.config import get_model_for_task
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async # For re-planning

class PlannerAgent:
    """
    Responsible for creating a sequence of tool invocations (a plan)
    to achieve a given goal.
    """

    # _extract_numbers, _extract_name_for_greeting, _plan_single_segment, create_plan (rule-based)
    # are kept for potential future hybrid approaches or specific simple tasks,
    # but create_plan_with_llm is the primary method.
    def _extract_numbers(self, text: str, count: int = 2) -> List[str]:
        numbers = re.findall(r'\d+(?:\.\d+)?', text)
        return numbers[:count]

    def _extract_name_for_greeting(self, text: str) -> str:
        match = re.search(
            r'(?:greet|hello to|hi to|say hello to|say hi to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', 
            text, 
            re.IGNORECASE
        )
        if match: return match.group(1)
        if "greet" in text.lower(): # Simplified fallback
            return "User"
        return "User"

    def _plan_single_segment(self, segment: str, available_tools: Dict[str, str]) -> Optional[Dict[str, Any]]:
        # Simplified for brevity, primary focus is LLM planning
        return None

    def create_plan(self, main_goal_description: str, available_tools: Dict[str, str]) -> List[Dict[str, Any]]:
        # Simplified for brevity
        return []

    async def create_plan_with_llm(
        self, 
        goal_description: str, 
        available_tools: Dict[str, Any],
        project_context_summary: Optional[str] = None,
        project_name_for_context: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        displayed_code_content: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        import json 

        MAX_CORRECTION_ATTEMPTS = 1
        current_attempt = 0
        llm_response_str: Optional[str] = None
        parsed_plan: Optional[List[Dict[str, Any]]] = None
        last_error_description: str = "No response from LLM."

        print(f"\nPlannerAgent (LLM): Attempting to create plan for goal: '{goal_description}'")

        tools_for_prompt = {}
        for tool_name, tool_data in available_tools.items():
            desc_for_prompt = tool_data.get('description', 'No description.')
            schema = tool_data.get('schema_details')
            if schema and isinstance(schema.get('parameters'), list):
                param_descs = [f"{p.get('name')} ({p.get('type')}): {p.get('description')}" for p in schema['parameters'] if isinstance(p, dict)]
                if param_descs: desc_for_prompt += " Parameters: [" + "; ".join(param_descs) + "]"
            tools_for_prompt[tool_name] = desc_for_prompt
        tools_json_string = json.dumps(tools_for_prompt, indent=2)

        project_context_section_str = ""
        if project_context_summary and project_name_for_context:
            project_context_section_str = f"\nCurrent Project Context for '{project_name_for_context}':\n---\n{project_context_summary}\n---\n"

        conversation_history_section_str = ""
        if conversation_history:
            formatted_history_lines = [f"{msg['role'].capitalize()}: {msg['content']}" for msg in conversation_history]
            if formatted_history_lines:
                conversation_history_section_str = "\n\nRecent Conversation History (Oldest to Newest):\n---\n" + "\n".join(formatted_history_lines) + "\n---\n"

        # {{learned_facts_section}} is a placeholder for future dynamic insertion by orchestrator if needed
        # For now, learned facts are passed via final_context_for_planner in orchestrator.py, which is part of project_context_summary for the planner.
        # If we want a separate section, orchestrator would need to prepare it and pass it as another kwarg.
        # For this iteration, assuming learned_facts_section is effectively part of project_context_section if orchestrator includes it there.
        # So, I will remove {{learned_facts_section}} from the template for now to avoid KeyError if not supplied.

        LLM_PLANNING_PROMPT_TEMPLATE = """**Primary Directive:** Your main task is to formulate a JSON plan of tool calls to achieve the LATEST user goal: "{goal}"

**Contextual Information (Use ONLY if directly relevant to the LATEST user goal):**
{conversation_history_section}
{project_context_section}
{displayed_code_section}
<!-- Relevant Learned Facts might be part of project_context_section or conversation_history_section if provided by orchestrator -->

**Instructions for Using Context:**
1.  **LATEST User Goal is Paramount:** The user's statement "{goal}" is your primary focus. Your plan MUST directly address this.
2.  **Conversation History:**
    *   If provided under "Recent Conversation History", use the LAST 1-2 user/assistant turns to understand pronouns (he, she, it, that) and the immediate topic related to "{goal}".
    *   **Crucial:** DO NOT let older or unrelated topics in the history distract you from the current "{goal}". If "{goal}" introduces a new topic, prioritize that new topic.
3.  **Project Context:** If provided under "Current Project Context", use this ONLY if "{goal}" explicitly asks to modify, discuss, or use that specific project. Otherwise, IGNORE it.
4.  **Currently Displayed Project Code:** If provided under "Currently Displayed Project Code", this is the HTML/CSS/JS for the content currently shown in the UI's Project Display Area. Use this as a DIRECT REFERENCE if the user's LATEST goal is to *modify* or *discuss* this specific displayed content (e.g., "change the button color," "what does this script do?"). If the goal is unrelated to the displayed content, you can largely ignore this section.
5.  **Learned Facts:** If any learned facts are implicitly part of the provided context sections, use them to make informed decisions about tools/arguments for "{goal}" and to avoid redundant actions. Only use facts pertinent to "{goal}".

**Your Task:** Based *primarily* on the LATEST user goal ("{goal}"), and using other context *only for clarification and direct relevance* to this goal, generate a plan.

**Available Tools (tool_name: description):**
{tools_json_string}

**Plan Format:** The plan *MUST* be a JSON list of step dictionaries.
Each step dictionary *MUST* contain the following keys:
- "tool_name": string (must be one of the available tools listed above)
- "args": list of strings (positional arguments for the tool). If an argument value cannot be inferred from the goal, use an empty string "" or a placeholder like "TODO_infer_arg_value".
- "kwargs": dictionary (key-value pairs of strings for keyword arguments, e.g., {{"key": "value"}}). If no keyword arguments, use an empty dictionary {{}}.

**General Knowledge & Search Tool Usage:**
- If the user's goal is a question seeking factual information about real-world entities, events, concepts, general knowledge, or current events that are not covered by other specialized tools (like system status or project management tools), you *MUST* prioritize using a web search tool (e.g., 'search_duckduckgo' or 'search_google_custom_search' if available).
- Formulate a clear, concise, and effective search query as the first argument for the chosen search tool.
- **Crucially, after any search tool step, you *MUST* add a subsequent step in the plan to call the 'process_search_results' tool.** (Arguments for process_search_results: search_query, search_results_json from "[[step_X_output]]", and optional kwargs: {{"processing_instruction": "answer_query" | "summarize_results" | ...}})

**Displaying Rich Content in UI (Project Display Area):**
- If the goal requires displaying formatted text, tables, lists, or other HTML-renderable content as a primary output (not just a simple chat message), and a tool named 'display_html_content_in_project_area' is available, you *SHOULD* use it.
- The 'display_html_content_in_project_area' tool takes one argument: `html_content` (string).
- Construct valid, well-formed HTML for the `html_content` argument. This content will be directly rendered in a designated UI panel.
- This tool is for displaying substantial content. For brief informational messages or confirmations, a standard chat response (implied, no specific tool call needed for just text output) is usually sufficient.
- If you generate HTML for display, ensure it is self-contained and does not rely on external CSS or JS files not already part of the main UI. Basic inline styles are acceptable if necessary.
- **Pausable JavaScript for Interactive Content:** If the HTML content includes JavaScript for animations, games, or other continuously running interactive elements, this JavaScript *MUST* be pausable.
    - Implement this by adding an event listener for `message` events from the parent window.
    - The script should listen for `event.data === 'pause'` and `event.data === 'resume'`.
    - On 'pause', halt animations (e.g., `cancelAnimationFrame()`, clear intervals/timeouts) and store necessary state.
    - On 'resume', restart animations/processes from their saved state.
    - Example (Conceptual - adapt to specific animation loop):
      ```html
      <script>
        let animationFrameId = null;
        let isPaused = false;
        // ... your animation variables ...

        function gameLoop() {
          if (isPaused) return;
          // ... update logic ...
          // ... drawing logic ...
          animationFrameId = requestAnimationFrame(gameLoop);
        }

        window.addEventListener('message', function(event) {
          if (event.data === 'pause') {
            isPaused = true;
            if (animationFrameId) {
              cancelAnimationFrame(animationFrameId);
              animationFrameId = null;
            }
            console.log('Project content paused');
          } else if (event.data === 'resume') {
            if (isPaused) {
              isPaused = false;
              gameLoop(); // Or however your loop is restarted
              console.log('Project content resumed');
            }
          }
        });

        // Start your game/animation
        // gameLoop();
      </script>
      ```
- Example: If the user asks to "show me the project plan as a table", and you have the plan details, you would format it as an HTML table and pass it to 'display_html_content_in_project_area'.

**Handling Purely Analytical or Conversational Goals:**
- If the LATEST user goal is primarily for analysis, explanation, or a conversational response (e.g., "summarize this code", "what is this?", "how does this work?") AND no available tools are suitable for performing the core task, you SHOULD return an empty JSON list `[]`. This signals that a direct textual response from the AI is likely more appropriate than a tool-based plan.

**Modifying Displayed HTML Content (Granular Changes):**
- If the user asks to make a *small, targeted change* to the HTML/CSS/JS content already shown in the 'Project Display Area' (which will be provided in the '{displayed_code_section}'):
    - Prioritize using the `modify_displayed_html_content` tool.
    - **Args for `modify_displayed_html_content`**:
        - `search_pattern` (string): The exact text snippet from the *current displayed code* that you want to replace. Be very specific. Look at the `{displayed_code_section}` to find a unique string to search for. For example, if changing `<p style="color: red;">`, a good search pattern might be `style="color: red;"` or just `color: red;` if it's unique enough in context.
        - `replacement_code` (string): The new code snippet that will replace the `search_pattern`.
        - `occurrence_index` (int, optional, default 0): Use 0 to replace the first match.
    - **Example:** User says "Change the text color to blue." The `{displayed_code_section}` shows `<p style="color: red;">Hello</p>`. A good plan step would be:
      `{{"tool_name": "modify_displayed_html_content", "args": ["color: red;", "color: blue;"], "kwargs": {{"occurrence_index": 0}}}}`
    - If the change is very large or involves restructuring the entire displayed content, it might be better to use `display_html_content_in_project_area` with completely new HTML. But for small tweaks, `modify_displayed_html_content` is preferred.

If the goal cannot be achieved with the available tools (and is not purely analytical/conversational as described above), or if it's unclear after considering context and search, return an empty JSON list [].
Respond ONLY with the JSON plan. Do not include any other text, comments, or explanations outside the JSON structure.
The entire response must be a single, valid JSON object (a list of steps).
JSON Plan:
"""
        
        CORRECTION_PROMPT_TEMPLATE = """Your previous attempt to generate a JSON plan had issues.
Original Goal: "{goal}"
Available Tools:
{tools_json_string}
{conversation_history_section}
{project_context_section}
{displayed_code_section} <!-- Ensure this context is also considered if relevant to the original goal -->

Your Previous Incorrect Response:
---
{previous_llm_response}
---
Error Description: {error_description}

Please try again. Generate a plan as a JSON list of step dictionaries.
Each step *MUST* be a dictionary with "tool_name" (string from available tools), "args" (list of strings, use "" or "TODO_infer_arg_value" for missing values), and "kwargs" (dictionary of string:string, use {{}} if none).
Remember the instructions about:
- Using search tools (followed by 'process_search_results').
- Using 'display_html_content_in_project_area' for rich UI content.
- Generating PAUSABLE JAVASCRIPT (listening for 'pause'/'resume' messages) for interactive JS.
- Using `modify_displayed_html_content` for small, targeted changes to existing displayed code, referencing `{displayed_code_section}`.
- Returning an empty plan `[]` if the goal is purely analytical/conversational and no tools are suitable.
Respond ONLY with the corrected JSON plan. The entire response must be a single, valid JSON list.
JSON Plan:
"""

        displayed_code_section_str = ""
        if displayed_code_content:
            # Truncate if too long to avoid excessive prompt length
            max_displayed_code_len = 2000
            truncated_code = displayed_code_content
            if len(displayed_code_content) > max_displayed_code_len:
                truncated_code = displayed_code_content[:max_displayed_code_len] + "\n[... code truncated ...]"
            displayed_code_section_str = f"\n\nCurrently Displayed Project Code (for reference if modifying):\n---\n{truncated_code}\n---\n"

        current_prompt_text = LLM_PLANNING_PROMPT_TEMPLATE.format(
            goal=goal_description,
            conversation_history_section=conversation_history_section_str,
            project_context_section=project_context_section_str,
            displayed_code_section=displayed_code_section_str,
            tools_json_string=tools_json_string
        )

        while current_attempt <= MAX_CORRECTION_ATTEMPTS:
            model_for_planning = get_model_for_task("planning")
            print(f"PlannerAgent (LLM): Attempt {current_attempt + 1}/{MAX_CORRECTION_ATTEMPTS + 1}. Sending prompt to LLM (model: {model_for_planning})...")
            if current_attempt > 0 :
                 print(f"PlannerAgent (LLM): Correction prompt (first 500 chars):\n{current_prompt_text[:500]}...\n")
            
            llm_response_str = await invoke_ollama_model_async(
                prompt=current_prompt_text,
                model_name=model_for_planning,
                messages_history=conversation_history # Pass history for chat models IF client supports it for non-chat prompts too
            )

            if not llm_response_str:
                last_error_description = f"Received no response or empty response from LLM ({model_for_planning})."
                print(f"PlannerAgent (LLM): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt_text = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description,
                        conversation_history_section=conversation_history_section_str,
                        project_context_section=project_context_section_str,
                        # Make sure displayed_code_section is passed to correction too
                        # For brevity, if it's part of project_context_section or a general context reminder is sufficient
                        # This correction prompt is getting long, let's assume the main prompt's context elements are implicitly included
                        # or the error is about the plan structure itself.
                        # For now, not explicitly adding displayed_code_section to CORRECTION_PROMPT_TEMPLATE's format string
                        # to keep it focused on the error. The LLM should remember context from initial attempt.
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str or "",
                        error_description=last_error_description
                    )
                continue

            print(f"PlannerAgent (LLM): Raw response from LLM (Attempt {current_attempt + 1}):\n---\n{llm_response_str}\n---")
            
            json_str_to_parse = llm_response_str
            match = re.search(r"```json\s*([\s\S]*?)\s*```", json_str_to_parse)
            if match: json_str_to_parse = match.group(1)
            json_str_to_parse = re.sub(r"^\s*JSON Plan:?\s*", "", json_str_to_parse.strip(), flags=re.IGNORECASE).strip()

            try:
                parsed_plan = json.loads(json_str_to_parse)
            except json.JSONDecodeError as e:
                last_error_description = f"Failed to parse JSON response. Error: {e}. Response: '{json_str_to_parse}'"
                print(f"PlannerAgent (LLM): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt_text = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description,
                        conversation_history_section=conversation_history_section_str,
                        project_context_section=project_context_section_str,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str,
                        error_description=f"Response was not valid JSON. Error: {e}"
                    )
                continue

            if not isinstance(parsed_plan, list):
                last_error_description = f"LLM returned an invalid plan format - not a list. Got: {type(parsed_plan)}"
                print(f"PlannerAgent (LLM): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                     current_prompt_text = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description,
                        conversation_history_section=conversation_history_section_str,
                        project_context_section=project_context_section_str,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str,
                        error_description=last_error_description
                    )
                parsed_plan = None
                continue

            validated_plan: List[Dict[str, Any]] = []
            valid_plan_overall = True
            for i, step in enumerate(parsed_plan):
                if not isinstance(step, dict):
                    last_error_description = f"Step {i+1} is not a dictionary. Content: {step}"
                    valid_plan_overall = False; break
                tool_name = step.get("tool_name")
                args = step.get("args", []) 
                kwargs = step.get("kwargs", {}) 
                if not tool_name or not isinstance(tool_name, str) or tool_name not in available_tools:
                    last_error_description = f"Step {i+1} has missing/invalid/unavailable tool_name '{tool_name}'. Content: {step}"
                    valid_plan_overall = False; break
                if not isinstance(args, list): args = []
                if not isinstance(kwargs, dict): kwargs = {}
                validated_plan.append({"tool_name": tool_name, "args": tuple(str(a) for a in args), "kwargs": {str(k): str(v) for k, v in kwargs.items()}})
            
            if valid_plan_overall:
                print(f"PlannerAgent (LLM): Successfully parsed and validated LLM plan (Attempt {current_attempt + 1}): {validated_plan}")
                return validated_plan
            else: # Error message already printed
                print(f"PlannerAgent (LLM): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt_text = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description,
                        conversation_history_section=conversation_history_section_str,
                        project_context_section=project_context_section_str,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str,
                        error_description=last_error_description
                    )
                parsed_plan = None
                continue
        
        print(f"PlannerAgent (LLM): All {MAX_CORRECTION_ATTEMPTS + 1} attempts to generate a valid plan failed. Last error: {last_error_description}")
        return []

    async def replan_after_failure(
        self,
        original_goal: str,
        failure_analysis: str,
        available_tools: Dict[str, Any],
        conversation_history: Optional[List[Dict[str, str]]] = None,
        ollama_model_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        
        LLM_REPLANNING_PROMPT_TEMPLATE = """The previous attempt to achieve a goal failed. You need to create a new plan.
Original Goal: "{original_goal}"

Analysis of the previous failure:
---
{failure_analysis}
---

{conversation_history_section}
Available Tools (tool_name: description):
{tools_json_string}

Based on the original goal and the failure analysis, generate a new plan to achieve the goal.
The plan *MUST* be a JSON list of step dictionaries.
Each step dictionary *MUST* contain "tool_name" (string), "args" (list of strings), and "kwargs" (dictionary of string:string).
If an argument value cannot be inferred, use an empty string "" or a placeholder like "TODO_infer_arg_value".
If you use 'search_duckduckgo', you *MUST* add a subsequent step 'process_search_results_for_answer' with "[[step_X_output]]" as an argument.

Consider the failure analysis carefully. Try to use different tools or different arguments if the previous attempt failed due to tool misuse.
If the goal seems unachievable with the available tools even considering the failure, return an empty JSON list [].

Respond ONLY with the JSON plan. Do not include any other text, comments, or explanations outside the JSON structure.
The entire response must be a single, valid JSON object (a list of steps).
JSON Plan:
"""
        MAX_CORRECTION_ATTEMPTS = 1
        current_attempt = 0
        llm_response_str: Optional[str] = None
        last_error_description: str = "No response from LLM for re-planning."

        print(f"\nPlannerAgent (Re-plan): Attempting to re-plan for goal: '{original_goal}'")
        
        tools_for_prompt_replan = {}
        if available_tools and isinstance(next(iter(available_tools.values())), dict):
            for tool_name, tool_data in available_tools.items():
                desc_for_prompt = tool_data.get('description', 'No description.')
                schema = tool_data.get('schema_details')
                if schema and isinstance(schema.get('parameters'), list):
                    param_descs = [f"{p.get('name')} ({p.get('type')}): {p.get('description')}" for p in schema['parameters'] if isinstance(p, dict)]
                    if param_descs: desc_for_prompt += " Parameters: [" + "; ".join(param_descs) + "]"
                tools_for_prompt_replan[tool_name] = desc_for_prompt
        else:
            tools_for_prompt_replan = {k: str(v) for k,v in available_tools.items()}

        tools_json_string = json.dumps(tools_for_prompt_replan, indent=2)
        model_for_replan = ollama_model_name or get_model_for_task("planning")

        conversation_history_section_str_replan = ""
        if conversation_history:
            formatted_history_lines_replan = [f"{msg['role'].capitalize()}: {msg['content']}" for msg in conversation_history]
            if formatted_history_lines_replan:
                conversation_history_section_str_replan = "\n\nRecent Conversation History (Oldest to Newest):\n---\n" + "\n".join(formatted_history_lines_replan) + "\n---\n"

        current_prompt_text = LLM_REPLANNING_PROMPT_TEMPLATE.format(
            original_goal=original_goal,
            failure_analysis=failure_analysis,
            conversation_history_section=conversation_history_section_str_replan,
            tools_json_string=tools_json_string
        )

        CORRECTION_PROMPT_TEMPLATE_REPLAN = """Your previous attempt to generate a JSON re-plan had issues.
Original Goal: "{goal}"
Failure Analysis: {failure_analysis}
Available Tools: {tools_json_string}
{conversation_history_section}
Your Previous Incorrect Response: --- {previous_llm_response} ---
Error Description: {error_description}
Please try again. Respond ONLY with the corrected JSON plan.
JSON Plan:
"""

        while current_attempt <= MAX_CORRECTION_ATTEMPTS:
            print(f"PlannerAgent (Re-plan): Attempt {current_attempt + 1}/{MAX_CORRECTION_ATTEMPTS + 1}. Sending prompt to LLM (model: {model_for_replan})...")
            if current_attempt > 0:
                 print(f"PlannerAgent (Re-plan): Correction prompt (first 500 chars):\n{current_prompt_text[:500]}...\n")

            llm_response_str = await invoke_ollama_model_async(
                prompt=current_prompt_text,
                model_name=model_for_replan,
                messages_history=conversation_history
            )

            if not llm_response_str:
                last_error_description = f"Received no response or empty response from LLM ({model_for_replan}) during re-planning."
                print(f"PlannerAgent (Re-plan): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt_text = CORRECTION_PROMPT_TEMPLATE_REPLAN.format(
                        goal=original_goal, # Corrected from goal_description
                        failure_analysis=failure_analysis,
                        conversation_history_section=conversation_history_section_str_replan,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str or "",
                        error_description=last_error_description
                    )
                continue
            
            # ... (rest of parsing and validation logic as in create_plan_with_llm) ...
            json_str_to_parse = llm_response_str
            match = re.search(r"```json\s*([\s\S]*?)\s*```", json_str_to_parse)
            if match: json_str_to_parse = match.group(1)
            json_str_to_parse = re.sub(r"^\s*JSON Plan:?\s*", "", json_str_to_parse.strip(), flags=re.IGNORECASE).strip()

            try:
                parsed_plan = json.loads(json_str_to_parse)
            except json.JSONDecodeError as e:
                last_error_description = f"Failed to parse JSON response for re-plan. Error: {e}. Response: '{json_str_to_parse}'"
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt_text = CORRECTION_PROMPT_TEMPLATE_REPLAN.format(
                        goal=original_goal,
                        failure_analysis=failure_analysis,
                        conversation_history_section=conversation_history_section_str_replan,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str,
                        error_description=f"Response was not valid JSON. Error: {e}"
                    )
                continue

            if not isinstance(parsed_plan, list):
                last_error_description = f"LLM returned an invalid re-plan format - not a list. Got: {type(parsed_plan)}"
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                     current_prompt_text = CORRECTION_PROMPT_TEMPLATE_REPLAN.format(
                        goal=original_goal,
                        failure_analysis=failure_analysis,
                        conversation_history_section=conversation_history_section_str_replan,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str,
                        error_description=last_error_description
                    )
                parsed_plan = None 
                continue

            validated_plan: List[Dict[str, Any]] = []
            valid_plan_overall = True
            for i, step in enumerate(parsed_plan):
                if not isinstance(step, dict) or \
                   not step.get("tool_name") or not isinstance(step.get("tool_name"), str) or \
                   step.get("tool_name") not in tools_for_prompt_replan:
                    last_error_description = f"Re-plan step {i+1} is invalid. Content: {step}"
                    valid_plan_overall = False; break
                args = step.get("args", [])
                kwargs = step.get("kwargs", {})
                if not isinstance(args, list): args = []
                if not isinstance(kwargs, dict): kwargs = {}
                validated_plan.append({"tool_name": step["tool_name"], "args": tuple(str(a) for a in args), "kwargs": {str(k):str(v) for k,v in kwargs.items()}})
            
            if valid_plan_overall:
                print(f"PlannerAgent (Re-plan): Successfully parsed and validated LLM re-plan (Attempt {current_attempt + 1}): {validated_plan}")
                return validated_plan
            else:
                print(f"PlannerAgent (Re-plan): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt_text = CORRECTION_PROMPT_TEMPLATE_REPLAN.format(
                        goal=original_goal,
                        failure_analysis=failure_analysis,
                        conversation_history_section=conversation_history_section_str_replan,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str,
                        error_description=last_error_description
                    )
                parsed_plan = None
                continue
        
        print(f"PlannerAgent (Re-plan): All {MAX_CORRECTION_ATTEMPTS + 1} attempts to generate a valid re-plan failed. Last error: {last_error_description}")
        return []

if __name__ == '__main__':
    class MockToolSystem:
        def list_tools(self):
            return {
                "greet_user": {"description": "Greets the user. Args: name (str)", "schema_details": {"parameters": [{"name": "name", "type": "str", "description": "Name of the user"}]}},
                "add_numbers": {"description": "Adds two numbers. Args: a (str), b (str)", "schema_details": {"parameters": [{"name": "a", "type": "str", "description": "First number"}, {"name": "b", "type": "str", "description": "Second number"}]}},
                "multiply_numbers": {"description": "Multiplies two numbers. Args: x (str), y (str)", "schema_details": {"parameters": [{"name": "x", "type": "str", "description": "First number"}, {"name": "y", "type": "str", "description": "Second number"}]}},
                "no_op_tool": {"description": "Does nothing.", "schema_details": {"parameters": []}}
            }
        def list_tools_with_sources(self): return self.list_tools()

    mock_ts = MockToolSystem()
    planner = PlannerAgent()
    # ... (rest of __main__ tests) ...
    async def test_llm_planner():
        print("\n--- Testing PlannerAgent.create_plan_with_llm (requires Ollama) ---")
        rich_available_tools = mock_ts.list_tools_with_sources()
        goal1 = "Say hi to Jane and then tell me the sum of 100 and 200."
        history1 = [
            {"role": "user", "content": "What's the weather like?"},
            {"role": "assistant", "content": "It's sunny today!"},
            {"role": "user", "content": goal1}
        ]
        try:
            plan1 = await planner.create_plan_with_llm(goal1, rich_available_tools, conversation_history=history1)
            print(f"LLM Plan for '{goal1}': {plan1}")
        except Exception as e:
            print(f"Error: {e}")
        # ... more tests ...

    if __name__ == '__main__':
        # ... sync tests ...
        import asyncio
        asyncio.run(test_llm_planner())
