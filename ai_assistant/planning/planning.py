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
        available_tools: Dict[str, str], # This will be Dict[str, Dict[str, Any]] from ToolSystem.list_tools_with_sources()
        project_context_summary: Optional[str] = None,
        project_name_for_context: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """ (Async)
        Creates a plan to achieve the goal_description using an LLM to generate the plan steps.
        Optionally includes project context if provided.
        """
        import json 

        MAX_CORRECTION_ATTEMPTS = 1
        current_attempt = 0
        llm_response_str: Optional[str] = None
        parsed_plan: Optional[List[Dict[str, Any]]] = None
        last_error_description: str = "No response from LLM."

        print(f"\nPlannerAgent (LLM): Attempting to create plan for goal: '{goal_description}'")

        # Prepare tools description for the LLM, including parameters from schema
        tools_for_prompt = {}
        for tool_name, tool_data in available_tools.items(): # available_tools is now richer
            desc_for_prompt = tool_data.get('description', 'No description.')
            schema = tool_data.get('schema_details')
            if schema and isinstance(schema.get('parameters'), list): # Check if parameters is a list
                param_descs = []
                for p_data in schema['parameters']:
                    if isinstance(p_data, dict): # Ensure p_data is a dictionary
                        p_name = p_data.get('name')
                        p_type = p_data.get('type')
                        p_desc = p_data.get('description')
                        param_descs.append(f"{p_name} ({p_type}): {p_desc}")
                if param_descs:
                    desc_for_prompt += " Parameters: [" + "; ".join(param_descs) + "]"
            tools_for_prompt[tool_name] = desc_for_prompt
        tools_json_string = json.dumps(tools_for_prompt, indent=2)


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

If the user's intent for a "creation" task is ambiguous between an Agent Tool and a User Project, your *first planned step* should be to use the 'request_user_clarification' tool (if available). The 'question_text' argument for this tool should ask the user to specify if they want an agent tool or a user project, e.g., question_text="Are you asking me to create a new capability/tool for myself, or to start scaffolding a new software project for you?". You can also provide a list of strings for the 'options' argument if offering choices is helpful, for example: options=["A new tool for me (the AI assistant)?", "A new software project for you to work on?"]. If 'request_user_clarification' is not available, make your best judgment based on the detail and scope of the request.

**General Guidance for Seeking Clarification:**
- **Use `request_user_clarification`**: If the user's goal is ambiguous, if required arguments for a chosen tool cannot be reliably inferred from the goal, or if there are multiple plausible interpretations that could lead to different plans, your first step should be to use the `request_user_clarification` tool.
- **Formulate Clear Questions**: For the `question_text` argument, provide a concise question that directly addresses the ambiguity or missing information.
- **Offer Options (Optional)**: For the `options` argument (a list of strings), provide choices if it helps the user narrow down their intent or provide specific details. Example: `question_text="Which file format do you prefer?", options=["CSV", "JSON", "Plain Text"]`.

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
If the user's goal is to "edit an existing agent tool", "modify an agent tool", "change how an agent tool works", or similar, your plan should generally follow these steps. If the user's feedback about which tool to edit or what specific change to make is too vague, consider using the `request_user_clarification` tool first to get more details before proceeding with these steps.
1.  **Find the tool's source code**: Use the `find_agent_tool_source` tool. The `tool_name` argument should be the name of the tool to be edited. (Assumes `find_agent_tool_source` is an available tool).
2.  **Generate code modification**: Use a code modification tool/service (e.g., a tool named `call_code_service_modify_code` that wraps `CodeService.modify_code`).
    *   The `context` argument for this tool (e.g., `GRANULAR_CODE_REFACTOR` or `SELF_FIX_TOOL`) should be chosen based on the specificity of the user's request. Prefer `GRANULAR_CODE_REFACTOR` if the user's feedback points to a specific part of the tool's code or describes a very targeted change. Use `SELF_FIX_TOOL` for more general bug fixes or broader enhancements where the exact lines of code to change are not specified by the user.
    *   The `modification_instruction` argument will be the user's description of desired changes. Strive to make this instruction as clear and specific as possible for the code modification step. If the user's feedback is general (e.g., "tool X is broken"), the `modification_instruction` should still be specific if possible by including observed symptoms or expected behavior (e.g., "Tool X produced an error [error details if known] when given input Y, expected Z. User reports it is broken."). If the user's feedback is specific (e.g., "add a parameter to tool X to handle timeouts"), use that directly.
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

