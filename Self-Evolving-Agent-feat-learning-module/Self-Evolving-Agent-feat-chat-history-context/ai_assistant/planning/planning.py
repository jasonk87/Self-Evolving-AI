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

    def _extract_numbers(self, text: str, count: int = 2) -> List[str]:
        """Extracts up to 'count' numbers from the text using regex."""
        numbers = re.findall(r'\d+(?:\.\d+)?', text) # Supports integers and decimals
        return numbers[:count]

    def _extract_name_for_greeting(self, text: str) -> str:
        """Extracts a name for greeting, looking for capitalized words after keywords."""
        match = re.search(
            r'(?:greet|hello to|hi to|say hello to|say hi to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', 
            text, 
            re.IGNORECASE
        )
        if match:
            return match.group(1)
        
        if "greet" in text.lower():
            words = text.split()
            for i, word in enumerate(words):
                if word.istitle() and word.lower() not in ["greet", "hello", "hi", "say", "to"]:
                    if i > 0 and words[i-1].lower() in ["greet", "to"]:
                        # Check for multi-word names like "John Doe"
                        name_parts = [word]
                        for j in range(i + 1, len(words)):
                            if words[j].istitle():
                                name_parts.append(words[j])
                            else:
                                break
                        return " ".join(name_parts)
                    elif i > 0 and not words[i-1].istitle():
                        return word 
        return "User"

    def _plan_single_segment(self, segment: str, available_tools: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """
        Attempts to plan a single tool invocation for a given text segment.
        This encapsulates the previous single-step planning logic and integrates LLM for arg population.
        """
        segment_lower = segment.lower()
        
        selected_tool_name: Optional[str] = None
        extracted_args: tuple = ()
        extracted_kwargs: Dict[str, Any] = {}
        
        # 1. Rule-based tool selection
        if "greet_user" in available_tools and \
           any(kw in segment_lower for kw in ["greet", "hello", "hi", "say hi", "say hello"]):
            selected_tool_name = "greet_user"
            name_to_greet = self._extract_name_for_greeting(segment)
            if name_to_greet and name_to_greet != "User": # If a specific name was found
                extracted_args = (name_to_greet,)
            # If name_to_greet is "User" (default), regex was weak. LLM might do better.
        
        elif "add_numbers" in available_tools and \
             any(kw in segment_lower for kw in ["add", "sum", "plus", "total of"]):
            selected_tool_name = "add_numbers"
            numbers = self._extract_numbers(segment, 2)
            if len(numbers) == 2:
                extracted_args = tuple(numbers)
            # If not 2 numbers, regex was weak. LLM might do better.

        elif "multiply_numbers" in available_tools and \
             any(kw in segment_lower for kw in ["multiply", "times", "product of"]):
            selected_tool_name = "multiply_numbers"
            numbers = self._extract_numbers(segment, 2)
            if len(numbers) == 2:
                extracted_args = tuple(numbers)
            # If not 2 numbers, regex was weak. LLM might do better.
        
        # Add other rule-based tool selections here...

        if selected_tool_name:
            tool_description = available_tools[selected_tool_name]
            
            # 2. Decide if LLM should be used for argument population
            # Strategy: Use LLM if regex extraction was weak (e.g., no args found for tools that expect them)
            # or for tools where regex is inherently difficult for args.
            use_llm_for_args = False
            if selected_tool_name in ["add_numbers", "multiply_numbers"] and not extracted_args:
                use_llm_for_args = True
                print(f"PlannerAgent: Rule-based arg extraction for '{selected_tool_name}' yielded no args. Trying LLM.")
            elif selected_tool_name == "greet_user" and (not extracted_args or extracted_args[0] == "User"):
                # If regex found default "User" or nothing, LLM might find a specific name.
                use_llm_for_args = True
                print(f"PlannerAgent: Rule-based arg extraction for '{selected_tool_name}' was weak. Trying LLM.")
            # Add other conditions for use_llm_for_args if needed for other tools

            if use_llm_for_args:
                llm_args_list, llm_kwargs_dict = populate_tool_arguments_with_llm(
                    goal_description=segment, # Use the current segment as the goal for arg population
                    tool_name=selected_tool_name,
                    tool_description=tool_description
                )
                
                # Merge strategy: LLM overrides if it provides something substantial.
                # For positional args, if LLM provides any, it usually has better context.
                if llm_args_list: # If LLM found any positional args
                    extracted_args = tuple(llm_args_list) 
                # For kwargs, merge or override. Here, simple override if LLM provides them.
                if llm_kwargs_dict:
                    extracted_kwargs = llm_kwargs_dict
            
            # Fallback for tools where regex failed and LLM also didn't provide args
            if selected_tool_name == "add_numbers" and not extracted_args:
                extracted_args = ("0", "0")
                extracted_kwargs["note"] = f"Could not infer numbers for 'add_numbers' from '{segment}'. Using defaults."
            elif selected_tool_name == "multiply_numbers" and not extracted_args:
                extracted_args = ("1", "1")
                extracted_kwargs["note"] = f"Could not infer numbers for 'multiply_numbers' from '{segment}'. Using defaults."
            elif selected_tool_name == "greet_user" and not extracted_args:
                 extracted_args = ("User",) # Default if LLM also fails for greet_user

            return {
                "tool_name": selected_tool_name,
                "args": extracted_args,
                "kwargs": extracted_kwargs
            }

        return None # No tool matched for this segment by rule-based selection

    def create_plan(self, main_goal_description: str, available_tools: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        Creates a multi-step plan to achieve the main_goal_description using available_tools.
        Splits the goal into segments and processes each.
        """
        full_plan: List[Dict[str, Any]] = []
        
        # Split by "and then" or "then" first, as these are strong indicators of sequence.
        # Using a regex that captures the delimiters to re-insert them for context or complex parsing later if needed,
        # but for now, we just split and process.
        # We use non-capturing groups for the delimiters for simpler splitting.
        segments = re.split(r'\s+(?:and then|then)\s+', main_goal_description, flags=re.IGNORECASE)
        
        processed_segments = []
        for segment in segments:
            # Further split by "and" if it seems to connect distinct actions.
            # This is heuristic. "add 5 and 7" should not be split.
            # "greet Alice and add 5 and 7" -> "greet Alice", "add 5 and 7" by the outer split.
            # "multiply 2 by 3 and greet Bob" -> needs "and" splitting.
            
            # Avoid splitting "and" if it's likely part of a number phrase like "add 5 and 7"
            # This check is very basic.
            if ' and ' in segment.lower() and not any(num_kw in segment.lower() for num_kw in ["add", "sum", "plus", "multiply", "times", "product of"]):
                 # Split only once by "and" to separate into two main actions if "and" is a primary conjunction
                sub_segments = re.split(r'\s+and\s+', segment, maxsplit=1, flags=re.IGNORECASE)
                processed_segments.extend(sub_segments)
            else:
                processed_segments.append(segment)

        for seg_idx, segment_text in enumerate(processed_segments):
            if not segment_text.strip(): # Skip empty segments
                continue
            
            print(f"PlannerAgent: Processing segment {seg_idx+1}/{len(processed_segments)}: '{segment_text}'")
            step = self._plan_single_segment(segment_text, available_tools)
            if step:
                full_plan.append(step)
            else:
                print(f"PlannerAgent: No specific tool action planned for segment: '{segment_text}'")

        # If no steps were generated at all from any segment, and no_op_tool is available, use it.
        if not full_plan and "no_op_tool" in available_tools:
            full_plan.append({
                "tool_name": "no_op_tool",
                "args": (),
                "kwargs": {} # Ensure no_op_tool is called without unexpected keyword arguments
            })
        elif not full_plan:
            print(f"Planner: Could not find any suitable tool or create a plan for the goal: '{main_goal_description}'")

        print(f"PlannerAgent: Generated plan for '{main_goal_description}': {full_plan}")
        return full_plan

    async def create_plan_with_llm(
        self, 
        goal_description: str, 
        available_tools: Dict[str, str],
        project_context_summary: Optional[str] = None, # NEW: For providing project file contents
        project_name_for_context: Optional[str] = None # NEW: Name of the project for context
    ) -> List[Dict[str, Any]]:
        """ (Async)
        Creates a plan to achieve the goal_description using an LLM to generate the plan steps.
        Optionally includes project context if provided.
        """
        import json 
        # No longer need sync invoke_ollama_model here, will use invoke_ollama_model_async

        MAX_CORRECTION_ATTEMPTS = 1 # 1 initial attempt + 1 correction attempt
        current_attempt = 0
        llm_response_str: Optional[str] = None
        parsed_plan: Optional[List[Dict[str, Any]]] = None
        last_error_description: str = "No response from LLM."

        print(f"\nPlannerAgent (LLM): Attempting to create plan for goal: '{goal_description}'")
        tools_json_string = json.dumps(available_tools, indent=2)

        # --- NEW: Prepare project context section for the prompt ---
        PROJECT_CONTEXT_SECTION_TEMPLATE = """
Current Project Context for '{project_name}':
---
{project_context_summary}
---
When generating the plan, consider this existing project context. For example, if the goal is to "fix a bug in function X of file Y.py", your plan should likely involve reading or modifying Y.py. If the goal is to "add a feature that uses existing function Z", your plan should reflect knowledge of Z if it's in the context.
"""
        project_context_section_str = ""
        if project_context_summary and project_name_for_context:
            project_context_section_str = PROJECT_CONTEXT_SECTION_TEMPLATE.format(
                project_name=project_name_for_context,
                project_context_summary=project_context_summary
            )
        # --- END NEW ---

        # Refined Prompt Template
        LLM_PLANNING_PROMPT_TEMPLATE = """Given the user's goal: "{goal}"
{project_context_section}

**Leveraging Provided Information (Context & Facts):**
- If a "Current Project Context" (e.g., code from existing files) is provided, use it to understand the current state and how the user's goal relates to it.
- If "Relevant Learned Facts" or "Knowledge Snippets" are provided, review them carefully.
- These facts represent information the assistant already knows.
- Use these facts to:
    - Inform your choice of tools and arguments.
    - Avoid asking for information already known.
    - Avoid planning steps to re-acquire or re-learn these facts.
- If a learned fact directly helps in achieving the user's goal, incorporate this knowledge into your plan.

And the following available tools (tool_name: description):
{tools_json_string}

Generate a plan to achieve this goal. The plan *MUST* be a JSON list of step dictionaries.
Each step dictionary *MUST* contain the following keys:
- "tool_name": string (must be one of the available tools listed above)
- "args": list of strings (positional arguments for the tool). If an argument value cannot be inferred from the goal, use an empty string "" or a placeholder like "TODO_infer_arg_value".
- "kwargs": dictionary (key-value pairs of strings for keyword arguments, e.g., {{"key": "value"}}). If no keyword arguments, use an empty dictionary {{}}.

**Critical First Step: Determine User's Intent for "Creation" Tasks**
Before planning any "creation" task (e.g., "create a ...", "make a ...", "build a ..."), you *MUST* first determine if the user is requesting:
A.  The creation of a new **Agent Tool**: A specific capability or function for the AI assistant itself. These are typically single Python scripts/functions. If so, prioritize using tools like 'generate_new_tool_from_description'.
B.  The creation or scaffolding of a **User Project**: A broader software application or multi-file project that the user wants to develop. If so, prioritize tools like 'initiate_ai_project', 'generate_code_for_project_file', or 'execute_project_coding_plan'.

If the user's intent for a "creation" task is ambiguous between an Agent Tool and a User Project, your *first planned step* should be to use the 'request_user_clarification' tool (if available). The 'clarification_question' argument for this tool should ask the user to specify if they want an agent tool or a user project, e.g., "Are you asking me to create a new capability/tool for myself, or to start scaffolding a new software project for you?". If 'request_user_clarification' is not available, make your best judgment based on the detail and scope of the request.

**Preferred Project Management Tools:**
For tasks related to software project creation, code generation for specific files within a project, or building out a project based on a plan, please PREFER the following tools:
1.  `initiate_ai_project(project_name: str, project_description: str)`:
    *   Use when the user wants to start a new software project.
    *   `project_name` should be a concise, descriptive name derived from the user's goal (e.g., "MyWebApp", "DataAnalyzer").
    *   `project_description` should be the user's stated goal or a clear summary of the project's purpose.
2.  `generate_code_for_project_file(project_name: str, filename: str)`:
    *   Use when the user wants to generate code for a specific file within an *existing* project.
    *   Identify the `project_name` and the target `filename` (e.g., "main.py", "utils/helpers.js") from the user's request.
3.  `execute_project_coding_plan(project_name: str)`:
    *   Use when the user wants to generate all remaining planned code for an *existing* project according to its coding plan.
    *   Identify the `project_name` from the user's request.

**IMPORTANT DIRECTIVE FOR TOOL CREATION:**
If the user's goal is to "create a tool", "make a tool", "generate a tool", or a similar request implying the creation of new functionality that is not met by existing tools, your primary plan *MUST* be to use the "generate_new_tool_from_description" tool.
The 'tool_description' argument for this tool should be the user's stated requirements for the new tool.
Example for tool creation:
  User goal: "Make a tool that tells me the current moon phase."
  Correct Plan:
  [
    {{"tool_name": "generate_new_tool_from_description", "args": ["a tool that tells me the current moon phase"], "kwargs": {{}}}}
  ]
Do NOT attempt to fulfill the *functionality* of a requested new tool using other existing tools if the user explicitly asks to *create* a tool. Your task in such a scenario is to initiate the tool creation process.

**Guidance for Editing Existing Agent Tools:**
If the user's goal is to "edit an existing agent tool", "modify an agent tool", "change how an agent tool works", or similar, your plan should generally follow these steps:
1.  **Find the tool's source code**: Use the `find_agent_tool_source` tool. The `tool_name` argument should be the name of the tool to be edited. (Assumes `find_agent_tool_source` is an available tool).
2.  **Generate code modification**: Use a code modification tool/service (e.g., a tool named `call_code_service_modify_code` that wraps `CodeService.modify_code`).
    *   The `context` argument for this tool (e.g., `GRANULAR_CODE_REFACTOR` or `SELF_FIX_TOOL`) should be chosen based on the specificity of the user's request.
    *   The `modification_instruction` argument will be the user's description of desired changes.
    *   Provide necessary code context using outputs from the previous step: `existing_code` (from `[[step_1_output.source_code]]`), `module_path` (from `[[step_1_output.module_path]]`), and `function_name` (from `[[step_1_output.function_name]]`).
    *   If using `GRANULAR_CODE_REFACTOR`, also provide a `section_identifier` in `kwargs` if the user specifies a particular part of the code to change.
3.  **Stage the modification for review and application**: Use a tool like `stage_agent_tool_modification`. This tool gathers all necessary information for the `ActionExecutor` to later process it as a `PROPOSE_TOOL_MODIFICATION` action type.
    *   `module_path`: from `[[step_1_output.module_path]]`
    *   `function_name`: from `[[step_1_output.function_name]]`
    *   `modified_code_string`: from `[[step_2_output.modified_code_string]]` (the output of the code modification step)
    *   `change_description`: A summary of the user's original request for the change (this will be used for review context).
    *   `original_reflection_entry_id`: (Optional) If this edit is a result of a reflection or a previous failed attempt, provide the ID of the original reflection log entry. If not applicable, pass an empty string or omit.

Example for editing an agent tool:
User goal: "Modify the 'my_calculator' tool to handle division by zero by returning an error message string instead of raising an exception."
Assumed Plan (tool names like `call_code_service_modify_code` and `stage_agent_tool_modification` must be available in `tools_json_string`):
[
  {{
    "tool_name": "find_agent_tool_source",
    "args": ["my_calculator"],
    "kwargs": {{}}
  }},
  {{
    "tool_name": "call_code_service_modify_code",
    "args": ["[[step_1_output.module_path]]", "[[step_1_output.function_name]]", "[[step_1_output.source_code]]", "Handle division by zero by returning an error message string instead of raising an exception.", "GRANULAR_CODE_REFACTOR"],
    "kwargs": {{"section_identifier": "the division operation"}}
  }},
  {{
    "tool_name": "stage_agent_tool_modification",
    "args": [
        "[[step_1_output.module_path]]",
        "[[step_1_output.function_name]]",
        "[[step_2_output.modified_code_string]]",
        "User request: Modify my_calculator to handle division by zero.",
        "" // original_reflection_entry_id (empty if not applicable)
    ],
    "kwargs": {{}}
  }}
]

Example of a valid JSON plan (list with one step using a general tool):
[
  {{"tool_name": "add_numbers", "args": ["10", "20"], "kwargs": {{}}}}
]

Examples using Project Management Tools:
*   User goal: "start a new python project called 'MyWebApp' to manage a to-do list"
    Plan: `[{{"tool_name": "initiate_ai_project", "args": ["MyWebApp", "A project to manage a to-do list"], "kwargs": {{}}}}]`
*   User goal: "generate the main.py file for the MyWebApp project"
    Plan: `[{{"tool_name": "generate_code_for_project_file", "args": ["MyWebApp", "main.py"], "kwargs": {{}}}}]`
*   User goal: "build the rest of the MyWebApp project"
    Plan: `[{{"tool_name": "execute_project_coding_plan", "args": ["MyWebApp"], "kwargs": {{}}}}]`

**Important Instructions for Search and Knowledge Retrieval:**
Use tools like 'search_google_custom_search' (if available and appropriate) primarily when the goal requires CURRENT information (e.g., recent news, rapidly changing facts) or specific external knowledge that your internal knowledge base is unlikely to cover. Do NOT use search for general knowledge, creative tasks, or if the answer is likely static and well-known.
When a search is needed and both 'search_google_custom_search' and 'search_duckduckgo' are available, generally prefer 'search_google_custom_search' for comprehensive results, unless DuckDuckGo is specifically requested or more appropriate for privacy-sensitive queries.

If you determine 'search_google_custom_search' is necessary:
1.  Formulate a clear and concise search query as the first argument for the 'search_google_custom_search' tool.
2.  Optionally, you can specify the number of results by providing a 'num_results' integer (between 1 and 10) in the 'kwargs' dictionary (e.g., `{{"num_results": "5"}}`). If omitted, it defaults to 5.
3.  You *MUST* add a subsequent step in the plan to call a tool named 'process_search_results'.

The 'process_search_results' tool takes the following arguments:
    - `search_query` (string): The original search query you provided to the search tool.
    - `search_results_json` (string): The JSON output from the preceding search tool (e.g., 'search_google_custom_search' or 'search_duckduckgo'). Use "[[step_X_output]]" where X is the 1-based index of the search tool step.
    - `processing_instruction` (string, optional kwargs): Describes the desired processing. Examples:
        - `"answer_query"` (default): Generate a direct natural language answer to the original query.
        - `"summarize_results"`: Provide a concise summary of the information found.
        - `"extract_entities"`: List key entities (people, places, organizations, dates) relevant to the query found in the results.
        - `"custom_instruction:<your specific request>"`: For more specific extraction tasks, e.g., "custom_instruction:Extract the main arguments for and against the proposal."
      If omitted, the default is "answer_query".

Example of a plan involving search with Google (default processing):
```json
[
  {{
    "tool_name": "search_duckduckgo",
    "args": ["latest developments in AI regulation"],
    "kwargs": {{}}
  }},
  {{
    "tool_name": "process_search_results",
    "args": ["latest developments in AI regulation", "[[step_1_output]]"],
    "kwargs": {{}} // Defaults to "answer_query"
  }}
]
```

Example of a plan involving search (custom processing - summarization):
```json
[
  {{
    "tool_name": "search_duckduckgo",
    "args": ["recent papers on climate change impact on agriculture"],
    "kwargs": {{}}
  }},
  {{
    "tool_name": "process_search_results",
    "args": ["recent papers on climate change impact on agriculture", "[[step_1_output]]"],
    "kwargs": {{"processing_instruction": "summarize_results"}}
  }}
]
```
If the goal cannot be achieved with the available tools, or if it's unclear, return an empty JSON list [].

Respond ONLY with the JSON plan. Do not include any other text, comments, or explanations outside the JSON structure.
The entire response must be a single, valid JSON object (a list of steps).
JSON Plan:
"""
        
        CORRECTION_PROMPT_TEMPLATE = """Your previous attempt to generate a JSON plan had issues.
Original Goal: "{goal}"
Available Tools:
{tools_json_string}

Your Previous Incorrect Response:
---
{previous_llm_response}
---
Error Description: {error_description}

Please try again. Generate a plan as a JSON list of step dictionaries.
Each step *MUST* be a dictionary with "tool_name" (string from available tools), "args" (list of strings, use "" or "TODO_infer_arg_value" for missing values), and "kwargs" (dictionary of string:string, use {{}} if none).
Respond ONLY with the corrected JSON plan. The entire response must be a single, valid JSON list.
JSON Plan:
"""

        current_prompt = LLM_PLANNING_PROMPT_TEMPLATE.format(
            goal=goal_description, 
            project_context_section=project_context_section_str, # NEW
            tools_json_string=tools_json_string
        )

        while current_attempt <= MAX_CORRECTION_ATTEMPTS:
            model_for_planning = get_model_for_task("planning")
            print(f"PlannerAgent (LLM): Attempt {current_attempt + 1}/{MAX_CORRECTION_ATTEMPTS + 1}. Sending prompt to LLM (model: {model_for_planning})...")
            if current_attempt > 0 : # Only print full prompt for corrections, initial is too long
                 print(f"PlannerAgent (LLM): Correction prompt (first 500 chars):\n{current_prompt[:500]}...\n")
            
            llm_response_str = await invoke_ollama_model_async(current_prompt, model_name=model_for_planning)

            if not llm_response_str:
                last_error_description = f"Received no response or empty response from LLM ({model_for_planning})."
                print(f"PlannerAgent (LLM): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description, 
                        tools_json_string=tools_json_string, 
                        # Note: Correction prompt doesn't explicitly re-add project_context_section for brevity, assumes LLM remembers context from first fail.
                        previous_llm_response=llm_response_str or "", 
                        error_description=last_error_description
                    )
                continue # Try correction if attempts left

            print(f"PlannerAgent (LLM): Raw response from LLM (Attempt {current_attempt + 1}):\n---\n{llm_response_str}\n---")
            
            json_str_to_parse = llm_response_str
            # Sanitize the response
            match = re.search(r"```json\s*([\s\S]*?)\s*```", json_str_to_parse)
            if match:
                json_str_to_parse = match.group(1)
            
            json_str_to_parse = re.sub(r"^\s*JSON Plan:?\s*", "", json_str_to_parse.strip(), flags=re.IGNORECASE).strip()

            try:
                parsed_plan = json.loads(json_str_to_parse)
            except json.JSONDecodeError as e:
                last_error_description = f"Failed to parse JSON response. Error: {e}. Response: '{json_str_to_parse}'"
                print(f"PlannerAgent (LLM): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description, 
                        tools_json_string=tools_json_string, 
                        # Correction prompt doesn't explicitly re-add project_context_section
                        previous_llm_response=llm_response_str, 
                        error_description=f"Response was not valid JSON. Error: {e}"
                    )
                continue # Try correction

            # Validate plan structure
            if not isinstance(parsed_plan, list):
                last_error_description = f"LLM returned an invalid plan format - not a list. Got: {type(parsed_plan)}"
                print(f"PlannerAgent (LLM): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                     current_prompt = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description, 
                        tools_json_string=tools_json_string, 
                        # Correction prompt doesn't explicitly re-add project_context_section
                        previous_llm_response=llm_response_str, 
                        error_description=last_error_description
                    )
                parsed_plan = None # Invalidate plan
                continue # Try correction

            validated_plan: List[Dict[str, Any]] = []
            valid_plan_overall = True
            for i, step in enumerate(parsed_plan):
                if not isinstance(step, dict):
                    last_error_description = f"Step {i+1} is not a dictionary. Content: {step}"
                    print(f"PlannerAgent (LLM): {last_error_description}")
                    valid_plan_overall = False; break
                
                tool_name = step.get("tool_name")
                args = step.get("args", []) 
                kwargs = step.get("kwargs", {}) 

                if not tool_name or not isinstance(tool_name, str):
                    last_error_description = f"Step {i+1} has missing or invalid 'tool_name'. Content: {step}"
                    print(f"PlannerAgent (LLM): {last_error_description}")
                    valid_plan_overall = False; break
                if tool_name not in available_tools:
                    last_error_description = f"Step {i+1} uses unavailable tool '{tool_name}'. Content: {step}"
                    print(f"PlannerAgent (LLM): {last_error_description}")
                    valid_plan_overall = False; break
                if not isinstance(args, list):
                    print(f"PlannerAgent (LLM): Warning - Step {i+1} 'args' for tool '{tool_name}' is not a list. Using empty list instead. Original: {args}")
                    args = []
                if not isinstance(kwargs, dict):
                    print(f"PlannerAgent (LLM): Warning - Step {i+1} 'kwargs' for tool '{tool_name}' is not a dictionary. Using empty dict instead. Original: {kwargs}")
                    kwargs = {}
                
                validated_args = [str(arg) for arg in args]
                validated_kwargs = {str(k): str(v) for k, v in kwargs.items()}

                validated_plan.append({
                    "tool_name": tool_name,
                    "args": tuple(validated_args), 
                    "kwargs": validated_kwargs
                })
            
            if valid_plan_overall:
                print(f"PlannerAgent (LLM): Successfully parsed and validated LLM plan (Attempt {current_attempt + 1}): {validated_plan}")
                return validated_plan
            else: # Plan was structurally okay as JSON list, but content failed validation
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description, 
                        tools_json_string=tools_json_string, 
                        # Correction prompt doesn't explicitly re-add project_context_section
                        previous_llm_response=llm_response_str, 
                        error_description=last_error_description
                    )
                parsed_plan = None # Invalidate plan due to content errors
                continue # Try correction
        
        # All attempts failed
        print(f"PlannerAgent (LLM): All {MAX_CORRECTION_ATTEMPTS + 1} attempts to generate a valid plan failed. Last error: {last_error_description}")
        return [] # Return empty plan if all attempts fail

    async def replan_after_failure(self, original_goal: str, failure_analysis: str, available_tools: Dict[str, str], ollama_model_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Attempts to create a new plan after a previous plan execution failed.
        Uses an LLM to generate the new plan based on the failure analysis.
        """
        
        LLM_REPLANNING_PROMPT_TEMPLATE = """The previous attempt to achieve a goal failed. You need to create a new plan.
Original Goal: "{original_goal}"

Analysis of the previous failure:
---
{failure_analysis}
---

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
        MAX_CORRECTION_ATTEMPTS = 1 # 1 initial attempt + 1 correction attempt
        current_attempt = 0
        llm_response_str: Optional[str] = None
        last_error_description: str = "No response from LLM for re-planning."

        print(f"\nPlannerAgent (Re-plan): Attempting to re-plan for goal: '{original_goal}'")
        tools_json_string = json.dumps(available_tools, indent=2)
        
        model_for_replan = ollama_model_name or get_model_for_task("planning") # Use provided or default

        current_prompt = LLM_REPLANNING_PROMPT_TEMPLATE.format(
            original_goal=original_goal,
            failure_analysis=failure_analysis,
            tools_json_string=tools_json_string
        )

        # Using a simplified correction prompt for re-planning as the main prompt is already contextual.
        CORRECTION_PROMPT_TEMPLATE_REPLAN = """Your previous attempt to generate a JSON re-plan had issues.
Original Goal: "{goal}"
Failure Analysis: {failure_analysis}
Available Tools: {tools_json_string}
Your Previous Incorrect Response: --- {previous_llm_response} ---
Error Description: {error_description}
Please try again. Respond ONLY with the corrected JSON plan.
JSON Plan:
"""

        while current_attempt <= MAX_CORRECTION_ATTEMPTS:
            print(f"PlannerAgent (Re-plan): Attempt {current_attempt + 1}/{MAX_CORRECTION_ATTEMPTS + 1}. Sending prompt to LLM (model: {model_for_replan})...")
            if current_attempt > 0:
                 print(f"PlannerAgent (Re-plan): Correction prompt (first 500 chars):\n{current_prompt[:500]}...\n")

            llm_response_str = await invoke_ollama_model_async(current_prompt, model_name=model_for_replan)

            if not llm_response_str:
                last_error_description = f"Received no response or empty response from LLM ({model_for_replan}) during re-planning."
                print(f"PlannerAgent (Re-plan): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt = CORRECTION_PROMPT_TEMPLATE_REPLAN.format(
                        goal=original_goal,
                        failure_analysis=failure_analysis,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str or "",
                        error_description=last_error_description
                    )
                continue

            print(f"PlannerAgent (Re-plan): Raw response from LLM (Attempt {current_attempt + 1}):\n---\n{llm_response_str}\n---")
            
            json_str_to_parse = llm_response_str
            match = re.search(r"```json\s*([\s\S]*?)\s*```", json_str_to_parse)
            if match:
                json_str_to_parse = match.group(1)
            json_str_to_parse = re.sub(r"^\s*JSON Plan:?\s*", "", json_str_to_parse.strip(), flags=re.IGNORECASE).strip()

            try:
                parsed_plan = json.loads(json_str_to_parse)
            except json.JSONDecodeError as e:
                last_error_description = f"Failed to parse JSON response for re-plan. Error: {e}. Response: '{json_str_to_parse}'"
                print(f"PlannerAgent (Re-plan): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt = CORRECTION_PROMPT_TEMPLATE_REPLAN.format(
                        goal=original_goal,
                        failure_analysis=failure_analysis,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str,
                        error_description=f"Response was not valid JSON. Error: {e}"
                    )
                continue

            if not isinstance(parsed_plan, list):
                last_error_description = f"LLM returned an invalid re-plan format - not a list. Got: {type(parsed_plan)}"
                print(f"PlannerAgent (Re-plan): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                     current_prompt = CORRECTION_PROMPT_TEMPLATE_REPLAN.format(
                        goal=original_goal,
                        failure_analysis=failure_analysis,
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
                   step.get("tool_name") not in available_tools:
                    last_error_description = f"Re-plan step {i+1} is invalid (not a dict, missing/invalid tool_name, or tool not available). Content: {step}"
                    print(f"PlannerAgent (Re-plan): {last_error_description}")
                    valid_plan_overall = False; break
                
                args = step.get("args", [])
                kwargs = step.get("kwargs", {})
                if not isinstance(args, list): args = []
                if not isinstance(kwargs, dict): kwargs = {}
                
                validated_args = [str(arg) for arg in args]
                validated_kwargs = {str(k): str(v) for k, v in kwargs.items()}

                validated_plan.append({
                    "tool_name": step["tool_name"],
                    "args": tuple(validated_args),
                    "kwargs": validated_kwargs
                })
            
            if valid_plan_overall:
                print(f"PlannerAgent (Re-plan): Successfully parsed and validated LLM re-plan (Attempt {current_attempt + 1}): {validated_plan}")
                return validated_plan
            else:
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt = CORRECTION_PROMPT_TEMPLATE_REPLAN.format(
                        goal=original_goal,
                        failure_analysis=failure_analysis,
                        tools_json_string=tools_json_string,
                        previous_llm_response=llm_response_str,
                        error_description=last_error_description
                    )
                parsed_plan = None
                continue
        
        print(f"PlannerAgent (Re-plan): All {MAX_CORRECTION_ATTEMPTS + 1} attempts to generate a valid re-plan failed. Last error: {last_error_description}")
        return []


if __name__ == '__main__':
    # Example Usage and Test
    class MockToolSystem:
        def list_tools(self):
            return {
                "greet_user": "Greets the user. Args: name (str)",
                "add_numbers": "Adds two numbers. Args: a (str), b (str)", # Assume tools handle string conversion
                "multiply_numbers": "Multiplies two numbers. Args: x (str), y (str)",
                "no_op_tool": "Does nothing."
            }

    mock_ts = MockToolSystem()
    planner = PlannerAgent()

    print("\n--- Testing PlannerAgent with Argument Extraction ---")

    test_cases = [
        ("Please greet John", [{'tool_name': 'greet_user', 'args': ('John',), 'kwargs': {}}]),
        ("Say hello to Alice", [{'tool_name': 'greet_user', 'args': ('Alice',), 'kwargs': {}}]),
        ("Hi to Bob", [{'tool_name': 'greet_user', 'args': ('Bob',), 'kwargs': {}}]),
        ("greet user", [{'tool_name': 'greet_user', 'args': ('User',), 'kwargs': {}}]), # Default
        ("just greet", [{'tool_name': 'greet_user', 'args': ('User',), 'kwargs': {}}]), # Default
        ("Can you add 15 and 30 for me?", [{'tool_name': 'add_numbers', 'args': ('15', '30'), 'kwargs': {}}]),
        ("what is 25 plus 102.5?", [{'tool_name': 'add_numbers', 'args': ('25', '102.5'), 'kwargs': {}}]),
        ("sum of 7 and 3", [{'tool_name': 'add_numbers', 'args': ('7', '3'), 'kwargs': {}}]),
        ("add 5", [{'tool_name': 'add_numbers', 'args': ('0', '0'), 'kwargs': {'note': "Could not infer numbers for 'add_numbers' from 'add 5'. Using defaults."}}]), # Fallback, updated expected
        ("What is 5 times 3?", [{'tool_name': 'multiply_numbers', 'args': ('5', '3'), 'kwargs': {}}]),
        ("multiply 6 by 8.2", [{'tool_name': 'multiply_numbers', 'args': ('6', '8.2'), 'kwargs': {}}]),
        ("product of 10 and 4", [{'tool_name': 'multiply_numbers', 'args': ('10', '4'), 'kwargs': {}}]),
        ("multiply 9", [{'tool_name': 'multiply_numbers', 'args': ('1', '1'), 'kwargs': {'note': "Could not infer numbers for 'multiply_numbers' from 'multiply 9'. Using defaults."}}]), # Fallback, updated expected
        ("Do something complex", [{'tool_name': 'no_op_tool', 'args': (), 'kwargs': {"note": "No specific actions identified in the main goal."}}]), # Fallback to no_op, updated expected
        ("This is a test", [{'tool_name': 'no_op_tool', 'args': (), 'kwargs': {"note": "No specific actions identified in the main goal."}}]), # Fallback if no keywords match, updated expected
    ]

    all_tests_passed = True
    for i, (goal_desc, expected_plan) in enumerate(test_cases):
        print(f"\nTest Case {i+1}: '{goal_desc}'")
        generated_plan = planner.create_plan(goal_desc, mock_ts.list_tools())
        if generated_plan == expected_plan:
            print(f"PASS: Expected {expected_plan}")
        else:
            print(f"FAIL: Expected {expected_plan}, Got {generated_plan}")
            all_tests_passed = False
            
    # Test with a tool not existing for a keyword
    print("\nTest Case: Tool not available")
    planner_no_greet = PlannerAgent()
    mock_ts_no_greet_tools = mock_ts.list_tools().copy()
    del mock_ts_no_greet_tools["greet_user"] # Remove greet_user tool
    
    goal_desc_greet = "Greet the team"
    # Updated expected plan when a tool is missing and no_op_tool is available.
    expected_plan_no_greet = [{'tool_name': 'no_op_tool', 'args': (), 'kwargs': {"note": "No specific actions identified in the main goal."}}] 
    generated_plan_no_greet = planner_no_greet.create_plan(goal_desc_greet, mock_ts_no_greet_tools)
    if generated_plan_no_greet == expected_plan_no_greet:
        print(f"PASS (Tool not available): Expected {expected_plan_no_greet}")
    else:
        print(f"FAIL (Tool not available): Expected {expected_plan_no_greet}, Got {generated_plan_no_greet}")
        all_tests_passed = False

    print(f"\n--- PlannerAgent Tests Finished. All Passed: {all_tests_passed} ---")
    
    # Minimal async test for replan_after_failure (rudimentary)
    # Note: This requires a running Ollama instance.
    # import asyncio
    # async def test_replan():
    #     print("\n--- Testing replan_after_failure (requires Ollama) ---")
    #     test_planner = PlannerAgent()
    #     tools = mock_ts.list_tools()
    #     analysis = "The 'add_numbers' tool failed because the input 'apple' was not a number."
    #     goal = "add 5 and apple, then greet User"
    #     try:
    #         new_plan = await test_planner.replan_after_failure(goal, analysis, tools)
    #         print(f"Replan result for '{goal}': {new_plan}")
    #     except Exception as e:
    #         print(f"Error during replan_after_failure test: {e}")
    #         print("This test might fail if Ollama is not running or the model is not available.")

    # if __name__ == '__main__':
    #    asyncio.run(test_replan()) # Comment out if you don't want to run this test by default
