# Code for task planning.
from typing import Optional, Dict, Any, List, TYPE_CHECKING
import re
import json # For parsing LLM plan string
from ai_assistant.config import get_model_for_task
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async # For re-planning
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

if TYPE_CHECKING:
    from ai_assistant.core.memory_manager import MemoryManager

class PlannerAgent:
    """
    Responsible for creating a sequence of tool invocations (a plan)
    to achieve a given goal.
    """
    def __init__(self, memory_manager: Optional["MemoryManager"] = None):
        self.memory_manager = memory_manager

    def _plan_single_segment(self, segment: str, available_tools: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """
        Attempts to plan a single tool invocation for a given text segment.
        This used to contain rule-based logic for legacy tools.
        Now it mostly returns None to defer to LLM planning, or could be used for other heuristics.
        """
        # Legacy rule-based logic for 'greet_user', 'add_numbers', 'multiply_numbers' has been removed
        # to enforce ReAct pattern and use of modern conversational tools.
        
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


        if not full_plan:
            print(f"Planner: Could not find any suitable tool or create a plan for the goal: '{main_goal_description}'")

        print(f"PlannerAgent: Generated plan for '{main_goal_description}': {full_plan}")
        return full_plan

    async def create_plan_with_llm(
        self, 
        goal_description: str, 
        available_tools: Dict[str, str], # This will be Dict[str, Dict[str, Any]] from ToolSystem.list_tools_with_sources()
        project_context_summary: Optional[str] = None,
        project_name_for_context: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        last_action_report: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """ (Async)
        Creates a plan to achieve the goal_description using an LLM to generate the plan steps.
        Optionally includes project context if provided.
        """
        with tracer.start_as_current_span("planner.create_plan_with_llm"):
            return await self._create_plan_with_llm_internal(goal_description, available_tools, project_context_summary, project_name_for_context, conversation_history, last_action_report)

    async def _create_plan_with_llm_internal(
        self,
        goal_description: str,
        available_tools: Dict[str, str],
        project_context_summary: Optional[str] = None,
        project_name_for_context: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        last_action_report: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        import json 

        MAX_CORRECTION_ATTEMPTS = 1
        current_attempt = 0
        llm_response_str: Optional[str] = None
        parsed_plan: Optional[List[Dict[str, Any]]] = None
        last_error_description: str = "No response from LLM."

        print(f"\nPlannerAgent (LLM): Attempting to create plan for goal: '{goal_description}'")

        # --- Memory Retrieval (Wisdom) ---
        relevant_memory_section = ""
        if self.memory_manager:
            try:
                # Query RAG for similar past situations
                context_results = await self.memory_manager.retrieve_relevant_context(goal_description, k=3)
                if context_results:
                    memory_lines = ["Relevant Past Experiences / Knowledge:"]
                    for item in context_results:
                        text = item.get('text', '')
                        # You could also include metadata if useful, e.g., "Source: ..."
                        memory_lines.append(f"- {text}")
                    relevant_memory_section = "\n".join(memory_lines) + "\n"
                    print(f"PlannerAgent (Wisdom): Retrieved {len(context_results)} relevant memories.")
                if hasattr(self.memory_manager, "retrieve_relevant_heuristics"):
                    heuristics = self.memory_manager.retrieve_relevant_heuristics(goal_description, k=6)
                    if heuristics:
                        relevant_memory_section += "Relevant Learned Planning Guidance:\n"
                        relevant_memory_section += "\n".join(
                            f"- {item.get('heuristic', '')}" for item in heuristics if item.get("heuristic")
                        ) + "\n"
            except Exception as e:
                print(f"PlannerAgent (Wisdom): Failed to retrieve memory context: {e}")
        # ---------------------------------

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

        conversation_history_section_str = ""
        if conversation_history:
            history_lines = ["Conversation History (Use this to resolve references like 'it', 'that', 'the previous tool'):"]
            # Take last 10 turns to keep context manageable
            recent_history = conversation_history[-10:] 
            for msg in recent_history:
                role = msg.get('role', 'unknown').capitalize()
                content = msg.get('content', '')
                history_lines.append(f"- {role}: {content}")
            conversation_history_section_str = "\n".join(history_lines) + "\n"

        last_action_report_section_str = ""
        if last_action_report:
            last_action_report_section_str = f"""
=== RECENT TECHNICAL CONTEXT (CRITICAL) ===
The user's request might be related to these recent tool activities/errors:
{last_action_report}
===========================================
"""

        LLM_PLANNING_PROMPT_TEMPLATE = """Given the user's goal: "{goal}"
{conversation_history_section}
{last_action_report_section}
{project_context_section}
{relevant_memory_section}

**Leveraging Provided Information (Context & Facts):**
- If a "Current Project Context" (e.g., code from existing files) is provided, use it to understand the current state and how the user's goal relates to it.
- If "Relevant Learned Facts", "Knowledge Snippets", or "Relevant Past Experiences" are provided, review them carefully.
- These facts represent information the assistant already knows or lessons from the past.
- **CRITICAL:** You MUST prioritize "Relevant Learned Facts" and "Relevant Past Experiences" over asking the user for information.
    - If the user's name, location, or preference is listed in the facts, **USE IT**. Do NOT plan a step to ask the user "Where are you?" if the fact "User Location: Smiths Grove, KY" is present.
    - Trust the learned facts as the primary source of truth for context.
- Use these facts to:
    - Inform your choice of tools and arguments.
    - Avoid asking for information already known.
    - Avoid planning steps to re-acquire or re-learn these facts.
    - **Avoid repeating past mistakes described in the experiences.**
- If a learned fact directly helps in achieving the user's goal, incorporate this knowledge into your plan.

**Few-Shot Examples (How to Plan):**

Example 1 (Coding - Refactoring):
Goal: "Refactor utils.py to move the 'calculate_metrics' function to a new file 'metrics.py'."
Context: Project has `utils.py` (contains `calculate_metrics`, `load_data`) and `metrics.py` (does not exist).
Thought: "I need to move a function. First, I must read `utils.py` to get the function code. Then I need to create `metrics.py` with that code. Finally, I need to remove it from `utils.py` and add an import. Verification: Check dependencies."
Plan:
[
  {{"tool_name": "read_file", "args": [".../utils.py"], "kwargs": {{}}}},
  {{"tool_name": "write_to_file", "args": [".../metrics.py", "def calculate_metrics..."], "kwargs": {{}}}},
  {{"tool_name": "replace_file_content", "args": [".../utils.py", ...], "kwargs": {{}}}}
]

Example 2 (Coding - New Tool):
Goal: "Create a new tool 'system_check' in 'ai_assistant/custom_tools/system_tools.py'."
Thought: "The user wants a new agent tool. I should use `generate_new_tool_from_description` if it's a standard tool request, but since they specified a file, they might want me to write code directly. However, the instruction says to PREFER `generate_new_tool_from_description` for tool creation. Let's start with that."
Plan:
[
  {{"tool_name": "generate_new_tool_from_description", "args": ["A system check tool..."], "kwargs": {{}}}}
]

**Critical Instructions for Coding & Planning:**
1.  **VERIFY FIRST**: If we are modifying code, do you have the *current* content? If not, plan a `read_file` or `grep_search` step FIRST. Do NOT blindly overwrite.
2.  **CHECK EXISTENCE**: If creating a file, check if it already exists to avoid accidental overwrites (unless intent is replacement).
3.  **REACT PATTERN**: Think about dependencies. If I add a new file, do I need to register it? If I delete a function, who calls it?
4.  **CONTEXT USAGE**: Use the provided `Relevant Learned Facts` and `Project Context`. Don't ask the user for things you already know.

And the following available tools (tool_name: description):
{tools_json_string}

Generate a plan to achieve this goal. The plan *MUST* be a JSON list of step dictionaries.
Each step dictionary *MUST* contain the following keys:
- "tool_name": string (must be one of the available tools listed above)
- "args": list of strings (positional arguments for the tool). If a mandatory argument value cannot be inferred directly from the goal, you *MUST* add prior exploratory steps to your plan (e.g., `search_codebase`, `read_text_from_file`, `recall_facts`, or `search_duckduckgo`) to autonomously discover the required information. Do NOT use placeholders and do NOT ask the user unless absolutely critical. You are an autonomous AI.
- "kwargs": dictionary (key-value pairs of strings for keyword arguments, e.g., {{"key": "value"}}). If no keyword arguments, use an empty dictionary {{}}.

**Handling Capability Inquiries (Meta-Questions)**
If the user asks if you have a certain ability or tool (e.g., "Do you have the ability to send text messages?", "Can you check the weather?", "Are you able to create files?"), check the `Available Tools` list.
- **If the tool exists**: Do NOT try to execute the tool immediately if the user hasn't provided the necessary arguments (like recipient or message body). instead, plan to use the `get_self_awareness_info_and_converse` tool to confirm the capability conversationally (e.g., answering "Yes, I have a tool for that").
- **If the tool does NOT exist**: Plan to use `get_self_awareness_info_and_converse` to inform the user that you don't have that specific capability yet.

**Answering from Memory:**
If the user asks a question (e.g., 'Where am I?') and the answer is explicitly present in the 'Relevant Learned Facts', do NOT assume you need to call a search or retrieval tool. Instead, use the `get_self_awareness_info_and_converse` tool to state the fact directly (e.g. args=["I know from our past conversations that you are in Smiths Grove, KY."]). This prevents unnecessary tool usage and proves you are paying attention.

**Critical First Step: Determine User's Intent for "Creation" Tasks**
Before planning any "creation" task (e.g., "create a ...", "make a ...", "build a ..."), you *MUST* first determine if the user is requesting:
A.  The creation of a new **Agent Tool**: A specific capability or function for the AI assistant itself. These are typically single Python scripts/functions. If so, prioritize using tools like 'generate_new_tool_from_description'.
B.  The creation or scaffolding of a **User Project**: A broader software application or multi-file project that the user wants to develop. If so, prioritize tools like 'initiate_ai_project', 'generate_code_for_project_file', or 'execute_project_coding_plan'.

If the user's intent for a "creation" task is ambiguous between an Agent Tool and a User Project, your *first planned step* should be to use the 'get_self_awareness_info_and_converse' tool. The 'context' argument for this tool should explain that you need clarification on whether to create an agent tool or a user project. For example: context="Asking the user to specify if they want an agent tool or a user project.". This will prompt a conversational response where you can ask the question naturally.

**General Guidance for Seeking Clarification:**
**General Guidance for Seeking Clarification:**
- **Use `get_self_awareness_info_and_converse`**: If the user's goal is ambiguous, use this tool to trigger a conversational turn. Set the `context` argument to the question you need to ask.
- **Do not output a tool for clarification**: Simply planning the `get_self_awareness_info_and_converse` step is sufficient to hand control back to the conversational engine, which will then generate a response including your question.

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

**Contextual Intent Analysis (CRITICAL):**
Before generating a plan, you MUST analyze the *flow* of the specific conversation to determine the user's true intent. Use the `Conversation History` provided above as your primary guide.
1.  **Exploration Mode** (Brainstorming, "What if...", "How about...", asking for ideas):
    *   **Goal**: The user is thinking, not doing.
    *   **Action**: Your plan should prioritize **CONVERSATION**. Use `get_self_awareness_info_and_converse` to discuss the ideas, offer suggestions, or ask clarifying questions.
    *   **Restriction**: Do NOT plan heavy-handed actions like `initiate_ai_project` or `write_to_file` during this phase, even if the user mentions a specific app idea. "Talking about it" != "Building it".
2.  **Instruction Mode** (Directives, "Go ahead", "Fix it", "Start X", "Make Y"):
    *   **Goal**: The user has decided on a course of action.
    *   **Action**: Plan the necessary tools to execute the request (e.g., `initiate_ai_project`, `generate_code`).

**Example of Intent Distinction:**
*   User: "I'm thinking about a to-do app." -> **Exploration**. Plan: `get_self_awareness_info_and_converse` (Discuss features).
*   User: "That sounds good, let's make the to-do app." -> **Instruction**. Plan: `initiate_ai_project`.

**Project Initiation Safety:**
Do NOT call `initiate_ai_project` unless the user has moved from **Exploration** to **Instruction**. If in doubt, assume Exploration.

**IMPORTANT DIRECTIVE FOR TOOL CREATION:**
If the user's goal is to "create a tool", "make a tool", "generate a tool", or a similar request implying the creation of new functionality that is not met by existing tools, your primary plan *MUST* be to use the "generate_new_tool_from_description" tool.
The 'tool_description' argument for this tool should be the user's stated requirements for the new tool.
Example for tool creation:
  User goal: "Make a tool that tells me the current moon phase."
  Correct Plan:
  [
    {{"tool_name": "generate_new_tool_from_description", "args": ["a tool that tells me the current moon phase"], "kwargs": {{}}}}
  ]
Do NOT attempt to fulfill the *functionality* of a requested new tool using other existing tools (especially `execute_sandboxed_python_script`) if the user explicitly asks to *create* a tool. Your task in such a scenario is to initiate the tool creation process so the capability becomes persistent. One-off scripts are NOT tools.

**Guidance for Editing Existing Agent Tools:**
If the user's goal is to "edit an existing agent tool", "modify an agent tool", "change how an agent tool works", or similar, your plan should generally follow these steps. If the user's feedback about which tool to edit or what specific change to make is too vague, consider using `get_self_awareness_info_and_converse` first to get more details before proceeding with these steps.
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
1.  **Identify Project**: Determine the `project_identifier` (name or ID) from the user's feedback. If ambiguous, use `get_self_awareness_info_and_converse` to ask for the project name.
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
Each step *MUST* be a dictionary with "tool_name" (string from available tools), "args" (list of strings, use exploratory tools first if values are missing), and "kwargs" (dictionary of string:string, use {{}} if none).
Respond ONLY with the corrected JSON plan. The entire response must be a single, valid JSON list.
JSON Plan:
"""

        current_prompt = LLM_PLANNING_PROMPT_TEMPLATE.format(
            goal=goal_description, 
            conversation_history_section=conversation_history_section_str,
            last_action_report_section=last_action_report_section_str,
            project_context_section=project_context_section_str,
            relevant_memory_section=relevant_memory_section,
            tools_json_string=tools_json_string
        )

        while current_attempt <= MAX_CORRECTION_ATTEMPTS:
            model_for_planning = get_model_for_task("planning")
            print(f"PlannerAgent (LLM): Attempt {current_attempt + 1}/{MAX_CORRECTION_ATTEMPTS + 1}. Sending prompt to LLM (model: {model_for_planning})...")
            if current_attempt > 0 :
                 print(f"PlannerAgent (LLM): Correction prompt (first 500 chars):\n{current_prompt[:500]}...\n")
            
            # Pass task_name for model/accounting metadata.
            llm_response_str = await invoke_ollama_model_async(
                current_prompt,
                model_name=model_for_planning,
                task_name="planning"
            )

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
If a mandatory argument value cannot be inferred, you *MUST* add exploratory steps (like reading files, searching memory, or web search) to find the information autonomously. Do not use placeholders.
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

            # Explicitly pass task_name="planning" for re-planning as well
            llm_response_str = await invoke_ollama_model_async(
                current_prompt,
                model_name=model_for_replan,
                task_name="planning"
            )

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
        ("Please greet John", []),
        ("Can you add 15 and 30 for me?", []),
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