**Guidance for Managing Suggestions:**
If the user wants to approve or deny a suggestion:
1. Identify the `suggestion_id`. If the user refers to a suggestion by description, you might first need to use `list_formatted_suggestions` (with appropriate filters) to find its ID.
2. Use the `manage_suggestion_status` tool.
   - `suggestion_id`: The ID of the suggestion.
   - `action`: "approve" or "deny".
   - `reason`: Any reason provided by the user.

Example:
User goal: "That idea about improving the calculator (sugg_calc123) is great, approve it."
Plan:
[
  {{
    "tool_name": "manage_suggestion_status",
    "args": ["sugg_calc123", "approve", "User stated it's a great idea."],
    "kwargs": {{}}
  }}
]

**Guidance for Iterating on User Projects (Based on Feedback):**
If the user provides feedback on a project they are working on (e.g., "My 'WebAppX' project has a bug in `main.py`," or "Add a new feature to the 'DataAnalyzer' project to plot charts," or "The 'GameProject' is not working, please fix it."), your plan should generally follow these steps:
1.  **Identify Project**: Determine the `project_identifier` (name or ID) from the user's feedback. If ambiguous, use `request_user_clarification`.
2.  **Gather Context (if needed)**:
    *   Use `list_project_files` (passing `project_identifier` and optionally a `sub_directory`) to understand the project structure if the feedback is general or implies needing to know file organization.
    *   If specific files are mentioned or relevant (e.g., "bug in `main.py`"), use `get_project_file_content` (passing `project_identifier` and the relative `file_path_in_project`) to read their content. Multiple calls may be needed for multiple files.
    *   The gathered file content(s) and file list become context for the code generation/modification step.
3.  **Plan Code Changes using `CodeService` (via wrapper tools)**:
    *   If **modifying existing project file(s)**: Plan to use a tool that wraps `CodeService.modify_code` (e.g., the conceptual `call_code_service_modify_code`).
        *   The `modification_instruction` should be derived from the user's feedback.
        *   Provide the full file content (from `get_project_file_content`) as `existing_code`.
        *   The `module_path` and `function_name` arguments for `call_code_service_modify_code` might be `null` or omitted if the change is not specific to a single function within the file. Choose a `CodeService` `context` like `SELF_FIX_TOOL` or `GRANULAR_CODE_REFACTOR` (if a specific section is targeted).
    *   If **adding new files/features** to a project: Plan to use a tool that wraps `CodeService.generate_code` (e.g., `call_code_service_generate_code_for_project` or using `HIERARCHICAL_GEN_COMPLETE_TOOL` with a `target_path` that includes the project's root path and the new file's relative path).
        *   The `prompt_or_description` for code generation should be derived from the user's requirements for the new feature/file.
4.  **Propose and Apply Changes with Review**:
    *   The output from the code generation/modification step (new/modified code string) needs to be applied to the project file.
    *   Plan to use a tool named `propose_project_file_update`. This tool handles backup, diff generation, critical review, and then applies the change if approved.
    *   Key arguments for `propose_project_file_update`:
        *   `absolute_target_filepath: str` (This would come from `get_project_file_content` if editing, e.g., `[[step_1_output.file_path]]`, or be constructed from `project_root_path` + `relative_file_path` if creating a new file).
        *   `new_file_content: str` (From the `CodeService` output, e.g., `[[step_2_output.modified_code_string]]`).
        *   `change_description: str` (User's original feedback or a summary, for review context. E.g., "User request: Fix bug in handle_request...").

Conceptual Schema for `propose_project_file_update` (for your understanding when planning):
```json
// "propose_project_file_update": {{
//   "description": "Proposes changes to a user's project file. Initiates a backup, diff generation, and a two-critic review process. Changes are only applied if approved.",
//   "parameters": [
//     {{"name": "absolute_target_filepath", "type": "str", "description": "The full, absolute path to the file to be modified or created."}},
//     {{"name": "new_file_content", "type": "str", "description": "The complete new content for the file."}},
//     {{"name": "change_description", "type": "str", "description": "A description of why this change is being proposed (e.g., user's request, bug fix details). This is used for the review context."}}
//   ],
//   "returns": {{"type": "dict", "description": "{{'status': 'success'/'error'/'rejected', 'message': str}}"}}
// }}
```

Example for Iterating on a User Project:
User goal: "In my 'WebAppX' project, the `handle_request` function in `api/routes.py` has a bug when the input is empty. Fix it to return a 400 error."
Assumed Plan (tool names are illustrative; ensure they match available tools):
[
  {{
    "tool_name": "get_project_file_content",
    "args": ["WebAppX", "api/routes.py"],
    "kwargs": {{}}
  }},
  {{
    "tool_name": "call_code_service_modify_code",
    "args": [
        null, // module_path (can be null if full file content is provided as existing_code)
        "handle_request", // function_name (if applicable, or null)
        "[[step_1_output.content]]", // existing_code (full content of api/routes.py)
        "Fix the handle_request function to return a 400 error when input is empty.", // modification_instruction
        "SELF_FIX_TOOL" // context for CodeService
    ],
    "kwargs": {{}}
  }},
  {{
    "tool_name": "propose_project_file_update",
    "args": [
        "[[step_1_output.file_path]]",
        "[[step_2_output.modified_code_string]]",
        "User request: Fix bug in handle_request in api/routes.py for WebAppX project regarding empty input."
    ],
    "kwargs": {{}}
  }}
]
Note: The `propose_project_file_update` tool initiates a process that includes backing up the original file (if it exists), generating a diff of the changes, subjecting the changes to a two-critic review, and only applying the changes if unanimously approved. This ensures safety and quality for modifications to user project files. The `[[step_1_output.file_path]]` from `get_project_file_content` provides the absolute path, suitable for `propose_project_file_update`.

**Guidance for System Status Queries:**
If the user asks about the system's current activities, overall status, or what you are working on:
- Plan to use the `get_system_status_summary` tool. You can optionally specify `active_limit` and `archived_limit` as kwargs if the user asks for more or less detail.

If the user asks for the status or details of a *specific* item (task, suggestion, or project) and provides an ID:
- Plan to use the `get_item_details_by_id` tool.
- `item_id`: The ID provided by the user.
- `item_type`: Must be one of "task", "suggestion", or "project". Infer this from the user's query.

Example 1 (Overall Status):
User goal: "What are you working on?"
Plan:
[
  {{"tool_name": "get_system_status_summary", "args": [], "kwargs": {{"active_limit": "5", "archived_limit": "3"}}}}
]

Example 2 (Specific Task Status):
User goal: "Tell me about task task_abc123."
Plan:
[
  {{"tool_name": "get_item_details_by_id", "args": ["task_abc123", "task"], "kwargs": {{}}}}
]

Example 3 (Specific Project by Name - requires ID lookup first if tool expects ID):
User goal: "How is the 'MyWebApp' project doing?"
Plan (conceptual, assumes ID is known or can be found by another tool not shown here if get_item_details_by_id only takes IDs):
[
  // Step 1 (Optional, if needed): find_project_id_by_name tool, if user gives name not ID
  // {{"tool_name": "find_project_id_by_name", "args": ["MyWebApp"], "kwargs": {{}}}},
  {{"tool_name": "get_item_details_by_id", "args": ["project_id_for_MyWebApp" /* or [[step_1_output.project_id]] */, "project"], "kwargs": {{}}}}
]
For now, assume if a name is given for a project/suggestion, the user might need to be prompted for an ID if `get_item_details_by_id` strictly needs an ID and no lookup tool is used first. Or, make your best guess for common items.

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
            project_context_section=project_context_section_str,
            tools_json_string=tools_json_string
        )

        while current_attempt <= MAX_CORRECTION_ATTEMPTS:
            model_for_planning = get_model_for_task("planning")
            print(f"PlannerAgent (LLM): Attempt {current_attempt + 1}/{MAX_CORRECTION_ATTEMPTS + 1}. Sending prompt to LLM (model: {model_for_planning})...")
            if current_attempt > 0 :
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
                        previous_llm_response=llm_response_str or "", 
                        error_description=last_error_description
                    )
                continue

            print(f"PlannerAgent (LLM): Raw response from LLM (Attempt {current_attempt + 1}):\n---\n{llm_response_str}\n---")
            
            json_str_to_parse = llm_response_str
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
                        previous_llm_response=llm_response_str, 
                        error_description=f"Response was not valid JSON. Error: {e}"
                    )
                continue

            if not isinstance(parsed_plan, list):
                last_error_description = f"LLM returned an invalid plan format - not a list. Got: {type(parsed_plan)}"
                print(f"PlannerAgent (LLM): {last_error_description}")
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                     current_prompt = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description, 
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
                    print(f"PlannerAgent (LLM): {last_error_description}")
                    valid_plan_overall = False; break
                
                tool_name = step.get("tool_name")
                args = step.get("args", []) 
                kwargs = step.get("kwargs", {}) 

                if not tool_name or not isinstance(tool_name, str):
                    last_error_description = f"Step {i+1} has missing or invalid 'tool_name'. Content: {step}"
                    print(f"PlannerAgent (LLM): {last_error_description}")
                    valid_plan_overall = False; break

                # Validate tool_name against available_tools which is now Dict[str, Dict[str, Any]]
                if tool_name not in available_tools: # Check if tool_name is a key in the richer available_tools
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
            else:
                current_attempt += 1
                if current_attempt <= MAX_CORRECTION_ATTEMPTS:
                    current_prompt = CORRECTION_PROMPT_TEMPLATE.format(
                        goal=goal_description, 
                        tools_json_string=tools_json_string, 
                        previous_llm_response=llm_response_str, 
                        error_description=last_error_description
                    )
                parsed_plan = None
                continue
        
        print(f"PlannerAgent (LLM): All {MAX_CORRECTION_ATTEMPTS + 1} attempts to generate a valid plan failed. Last error: {last_error_description}")
        return []

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
        MAX_CORRECTION_ATTEMPTS = 1
        current_attempt = 0
        llm_response_str: Optional[str] = None
        last_error_description: str = "No response from LLM for re-planning."

        print(f"\nPlannerAgent (Re-plan): Attempting to re-plan for goal: '{original_goal}'")
        
        # Prepare tools description for the LLM, including parameters from schema if available_tools is rich
        tools_for_prompt_replan = {}
        if available_tools and isinstance(next(iter(available_tools.values())), dict): # Check if it's rich format
            for tool_name, tool_data in available_tools.items():
                desc_for_prompt = tool_data.get('description', 'No description.')
                schema = tool_data.get('schema_details')
                if schema and isinstance(schema.get('parameters'), list):
                    param_descs = []
                    for p_data in schema['parameters']:
                        if isinstance(p_data, dict):
                            p_name = p_data.get('name')
                            p_type = p_data.get('type')
                            p_desc = p_data.get('description')
                            param_descs.append(f"{p_name} ({p_type}): {p_desc}")
                    if param_descs:
                        desc_for_prompt += " Parameters: [" + "; ".join(param_descs) + "]"
                tools_for_prompt_replan[tool_name] = desc_for_prompt
        else: # Fallback to old format if not rich
            tools_for_prompt_replan = available_tools

        tools_json_string = json.dumps(tools_for_prompt_replan, indent=2)

        model_for_replan = ollama_model_name or get_model_for_task("planning")

        current_prompt = LLM_REPLANNING_PROMPT_TEMPLATE.format(
            original_goal=original_goal,
            failure_analysis=failure_analysis,
            tools_json_string=tools_json_string
        )

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
                   step.get("tool_name") not in available_tools: # Check against keys of available_tools (which is tools_for_prompt_replan)
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
        def list_tools(self): # This should now return the rich format for consistency if create_plan_with_llm expects it
            return {
                "greet_user": {"description": "Greets the user. Args: name (str)", "schema_details": {"parameters": [{"name": "name", "type": "str", "description": "Name of the user"}]}},
                "add_numbers": {"description": "Adds two numbers. Args: a (str), b (str)", "schema_details": {"parameters": [{"name": "a", "type": "str", "description": "First number"}, {"name": "b", "type": "str", "description": "Second number"}]}},
                "multiply_numbers": {"description": "Multiplies two numbers. Args: x (str), y (str)", "schema_details": {"parameters": [{"name": "x", "type": "str", "description": "First number"}, {"name": "y", "type": "str", "description": "Second number"}]}},
                "no_op_tool": {"description": "Does nothing.", "schema_details": {"parameters": []}}
            }

        def list_tools_with_sources(self): # Keep this consistent with what create_plan_with_llm expects
             return self.list_tools()


    mock_ts = MockToolSystem()
    planner = PlannerAgent()

    print("\n--- Testing PlannerAgent with Argument Extraction ---")

    # Test cases for the rule-based create_plan (expects Dict[str,str] for available_tools)
    # This part of the test needs to use the old format for available_tools if create_plan isn't updated for rich format.
    # For now, create_plan is not the primary target for schema usage, create_plan_with_llm is.
    # So, we'll use a simple description-only dict for create_plan tests.
    simple_available_tools = {name: data["description"] for name, data in mock_ts.list_tools().items()}

    test_cases_rule_based = [
        ("Please greet John", [{'tool_name': 'greet_user', 'args': ('John',), 'kwargs': {}}]),
        ("Can you add 15 and 30 for me?", [{'tool_name': 'add_numbers', 'args': ('15', '30'), 'kwargs': {}}]),
    ]

    all_tests_passed_rule = True
    for i, (goal_desc, expected_plan) in enumerate(test_cases_rule_based):
        print(f"\nRule-based Test Case {i+1}: '{goal_desc}'")
        generated_plan = planner.create_plan(goal_desc, simple_available_tools)
        if generated_plan == expected_plan:
            print(f"PASS: Expected {expected_plan}")
        else:
            print(f"FAIL: Expected {expected_plan}, Got {generated_plan}")
            all_tests_passed_rule = False
    
    print(f"\n--- PlannerAgent Rule-Based Tests Finished. All Passed: {all_tests_passed_rule} ---")
    
    # Test for create_plan_with_llm (requires Ollama and the rich tool format)
    async def test_llm_planner():
        print("\n--- Testing PlannerAgent.create_plan_with_llm (requires Ollama) ---")
        # Use the rich format from list_tools_with_sources (which is same as list_tools in mock)
        rich_available_tools = mock_ts.list_tools_with_sources()

        goal1 = "Say hi to Jane and then tell me the sum of 100 and 200."
        print(f"Testing LLM plan for: {goal1}")
        try:
            plan1 = await planner.create_plan_with_llm(goal1, rich_available_tools)
            print(f"LLM Plan for '{goal1}': {plan1}")
            # Add assertions here based on expected LLM output structure
            assert isinstance(plan1, list), "Plan should be a list"
            if plan1: # If plan is not empty
                for step in plan1:
                    assert "tool_name" in step, "Each step must have a tool_name"
                    assert "args" in step, "Each step must have args"
                    assert "kwargs" in step, "Each step must have kwargs"
        except Exception as e:
            print(f"Error during create_plan_with_llm test for '{goal1}': {e}")
            print("This test might fail if Ollama is not running or the model is not available.")

        # Test with project context
        goal2 = "In my 'TestProject', add a new function to 'main.py' that prints hello."
        context2 = "File: main.py\n\nprint('hello old world')"
        print(f"Testing LLM plan for: {goal2} with context")
        try:
            plan2 = await planner.create_plan_with_llm(goal2, rich_available_tools, project_context_summary=context2, project_name_for_context="TestProject")
            print(f"LLM Plan for '{goal2}': {plan2}")
            assert isinstance(plan2, list), "Plan should be a list"

        except Exception as e:
            print(f"Error during create_plan_with_llm test for '{goal2}': {e}")


    if __name__ == '__main__':
        # For rule-based tests:
        # Loop through test_cases_rule_based as before... (this part is synchronous)
        all_tests_passed = True
        for i, (goal_desc, expected_plan) in enumerate(test_cases_rule_based): # Use the original test_cases list
            print(f"\nRule-based Test Case {i+1}: '{goal_desc}'")
            generated_plan = planner.create_plan(goal_desc, simple_available_tools) # Pass simple_available_tools
            if generated_plan == expected_plan:
                print(f"PASS: Expected {expected_plan}")
            else:
                print(f"FAIL: Expected {expected_plan}, Got {generated_plan}")
                all_tests_passed = False
        print(f"\n--- PlannerAgent Rule-Based Tests Finished. All Passed: {all_tests_passed} ---")

        # For async LLM-based tests:
        import asyncio # Ensure asyncio is imported here for the __main__ block
        asyncio.run(test_llm_planner())

# [end of Self-Evolving-Agent-feat-learning-module/Self-Evolving-Agent-feat-chat-history-context/ai_assistant/planning/planning.py]
