# ai_assistant/code_services/service.py
import logging
from typing import Dict, Any, Optional, Tuple, List
import re
import json # For parsing metadata
import asyncio

# Assuming these imports are relative to the ai_assistant package root
from ..config import get_model_for_task, is_debug_mode
from ..core.fs_utils import write_to_file
from ..core.task_manager import TaskManager, ActiveTaskType, ActiveTaskStatus # Added
from enum import Enum as PyEnum # To avoid conflict with any other Enum class

class CodeReviewSeverity(PyEnum):
    CRITICAL = "Critical"
    MAJOR = "Major"
    MINOR = "Minor"
    INFO = "Info"
    STYLE = "Style"

LLM_CODE_REVIEW_PROMPT_TEMPLATE = """You are an expert Python code reviewer.
Your task is to review the provided Python code and identify areas for improvement.
Focus on:
- Correctness: Are there any logical errors or potential bugs?
- Clarity: Is the code easy to read and understand? Are variable and function names descriptive?
- Efficiency: Are there any performance bottlenecks or areas where the code could be more performant?
- Best Practices: Does the code adhere to common Python best practices (e.g., PEP 8, idiomatic Python)?
- Security: Are there any potential security vulnerabilities (though this is a secondary focus unless obvious)?
- Maintainability: Is the code well-structured and easy to maintain or modify?

Code to Review:
```python
{code_to_review}
```

Review Context (Optional): {review_context}
(If a review context is provided, e.g., "NEW_TOOL_REVIEW", consider any specific requirements or conventions relevant to that context if known. Otherwise, perform a general Python code review.)

Output Format:
Please provide your review as a JSON object with two main keys: "overall_summary" and "suggestions".
1.  "overall_summary": A brief (1-2 sentences) qualitative summary of the code.
2.  "suggestions": A list of JSON objects, where each object represents a specific suggestion and includes the following keys:
    - "line_start": The starting line number of the code segment relevant to your suggestion.
    - "line_end": The ending line number of the code segment. (Can be the same as line_start if it's a single line).
    - "severity": A string indicating the severity of the issue (e.g., "Critical", "Major", "Minor", "Info", "Style").
    - "comment": A detailed explanation of the issue and your suggestion for improvement.

Example of a suggestion object:
{{
  "line_start": 10,
  "line_end": 12,
  "severity": "Minor", # Ensure this matches one of: Critical, Major, Minor, Info, Style
  "comment": "The variable 'temp_val' could be renamed to 'user_input' for better clarity."
}}

Valid severity values are: "Critical", "Major", "Minor", "Info", "Style".

If you find no specific issues, return an appropriate summary and an empty list for "suggestions".
Do not include any explanations or text outside of the main JSON object in your response.

JSON Review Output:
"""

LLM_SECURITY_REVIEW_PROMPT_TEMPLATE = """You are an expert Python security reviewer.
Your task is to review the provided Python code specifically for potential security vulnerabilities.
Focus on common Python security issues such as:
- Injection vulnerabilities (e.g., SQL injection, command injection - if applicable based on code context).
- Cross-Site Scripting (XSS) vulnerabilities (if web-related).
- Insecure handling of sensitive data (e.g., hardcoded secrets, weak encryption).
- Use of outdated or vulnerable libraries (if version information is available or implied).
- Insecure deserialization.
- Insufficient input validation leading to security risks.
- Path traversal vulnerabilities.
- Open redirects (if applicable).
- Use of dangerous functions like `eval()`, `exec()`, `pickle.loads()` with untrusted data.

Code to Review:
```python
{code_to_review}
```

Review Context (Optional): {review_context}
(If a review context is provided, consider any specific security requirements or threat models relevant to that context if known. Otherwise, perform a general Python security code review.)

Output Format:
Please provide your review as a JSON object with two main keys: "overall_summary" and "suggestions".
1.  "overall_summary": A brief (1-2 sentences) qualitative summary of the code's security posture from your review.
2.  "suggestions": A list of JSON objects, where each object represents a specific potential vulnerability or security concern and includes the following keys:
  "line_start": The starting line number of tweaking the code segment relevant to your suggestion.
    - "line_end": The ending line number of the code segment.
    - "severity": A string indicating the severity of the issue. Use one of the following values: "Critical", "Major", "Minor", "Info", "Style".
    - "comment": A detailed explanation of the potential vulnerability and your recommendation for mitigation.

If you find no specific security issues, return an appropriate summary and an empty list for "suggestions".
Do not include any explanations or text outside of the main JSON object in your response.

JSON Review Output:
"""

LLM_REFACTORING_REVIEW_PROMPT_TEMPLATE = """You are an expert Python code refactoring advisor.
Your task is to review the provided Python code to identify opportunities for refactoring and improving its structure, readability, and maintainability.
Focus on aspects like:
- Code Duplication (DRY principle violations).
- Long Functions or Methods (violating Single Responsibility Principle).
- Complex Conditional Logic or Deeply Nested Structures.
- Poorly Named Variables, Functions, or Classes.
- Lack of Modularity or Cohesion.
- Opportunities to use more Pythonic idioms or built-in functions for clarity and conciseness.
- Overly complex class structures or inheritance hierarchies.
- "Code smells" that indicate underlying design problems.

Do NOT focus on minor PEP 8 style issues unless they significantly impact readability or maintainability. The primary goal is structural and design improvement.

Code to Review:
```python
{code_to_review}
```

Review Context (Optional): {review_context}
(If a review context is provided, consider any specific refactoring goals relevant to that context if known.)

Output Format:
Please provide your review as a JSON object with two main keys: "overall_summary" and "suggestions".
1.  "overall_summary": A brief (1-2 sentences) qualitative summary of the code's refactoring potential.
2.  "suggestions": A list of JSON objects, where each object represents a specific refactoring suggestion and includes the following keys:
    - "line_start": The starting line number of the code segment relevant to your suggestion.
    - "line_end": The ending line number of the code segment.
    - "severity": A string indicating the potential impact or benefit of the refactoring. Use one of the following values: "Critical", "Major", "Minor", "Info", "Style".
    - "comment": A detailed explanation of the refactoring opportunity and why it would be beneficial.
    - "suggested_replacement_code": Optional. If the suggestion involves a very simple, localized code change (e.g., renaming a variable within its scope, a minor syntactic correction), provide the exact code snippet that should replace the code from "line_start" to "line_end". Only provide this for changes you are highly confident are safe and correct. For complex refactoring, omit this field.

Example of a suggestion with `suggested_replacement_code`:
{{
  "line_start": 5,
  "line_end": 5,
  "severity": "Style",
  "comment": "Variable 'i' could be renamed to 'index' for better clarity in this context.",
  "suggested_replacement_code": "index"
}}
(Assuming line 5 was `for i in range(n):` and the suggestion is to change `i` if it's only used as an index name locally)

If the code is already very well-structured and offers no significant refactoring opportunities, return an appropriate summary and an empty list for "suggestions".
Do not include any explanations or text outside of the main JSON object in your response.

JSON Review Output:
"""

logger = logging.getLogger(__name__)
if not logger.handlers: # pragma: no cover
    if not logging.getLogger().handlers:
         logger.addHandler(logging.StreamHandler())
         logger.setLevel(logging.INFO)

LLM_NEW_TOOL_PROMPT_TEMPLATE = """Based on the following high-level description of a desired tool, your task is to generate a single Python function and associated metadata.

Tool Description: "{description}"

Instructions:
1.  **Metadata Line (First Line of Response):** At the very beginning of your response, include a line starting with '# METADATA: ' followed by a JSON string. This JSON string *MUST* contain:
    - 'suggested_function_name': A Pythonic function name (snake_case) for the generated function.
    - 'suggested_tool_name': A short, user-friendly name for tool registration (camelCase or snake_case is acceptable).
    - 'suggested_description': A concise description (max 1-2 sentences) of what the tool does, suitable for a tool registry.
    Example of the first line of the response:
    # METADATA: {{"suggested_function_name": "calculate_circle_area", "suggested_tool_name": "calculateCircleArea", "suggested_description": "Calculates the area of a circle given its radius."}}

2.  **Python Function Code (Following Metadata):** After the metadata line, provide the raw Python code for the function.
    - The function should be self-contained if possible, or use common Python standard libraries.
    - Include type hints for all parameters and the return value.
    - Include a comprehensive docstring explaining what the function does, its arguments (name, type, description), and what it returns.
    - Implement basic error handling using try-except blocks where appropriate (e.g., for type conversions if arguments might not be of the expected type, or for file operations).

Constraints:
- Respond ONLY with the metadata line followed by the raw Python code.
- Do not include any other explanations, comments outside the function's docstring (except the metadata line), or markdown formatting like ```python.

Response Structure:
# METADATA: {{"suggested_function_name": "...", "suggested_tool_name": "...", "suggested_description": "..."}}
def generated_function_name(param1: type, ...) -> return_type:
    \"\"\"Docstring for the function.\"\"\"
    # Function implementation
    ...

Now, generate the metadata and Python function based on the Tool Description provided above.
"""

LLM_CODE_FIX_PROMPT_TEMPLATE = """
The following Python function (from module '{module_path}', function name '{function_name}') has an issue.
Original Problem Description / Goal for Fix:
{problem_description}

Original Function Code:
```python
{original_code}
```

Your task is to provide a corrected version of this Python function.
- Only output the complete, raw Python code for the corrected function.
- Do NOT include any explanations, markdown formatting (like ```python), or any text other than the function code itself.
- Ensure the function signature (name, parameters, type hints) remains the same unless the problem description explicitly requires changing it.
- If you cannot determine a fix or the original code is not a single function, return only the text: "// NO_CODE_SUGGESTION_POSSIBLE"

Corrected Python function code:
"""

# Prompt for generating unit test scaffolds
LLM_UNIT_TEST_SCAFFOLD_PROMPT_TEMPLATE = """You are an expert Python testing assistant.
Given the following Python code, generate a basic unit test scaffold using the 'unittest' framework.

The scaffold should include:
1.  Necessary imports (e.g., `unittest`, and the module containing the code to be tested if it's implied to be in a separate file). Assume the code to be tested is available in a module that can be imported as '{module_name_hint}'.
2.  A test class that inherits from `unittest.TestCase`.
3.  A `setUp` method if it seems beneficial (e.g., if the input code is a class that needs instantiation).
4.  Placeholder test methods (e.g., `test_function_name_basic_case`, `test_function_name_edge_case`) for each public function or method in the provided code.
    - Each placeholder test method should include `self.fail("Test not yet implemented")` or a simple `pass`.
5.  An `if __name__ == '__main__': unittest.main()` block.

Do NOT generate actual test logic or assertions within the placeholder methods. Only generate the structural scaffold.

Python code to generate a unit test scaffold for:
```python
{code_to_test}
```

Unit test scaffold:
"""

# Prompt for generating a structured outline for hierarchical code generation
LLM_HIERARCHICAL_OUTLINE_PROMPT_TEMPLATE = """
You are a senior software architect. Based on the following high-level requirement, generate a structural outline of the Python code needed.
The outline must be a single JSON object.
The JSON object should describe the main module, any classes, and functions/methods.
For each component (module, class, function, method), include:
- "type": e.g., "module", "class", "function", "method"
- "name": The Pythonic name.
- "description": A brief explanation of its purpose.
- (For functions/methods) "signature": e.g., "(self, arg1: str, arg2: int) -> bool"
- (For functions/methods) "body_placeholder": A specific, actionable comment or concise instruction for the AI that will implement this component's body. For example: "# Implement CSV parsing and extract specified column data." or "# Calculate factorial using recursion, handle n=0."
- (For classes) "attributes": A list of attribute definitions (name, type, description).
- (For modules) "imports": A list of necessary Python modules to import.

High-Level Requirement:
{high_level_description}

JSON Outline:
"""

# Prompt for generating component details based on an outline
LLM_COMPONENT_DETAIL_PROMPT_TEMPLATE = """You are an expert Python programmer. Your task is to implement the body of a specific Python function or method based on its definition and the overall context of its containing module or class.

Overall Module/Class Context:
<context_summary>
{overall_context_summary}
</context_summary>

Component to Implement:
- Type: {component_type}
- Name: {component_name}
- Signature: `{component_signature}`
- Description/Purpose: {component_description}
- Body Placeholder (Initial thought from outline): {component_body_placeholder}

Required Module-Level Imports (available for use, do not redeclare unless shadowing):
{module_imports}

Instructions for Implementation:
1.  Implement *only* the Python code for the body of the function/method `{component_name}`.
2.  Adhere strictly to the provided signature: `{component_signature}`.
3.  Ensure your code fulfills the component's described purpose: "{component_description}" and expands on the placeholder: "{component_body_placeholder}".
4.  Use the provided module-level imports if needed. Do not add new module-level imports unless absolutely necessary and clearly justified by a specific library for the task. Local imports within the function are acceptable if scoped appropriately.
5.  If the component is a class method, you can assume it has access to `self` and any attributes defined in the `Overall Module/Class Context` (if provided for a class).
6.  Focus on clear, correct, and efficient Python code. Include comments for complex logic.
7.  For simplicity and consistency, always generate the full component code including signature, i.e., `def function_name(...):\n    body...`. The assembly step can handle placing it correctly.
8.  If the task is impossible or the description is too ambiguous to implement, return only the comment: `# IMPLEMENTATION_ERROR: Ambiguous instruction or impossible task.`

Python code for `{component_name}`:
"""

LLM_GRANULAR_REFACTOR_PROMPT_TEMPLATE = """You are an expert Python refactoring assistant.
You will be given the full source code of a Python function/method, a specific section within that code to target, and a refactoring instruction.
Your task is to apply the refactoring instruction *only* to the specified section and then return the *entire modified function/method code*.

Module Path: `{module_path}`
Function Name: `{function_name}`

Original Function Code:
```python
{original_code}
```

Section to Modify:
```
{section_to_modify}
```

Refactoring Instruction:
{refactor_instruction}

Constraints:
- Modify *only* the specified section if possible. If the change necessitates minor adjustments elsewhere in the function (e.g., a new variable), that's acceptable.
- Return the complete Python code for the *entire function/method*, including the function signature, docstring, and the applied modification.
- Do NOT include any explanations, markdown formatting (like ```python), or any text other than the complete, modified function/method code itself.
- If the instruction is unclear, impossible, or the section cannot be reasonably identified/modified as instructed, return only the text: "// REFACTORING_SUGGESTION_IMPOSSIBLE"

Modified full function/method code:
"""

class CodeService:
    def __init__(self, llm_provider: Optional[Any] = None,
                 self_modification_service: Optional[Any] = None,
                 task_manager: Optional[TaskManager] = None, # Added TaskManager
                 notification_manager: Optional[Any] = None): # Added NotificationManager
        self.llm_provider = llm_provider
        self.self_modification_service = self_modification_service
        self.task_manager = task_manager
        self.notification_manager = notification_manager # Store NotificationManager
        logger.info("CodeService initialized.")
        if is_debug_mode(): # pragma: no cover
            print(f"[DEBUG] CodeService initialized with llm_provider: {llm_provider}, self_modification_service: {self_modification_service}, task_manager: {task_manager}, notification_manager: {notification_manager}")
        if not self.llm_provider: # pragma: no cover
            logger.warning("CodeService initialized without an LLM provider. Code generation capabilities will be limited.")
        if not self.self_modification_service: # pragma: no cover
            logger.warning("CodeService initialized without a self-modification service. File operations will be limited.")
        if not self.task_manager: # pragma: no cover
            logger.info("CodeService initialized without a TaskManager. Task status updates will be skipped.")
        if not self.notification_manager: # pragma: no cover
            logger.info("CodeService initialized without a NotificationManager. Notifications will be skipped.")


    def _update_task(self, task_id: Optional[str], status: ActiveTaskStatus, reason: Optional[str] = None, step_desc: Optional[str] = None):
        if task_id and self.task_manager:
            self.task_manager.update_task_status(task_id, status, reason=reason, step_desc=step_desc)

    async def generate_code(
        self,
        context: str,
        prompt_or_description: str,
        language: str = "python",
        target_path: Optional[str] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        additional_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        task_id: Optional[str] = None
        result: Dict[str, Any] = {}
        task_type_for_manager: ActiveTaskType = ActiveTaskType.MISC_CODE_GENERATION
        related_id_for_task: Optional[str] = prompt_or_description[:70]

        if context == "NEW_TOOL":
            task_type_for_manager = ActiveTaskType.AGENT_TOOL_CREATION
        elif context == "HIERARCHICAL_GEN_COMPLETE_TOOL":
            task_type_for_manager = ActiveTaskType.AGENT_TOOL_CREATION
        elif context == "GENERATE_UNIT_TEST_SCAFFOLD":
            task_type_for_manager = ActiveTaskType.MISC_CODE_GENERATION
            related_id_for_task = additional_context.get("module_name_hint", "scaffold_target") if additional_context else "scaffold_target"
        elif context == "EXPERIMENTAL_HIERARCHICAL_OUTLINE":
            task_type_for_manager = ActiveTaskType.PLANNING_CODE_STRUCTURE
        elif context == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
             task_type_for_manager = ActiveTaskType.MISC_CODE_GENERATION

        if self.task_manager:
            task_desc = f"Generate_code: {context}, Target: {prompt_or_description[:50]}..."
            task = self.task_manager.add_task(
                description=task_desc, # Corrected order
                task_type=task_type_for_manager, # Corrected order
                related_item_id=related_id_for_task
            )
            task_id = task.task_id

        try:
            logger.info(f"CodeService.generate_code called with context='{context}', description='{prompt_or_description[:50]}...' (Task ID: {task_id})")

            if not self.llm_provider:
                result = {"status": "ERROR_LLM_PROVIDER_MISSING", "code_string": None, "metadata": None, "logs": ["LLM provider not configured."], "error": "LLM provider missing."}
                self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                return result

            if language != "python": # pragma: no cover
                result = {
                    "status": "ERROR_UNSUPPORTED_LANGUAGE", "code_string": None, "metadata": None,
                    "logs": [f"Language '{language}' not supported for {context} yet."],
                    "error": "Unsupported language."
                }
                self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                return result

            model_to_use = get_model_for_task("code_generation")
            temperature = 0.3
            max_tokens_to_use = 2048

            if llm_config: # pragma: no cover
                model_to_use = llm_config.get("model_name", model_to_use)
                temperature = llm_config.get("temperature", temperature)
                max_tokens_to_use = llm_config.get("max_tokens", max_tokens_to_use)

            if context == "NEW_TOOL":
                formatted_prompt = LLM_NEW_TOOL_PROMPT_TEMPLATE.format(description=prompt_or_description)
                logs = [f"Using NEW_TOOL context. Prompt description: {prompt_or_description[:50]}... (Task ID: {task_id})"]
                self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Calling LLM for new tool generation")

                raw_llm_output = await self.llm_provider.invoke_ollama_model_async(
                    formatted_prompt, model_name=model_to_use, temperature=temperature, max_tokens=max_tokens_to_use
                )

                if not raw_llm_output or not raw_llm_output.strip():
                    logger.warning(f"LLM returned empty response for NEW_TOOL. Task ID: {task_id}")
                    logs.append("LLM returned empty response.")
                    result = {"status": "ERROR_LLM_NO_CODE", "code_string": None, "metadata": None, "logs": logs, "error": "LLM provided no code."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error"), step_desc=result.get("status"))
                    return result
                logs.append(f"Raw LLM output length: {len(raw_llm_output)}")

                parsed_metadata: Optional[Dict[str, str]] = None
                actual_code_str: str = ""
                if raw_llm_output.startswith("# METADATA:"):
                    try:
                        lines = raw_llm_output.split('\n', 1)
                        metadata_line = lines[0]
                        metadata_json_str_match = re.search(r"{\s*.*?\s*}", metadata_line)
                        if metadata_json_str_match:
                            metadata_json_str = metadata_json_str_match.group(0)
                            parsed_metadata = json.loads(metadata_json_str)
                            logs.append(f"Successfully parsed metadata: {parsed_metadata}")
                            actual_code_str = lines[1] if len(lines) > 1 else ""
                        else: # pragma: no cover
                            logs.append("Could not find JSON object in metadata line.")
                            actual_code_str = raw_llm_output
                    except Exception as e: # pragma: no cover
                        logger.warning(f"Failed to parse metadata JSON for NEW_TOOL: {e}")
                        logs.append(f"Error parsing metadata: {e}. Treating rest as code.")
                        actual_code_str = raw_llm_output.lstrip("# METADATA:") if raw_llm_output.startswith("# METADATA:") else raw_llm_output
                else:
                    logs.append("LLM output for NEW_TOOL did not start with '# METADATA:'.")
                    actual_code_str = raw_llm_output

                cleaned_code = re.sub(r"^\s*```python\s*\n?", "", actual_code_str, flags=re.IGNORECASE | re.MULTILINE)
                cleaned_code = re.sub(r"\n?\s*```\s*$", "", cleaned_code, flags=re.IGNORECASE | re.MULTILINE).strip()
                cleaned_code = cleaned_code.replace("\\n", "\n")


                if not cleaned_code: # pragma: no cover
                    logs.append("Extracted code is empty after cleaning for NEW_TOOL.")
                    result = {"status": "ERROR_CODE_EMPTY_POST_METADATA" if parsed_metadata else "ERROR_LLM_NO_CODE", "code_string": None, "metadata": parsed_metadata, "logs": logs, "error": "No actual code block found or code was empty."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error"), step_desc=result.get("status"))
                    return result
                if not parsed_metadata: # pragma: no cover
                    logs.append("Metadata not successfully parsed for NEW_TOOL, which is required.")
                    result = {"status": "ERROR_METADATA_PARSING", "code_string": cleaned_code, "metadata": None, "logs": logs, "error": "Metadata parsing failed for NEW_TOOL."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error"), step_desc=result.get("status"))
                    return result

                logs.append(f"Cleaned code length for NEW_TOOL: {len(cleaned_code)}")
                logger.info(f"Successfully generated new tool code and metadata for '{parsed_metadata.get('suggested_tool_name', 'UnknownTool')}'. Task ID: {task_id}")

                saved_to_path_val: Optional[str] = None
                final_status_new_tool = "SUCCESS_CODE_GENERATED"
                error_new_tool = None

                if cleaned_code:
                    lint_messages, lint_run_error = await self._run_linter(cleaned_code)
                    if lint_run_error:
                        logs.append(f"Linter execution error: {lint_run_error}")
                    if lint_messages:
                        logs.append("Linting issues found:")
                        logs.extend(lint_messages)

                if target_path and cleaned_code:
                    self._update_task(task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc=f"Saving to {target_path}")
                    logs.append(f"Attempting to save generated NEW_TOOL code to {target_path}")
                    if write_to_file(target_path, cleaned_code):
                        saved_to_path_val = target_path
                        logs.append(f"Successfully saved NEW_TOOL code to {target_path}")
                    else: # pragma: no cover
                        final_status_new_tool = "ERROR_SAVING_CODE"
                        error_new_tool = f"Successfully generated code for NEW_TOOL, but failed to save to {target_path}."
                        logs.append(error_new_tool)
                        logger.error(error_new_tool)

                result = {
                    "status": final_status_new_tool,
                    "code_string": cleaned_code,
                    "metadata": parsed_metadata,
                    "saved_to_path": saved_to_path_val,
                    "logs": logs,
                    "error": error_new_tool
                }
                if final_status_new_tool == "SUCCESS_CODE_GENERATED":
                    self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="New tool generated and saved (if path provided).")
                else:
                    self._update_task(task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason=error_new_tool, step_desc=final_status_new_tool)
                return result

            elif context == "GENERATE_UNIT_TEST_SCAFFOLD":
                self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Calling LLM for unit test scaffold")
                code_to_test = prompt_or_description
                module_name_hint = "your_module_to_test"
                if additional_context and additional_context.get("module_name_hint"): # pragma: no branch
                    module_name_hint = additional_context["module_name_hint"]

                formatted_prompt = LLM_UNIT_TEST_SCAFFOLD_PROMPT_TEMPLATE.format(
                    code_to_test=code_to_test,
                    module_name_hint=module_name_hint
                )
                logs = [f"Using GENERATE_UNIT_TEST_SCAFFOLD context. Module hint: {module_name_hint} Code snippet length: {len(code_to_test)}"]

                raw_llm_output = await self.llm_provider.invoke_ollama_model_async(
                    formatted_prompt, model_name=model_to_use, temperature=temperature, max_tokens=max_tokens_to_use
                )

                if not raw_llm_output or not raw_llm_output.strip():
                    logger.warning("LLM returned empty response for unit test scaffold generation.")
                    logs.append("LLM returned empty response.")
                    return {"status": "ERROR_LLM_NO_CODE", "code_string": None, "metadata": None, "logs": logs, "error": "LLM provided no code."}
                logs.append(f"Raw LLM output length for scaffold: {len(raw_llm_output)}")

                cleaned_scaffold = re.sub(r"^\s*```python\s*\n?", "", raw_llm_output, flags=re.IGNORECASE | re.MULTILINE)
                cleaned_scaffold = re.sub(r"\n?\s*```\s*$", "", cleaned_scaffold, flags=re.IGNORECASE | re.MULTILINE).strip()
                cleaned_scaffold = cleaned_scaffold.replace("\\n", "\n")


                if not cleaned_scaffold: # pragma: no cover
                    logs.append("Extracted scaffold is empty after cleaning.")
                    return {"status": "ERROR_LLM_NO_CODE", "code_string": None, "metadata": None, "logs": logs, "error": "LLM output was empty after cleaning for scaffold."}

                logs.append(f"Cleaned scaffold length: {len(cleaned_scaffold)}")
                logger.info("Successfully generated unit test scaffold.")

                saved_to_path_scaffold: Optional[str] = None
                final_status_scaffold = "SUCCESS_CODE_GENERATED"
                error_scaffold = None

                if target_path and cleaned_scaffold:
                    logs.append(f"Attempting to save generated unit test scaffold to {target_path}")
                    if write_to_file(target_path, cleaned_scaffold):
                        saved_to_path_scaffold = target_path
                        logs.append(f"Successfully saved unit test scaffold to {target_path}")
                    else: # pragma: no cover
                        final_status_scaffold = "ERROR_SAVING_CODE"
                        error_scaffold = f"Successfully generated unit test scaffold, but failed to save to {target_path}."
                        logs.append(error_scaffold)
                        logger.error(error_scaffold)

                return {
                    "status": final_status_scaffold,
                    "code_string": cleaned_scaffold,
                    "metadata": None,
                    "saved_to_path": saved_to_path_scaffold,
                    "logs": logs,
                    "error": error_scaffold
                }

            elif context == "EXPERIMENTAL_HIERARCHICAL_OUTLINE":
                high_level_description = prompt_or_description
                logs = [f"Context: EXPERIMENTAL_HIERARCHICAL_OUTLINE. Desc: {high_level_description[:50]}... (Task ID: {task_id})"]
                self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Calling _generate_hierarchical_outline")

                outline_gen_result = await self._generate_hierarchical_outline(high_level_description, llm_config)
                logs.extend(outline_gen_result.get("logs", []))

                result = {
                    "status": outline_gen_result.get("status", "ERROR_UNDEFINED_OUTLINE_FAILURE"),
                    "outline_str": outline_gen_result.get("outline_str"),
                    "parsed_outline": outline_gen_result.get("parsed_outline"),
                    "code_string": None,
                    "metadata": None,
                    "logs": logs,
                    "error": outline_gen_result.get("error")
                }

                if result["status"] == "SUCCESS_OUTLINE_GENERATED":
                    self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Hierarchical outline generated successfully.")
                else:
                    failure_reason = result.get("error", "Outline generation failed.")
                    step_description = result.get("status", "Outline generation failed")
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=failure_reason, step_desc=step_description)
                return result

            elif context == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
                high_level_description = prompt_or_description
                logs = [f"Context: EXPERIMENTAL_HIERARCHICAL_FULL_TOOL. Desc: {high_level_description[:50]}... (Task ID: {task_id})"]
                self._update_task(task_id, ActiveTaskStatus.PLANNING_CODE_STRUCTURE, step_desc="Generating outline via _generate_hierarchical_outline")

                outline_gen_result = await self._generate_hierarchical_outline(high_level_description, llm_config)
                logs.extend(outline_gen_result.get("logs", []))
                parsed_outline = outline_gen_result.get("parsed_outline")
                current_status = outline_gen_result.get("status")
                current_error = outline_gen_result.get("error")

                if current_status != "SUCCESS_OUTLINE_GENERATED" or not parsed_outline:
                    logs.append("Outline generation failed or outline is empty, cannot proceed to detail generation.")
                    result = {
                        "status": current_status or "ERROR_OUTLINE_GENERATION_FAILED",
                        "parsed_outline": parsed_outline,
                        "component_details": None,
                        "code_string": None,
                        "metadata": None,
                        "logs": logs,
                        "error": current_error or "Outline generation failed or outline was empty."
                    }
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error"), step_desc=result.get("status"))
                    return result

                self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Generating details for components based on outline")
                component_details: Dict[str, Optional[str]] = {}
                all_details_succeeded = True
                any_detail_succeeded = False

                components_to_generate = []
                if parsed_outline.get("components"):
                    for component_def in parsed_outline["components"]:
                        if component_def.get("type") == "function":
                            components_to_generate.append(component_def)
                        elif component_def.get("type") == "class" and component_def.get("methods"):
                            for method_def in component_def["methods"]:
                                method_key = f"{component_def.get('name', 'UnknownClass')}.{method_def.get('name', 'UnknownMethod')}"
                                components_to_generate.append({
                                    **method_def,
                                    "name": method_key,
                                    "original_name": method_def.get("name"),
                                    "class_context": component_def
                                })

                logs.append(f"Found {len(components_to_generate)} components for detail generation.")

                for comp_def_for_detail_gen in components_to_generate:
                    current_comp_key = comp_def_for_detail_gen.get("name")
                    logs.append(f"Generating details for component key: {current_comp_key}")
                    detail_code = await self._generate_detail_for_component(
                        component_definition=comp_def_for_detail_gen,
                        full_outline=parsed_outline,
                        llm_config=llm_config
                    )
                    if detail_code:
                        component_details[current_comp_key] = detail_code
                        logs.append(f"Successfully generated details for {current_comp_key}.")
                        any_detail_succeeded = True
                    else:
                        component_details[current_comp_key] = None
                        logs.append(f"Failed to generate details for {current_comp_key}.")
                        all_details_succeeded = False

                detail_gen_status = "ERROR_DETAIL_GENERATION_FAILED"
                if not current_error: current_error = None

                if all_details_succeeded and any_detail_succeeded:
                    detail_gen_status = "SUCCESS_HIERARCHICAL_DETAILS_GENERATED"
                elif any_detail_succeeded:
                    detail_gen_status = "PARTIAL_HIERARCHICAL_DETAILS_GENERATED"
                    if not current_error: current_error = "Some component details failed generation."
                else:
                    if not components_to_generate:
                        detail_gen_status = "SUCCESS_HIERARCHICAL_DETAILS_GENERATED"
                        logs.append("No components found in outline for detail generation.")
                    elif not current_error:
                        current_error = "All component details failed generation."

                logs.append(f"Detail generation phase status: {detail_gen_status}")

                result = {
                    "status": detail_gen_status,
                    "parsed_outline": parsed_outline,
                    "component_details": component_details,
                    "code_string": None,
                    "metadata": None,
                    "logs": logs,
                    "error": current_error
                }

                if detail_gen_status == "SUCCESS_HIERARCHICAL_DETAILS_GENERATED":
                    self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Outline and all component details generated.")
                elif detail_gen_status == "PARTIAL_HIERARCHICAL_DETAILS_GENERATED":
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error", "Partial success in generating component details."), step_desc=detail_gen_status)
                else:
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error", "Failed to generate component details."), step_desc=detail_gen_status)
                return result

            elif context == "HIERARCHICAL_GEN_COMPLETE_TOOL":
                high_level_description = prompt_or_description
                logs = [f"Context: HIERARCHICAL_GEN_COMPLETE_TOOL. Desc: {high_level_description[:50]}... (Task ID: {task_id})"]
                current_error: Optional[str] = None
                parsed_outline: Optional[Dict[str, Any]] = None
                component_details: Dict[str, Optional[str]] = {}
                assembled_code: Optional[str] = None
                saved_to_path_val: Optional[str] = None

                # 1. Generate Outline
                self._update_task(task_id, ActiveTaskStatus.PLANNING_CODE_STRUCTURE, step_desc="Generating hierarchical outline")
                outline_gen_result = await self._generate_hierarchical_outline(high_level_description, llm_config)
                logs.extend(outline_gen_result.get("logs", []))
                parsed_outline = outline_gen_result.get("parsed_outline")

                if outline_gen_result.get("status") != "SUCCESS_OUTLINE_GENERATED" or not parsed_outline:
                    current_error = outline_gen_result.get("error", "Outline generation failed or outline was empty.")
                    logs.append(f"Outline generation failed: {current_error}")
                    result = {
                        "status": outline_gen_result.get("status", "ERROR_OUTLINE_GENERATION_FAILED"),
                        "parsed_outline": parsed_outline, "component_details": None, "code_string": None,
                        "metadata": None, "logs": logs, "error": current_error, "saved_to_path": None
                    }
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=current_error, step_desc=result["status"])
                    return result

                # 2. Generate Details for Components
                self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Generating details for components")
                all_details_succeeded = True
                any_detail_succeeded = False
                components_to_generate = []
                if parsed_outline.get("components"):
                    for component_def in parsed_outline["components"]:
                        if component_def.get("type") == "function":
                            components_to_generate.append(component_def)
                        elif component_def.get("type") == "class" and component_def.get("methods"):
                            for method_def in component_def["methods"]:
                                method_key = f"{component_def.get('name', 'UnknownClass')}.{method_def.get('name', 'UnknownMethod')}"
                                components_to_generate.append({
                                    **method_def, "name": method_key, "original_name": method_def.get("name"),
                                    "class_context": component_def
                                })
                logs.append(f"Found {len(components_to_generate)} components for detail generation.")

                if components_to_generate:
                    for comp_def_for_detail in components_to_generate:
                        comp_name_key = comp_def_for_detail.get("name")
                        logs.append(f"Generating details for component: {comp_name_key}")
                        detail_code = await self._generate_detail_for_component(comp_def_for_detail, parsed_outline, llm_config)
                        if detail_code:
                            component_details[comp_name_key] = detail_code
                            logs.append(f"Successfully generated details for {comp_name_key}.")
                            any_detail_succeeded = True
                        else:
                            component_details[comp_name_key] = None
                            logs.append(f"Failed to generate details for {comp_name_key}.")
                            all_details_succeeded = False
                else:
                    logs.append("No components listed in outline for detail generation.")

                detail_gen_status_for_final_status = "SUCCESS_HIERARCHICAL_DETAILS_GENERATED"
                if not all_details_succeeded and any_detail_succeeded:
                    detail_gen_status_for_final_status = "PARTIAL_HIERARCHICAL_DETAILS_GENERATED"
                    if not current_error: current_error = "Some component details failed generation."
                elif not any_detail_succeeded and components_to_generate:
                    detail_gen_status_for_final_status = "ERROR_DETAIL_GENERATION_FAILED"
                    if not current_error: current_error = "All component details failed to generate."
                logs.append(f"Detail generation phase status: {detail_gen_status_for_final_status}")


                # 3. Assemble Components
                self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Assembling code components")
                try:
                    assembled_code = self._assemble_components(parsed_outline, component_details)
                    logs.append(f"Assembly attempt complete. Assembled code length: {len(assembled_code or '')}")
                    if not assembled_code and (parsed_outline.get("components") or parsed_outline.get("main_execution_block")):
                        if not current_error: current_error = "Assembly resulted in empty code despite having an outline."
                        logs.append(current_error)
                        # Status will be determined based on detail_gen_status later
                except Exception as e_assemble:
                    logger.error(f"Error during code assembly: {e_assemble}", exc_info=True)
                    logs.append(f"Exception during assembly: {e_assemble}")
                    current_error = f"Assembly failed: {e_assemble}"
                    result = {
                        "status": "ERROR_ASSEMBLY_FAILED", "parsed_outline": parsed_outline,
                        "component_details": component_details, "code_string": None, "metadata": None,
                        "logs": logs, "error": current_error, "saved_to_path": None
                    }
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=current_error, step_desc=result["status"])
                    return result

                # 4. Determine final status based on phases
                final_status = "ERROR_UNKNOWN_HIERARCHICAL_STATE" # Default, should be overwritten
                if detail_gen_status_for_final_status == "SUCCESS_HIERARCHICAL_DETAILS_GENERATED":
                    final_status = "SUCCESS_HIERARCHICAL_ASSEMBLED"
                elif detail_gen_status_for_final_status == "PARTIAL_HIERARCHICAL_DETAILS_GENERATED":
                    final_status = "PARTIAL_HIERARCHICAL_ASSEMBLED"
                elif detail_gen_status_for_final_status == "ERROR_DETAIL_GENERATION_FAILED":
                     final_status = "ERROR_ASSEMBLY_FAILED_DUE_TO_DETAILS" # Or similar to indicate root cause

                if not assembled_code and final_status not in ["ERROR_ASSEMBLY_FAILED_DUE_TO_DETAILS"]:
                    if (parsed_outline.get("components") or parsed_outline.get("main_execution_block")):
                        final_status = "ERROR_ASSEMBLY_EMPTY_CODE"
                        if not current_error: current_error = "Assembly resulted in empty code despite outline."
                    else: # Empty outline led to empty code, this is fine.
                        if final_status == "SUCCESS_HIERARCHICAL_ASSEMBLED": # if details were "successful" (vacuously true)
                             logs.append("Assembly resulted in empty code as outline was effectively empty.")


                # 5. Lint and Save Assembled Code (if generated)
                if assembled_code:
                    self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Running linter on assembled code")
                    lint_messages, lint_run_error = await self._run_linter(assembled_code)
                    if lint_run_error: logs.append(f"Linter execution error for assembled code: {lint_run_error}")
                    if lint_messages:
                        logs.append("Linting issues found in assembled code:")
                        logs.extend(lint_messages)

                    if target_path:
                        self._update_task(task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc=f"Saving assembled code to {target_path}")
                        logs.append(f"Attempting to save assembled code to {target_path}")
                        if write_to_file(target_path, assembled_code):
                            saved_to_path_val = target_path
                            logs.append(f"Successfully saved assembled code to {target_path}")
                        else:
                            final_status = "ERROR_SAVING_ASSEMBLED_CODE"
                            current_error = f"Failed to save assembled code to {target_path}."
                            logs.append(current_error)
                            logger.error(current_error)
                elif target_path:
                     logs.append(f"No assembled code to save (status: {final_status}). Error: {current_error}")

                # 6. Prepare and return result
                result = {
                    "status": final_status, "parsed_outline": parsed_outline, "component_details": component_details,
                    "code_string": assembled_code, "metadata": None, "saved_to_path": saved_to_path_val,
                    "logs": logs, "error": current_error
                }

                if "SUCCESS_HIERARCHICAL_ASSEMBLED" in final_status or "PARTIAL_HIERARCHICAL_ASSEMBLED" in final_status:
                    self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Hierarchical generation and assembly complete.", step_desc=final_status)
                else:
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=current_error, step_desc=final_status)
                return result

            else: # pragma: no cover
                result = {
                    "status": "ERROR_UNSUPPORTED_CONTEXT", "code_string": None, "metadata": None,
                    "logs": [f"generate_code for context '{context}' not supported yet."],
                    "error": "Unsupported context for generate_code."
                }
                self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                return result
        except Exception as e_gen_code: # pragma: no cover
            logger.error(f"Unexpected error in generate_code: {e_gen_code}. Task ID: {task_id}", exc_info=True)
            result = {"status": "ERROR_GENERATE_CODE_UNEXPECTED", "code_string": None, "metadata": None,
                      "logs": [f"Unexpected error in generate_code: {e_gen_code}"], "error": str(e_gen_code)}
            self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=str(e_gen_code), step_desc="Unexpected error in generate_code")
            return result


    async def _generate_hierarchical_outline(
        self,
        high_level_description: str,
        llm_config: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        logs = [f"Initiating hierarchical outline generation for: {high_level_description[:70]}..."]

        if not self.llm_provider: # pragma: no cover
            logger.error("LLM provider not configured for CodeService.")
            return {"status": "ERROR_LLM_PROVIDER_MISSING", "parsed_outline": None, "outline_str": None, "logs": logs, "error": "LLM provider missing."}

        model_to_use = get_model_for_task("code_outline_generation")
        temperature = 0.3
        max_tokens_to_use = 2048

        if llm_config: # pragma: no cover
            model_to_use = llm_config.get("model_name", model_to_use)
            temperature = llm_config.get("temperature", temperature)
            max_tokens_to_use = llm_config.get("max_tokens", max_tokens_to_use)

        logs.append(f"Using LLM model: {model_to_use}, Temp: {temperature}, Max Tokens: {max_tokens_to_use} for outline.")

        formatted_prompt = LLM_HIERARCHICAL_OUTLINE_PROMPT_TEMPLATE.format(
            high_level_description=high_level_description
        )

        raw_llm_output = await self.llm_provider.invoke_ollama_model_async(
            formatted_prompt, model_name=model_to_use, temperature=temperature, max_tokens=max_tokens_to_use
        )

        if not raw_llm_output or not raw_llm_output.strip(): # pragma: no cover
            logger.warning("LLM returned empty response for hierarchical outline generation.")
            logs.append("LLM returned empty response for outline.")
            return {"status": "ERROR_LLM_NO_OUTLINE", "parsed_outline": None, "outline_str": raw_llm_output, "logs": logs, "error": "LLM provided no outline."}

        logs.append(f"Raw LLM outline output length: {len(raw_llm_output)}")

        parsed_outline: Optional[Dict[str, Any]] = None
        error_message: Optional[str] = None
        final_status = "SUCCESS_OUTLINE_GENERATED"

        try:
            cleaned_json_str = raw_llm_output.strip()
            if cleaned_json_str.startswith("```json"): # pragma: no cover
                cleaned_json_str = cleaned_json_str[len("```json"):].strip()
            if cleaned_json_str.endswith("```"): # pragma: no cover
                cleaned_json_str = cleaned_json_str[:-len("```")].strip()

            cleaned_json_str = cleaned_json_str.replace('\\n', '\n').replace('\\"', '\"')

            parsed_outline = json.loads(cleaned_json_str)
            logs.append("Successfully parsed JSON outline from LLM response.")
        except json.JSONDecodeError as e: # pragma: no cover
            logger.warning(f"Failed to parse JSON outline: {e}. Raw output snippet: {raw_llm_output[:200]}...")
            logs.append(f"JSONDecodeError parsing outline: {e}. Raw output snippet: {raw_llm_output[:100]}")
            error_message = f"Failed to parse LLM JSON outline: {e}"
            final_status = "ERROR_OUTLINE_PARSING"

        return {"status": final_status, "parsed_outline": parsed_outline, "outline_str": raw_llm_output, "logs": logs, "error": error_message}


    async def modify_code(
        self, context: str, modification_instruction: str,
        existing_code: Optional[str] = None, language: str = "python",
        module_path: Optional[str] = None, function_name: Optional[str] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        additional_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        task_id: Optional[str] = None
        result: Dict[str, Any] = {}
        logs: List[str] = [] # Initialize logs here

        if self.task_manager:
            task_desc = f"Modify_code: {context}, Target: {module_path}.{function_name}"
            related_id = f"{module_path}.{function_name}" if module_path and function_name else "unknown_target"
            task = self.task_manager.add_task(
                description=task_desc, # Corrected order
                task_type=ActiveTaskType.AGENT_TOOL_MODIFICATION,
                related_item_id=related_id
            )
            task_id = task.task_id

        try:
            logger.info(f"CodeService.modify_code called for context='{context}', module='{module_path}', function='{function_name}'. Task ID: {task_id}")
            logs.append(f"modify_code invoked with context='{context}', module='{module_path}', function='{function_name}'. Task ID: {task_id}")

            if language != "python":
                result = {"status": "ERROR_UNSUPPORTED_LANGUAGE", "modified_code_string": None, "logs": logs, "error": "Unsupported language."}
                self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                return result

            actual_existing_code = existing_code
            if actual_existing_code is None and (context == "SELF_FIX_TOOL" or context == "GRANULAR_CODE_REFACTOR"):
                if not module_path or not function_name:
                    logs.append(f"Missing module_path or function_name for {context} when existing_code is not provided.")
                    result = {"status": "ERROR_MISSING_DETAILS", "modified_code_string": None, "logs": logs, "error": "Missing module_path or function_name."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result

                self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Fetching original code for modification")
                logger.info(f"No existing_code provided for {module_path}.{function_name}, attempting to fetch. Task ID: {task_id}")
                logs.append(f"Fetching original code for {module_path}.{function_name}.")
                if not self.self_modification_service:
                    logger.error(f"Self modification service not configured for modify_code. Task ID: {task_id}")
                    logs.append("Self modification service not configured.")
                    result = {"status": "ERROR_SELF_MOD_SERVICE_MISSING", "modified_code_string": None, "logs": logs, "error": "Self modification service not configured."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result
                actual_existing_code = self.self_modification_service.get_function_source_code(module_path, function_name)

                if actual_existing_code is None:
                    logger.warning(f"Could not retrieve original code for {module_path}.{function_name}. Task ID: {task_id}")
                    logs.append(f"Failed to retrieve original source for {module_path}.{function_name}.")
                    result = {"status": "ERROR_NO_ORIGINAL_CODE", "modified_code_string": None, "logs": logs, "error": "Cannot get original code."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result

            current_llm_config = llm_config if llm_config else {}
            code_gen_model = current_llm_config.get("model_name", get_model_for_task("code_modification"))
            temperature = current_llm_config.get("temperature", 0.2)
            max_tokens = current_llm_config.get("max_tokens", 2048)

            prompt = ""
            if context == "SELF_FIX_TOOL":
                if not module_path or not function_name: # Should be caught earlier if actual_existing_code was None
                    logs.append("Missing module_path or function_name for SELF_FIX_TOOL (post-fetch check).") # Defensive
                    result = {"status": "ERROR_MISSING_DETAILS", "modified_code_string": None, "logs": logs, "error": "Missing details for self-fix."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result
                if actual_existing_code is None: # Should be caught earlier
                     logs.append("Original code is missing for SELF_FIX_TOOL (post-fetch check).") # Defensive
                     result = {"status": "ERROR_NO_ORIGINAL_CODE", "modified_code_string": None, "logs": logs, "error": "Original code missing."}
                     self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                     return result

                prompt = LLM_CODE_FIX_PROMPT_TEMPLATE.format(
                    module_path=module_path, function_name=function_name,
                    problem_description=modification_instruction, original_code=actual_existing_code
                )
                logs.append(f"Using SELF_FIX_TOOL. Target: {module_path}.{function_name}")

            elif context == "GRANULAR_CODE_REFACTOR":
                if not module_path or not function_name: # Defensive
                    logs.append("Missing module_path or function_name for GRANULAR_CODE_REFACTOR (post-fetch check).")
                    result = {"status": "ERROR_MISSING_DETAILS", "modified_code_string": None, "logs": logs, "error": "Missing module_path or function_name for prompt."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result
                if actual_existing_code is None: # Defensive
                     logs.append("Original code is missing for GRANULAR_CODE_REFACTOR (post-fetch check).")
                     result = {"status": "ERROR_NO_ORIGINAL_CODE", "modified_code_string": None, "logs": logs, "error": "Original code missing."}
                     self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                     return result

                if not additional_context or "section_identifier" not in additional_context:
                    logs.append("Missing 'section_identifier' in additional_context for GRANULAR_CODE_REFACTOR.")
                    result = {"status": "ERROR_MISSING_SECTION_IDENTIFIER", "modified_code_string": None, "logs": logs, "error": "Section identifier not provided."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result

                section_to_modify = additional_context["section_identifier"]
                prompt = LLM_GRANULAR_REFACTOR_PROMPT_TEMPLATE.format(
                    module_path=module_path, function_name=function_name,
                    original_code=actual_existing_code,
                    section_to_modify=section_to_modify,
                    refactor_instruction=modification_instruction
                )
                logs.append(f"Using GRANULAR_CODE_REFACTOR. Target: {module_path}.{function_name}, Section: '{section_to_modify[:50]}...'")

            elif context == "SELF_FIX_AST":
                logs.append(f"Using SELF_FIX_AST. Target: {module_path}.{function_name}")
                if not module_path or not function_name:
                    logs.append(f"Missing module_path or function_name for {context}.")
                    result = {"status": "ERROR_MISSING_DETAILS", "modified_code_string": None, "logs": logs, "error": "Missing module_path or function_name for AST fix."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result

                new_code_string = additional_context.get("new_code_string") if additional_context else None
                if not new_code_string:
                    logs.append(f"Missing 'new_code_string' in additional_context for {context}.")
                    result = {"status": "ERROR_MISSING_NEW_CODE_STRING", "modified_code_string": None, "logs": logs, "error": "'new_code_string' not provided for AST fix."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result

                if not self.self_modification_service:
                    logger.error(f"Self modification service not configured for {context}. Task ID: {task_id}")
                    logs.append("Self modification service not configured.")
                    result = {"status": "ERROR_SELF_MOD_SERVICE_MISSING", "modified_code_string": None, "logs": logs, "error": "Self modification service not configured."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result

                self._update_task(task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc="Applying AST-based code modification")
                try:
                    # Assuming edit_function_source_code returns True on success, False/raises on failure
                    # The actual self_modification.edit_function_source_code might need adjustment or a wrapper
                    # if its return/error handling isn't directly True/False.
                    # For now, let's assume it either works or raises an exception handled by the main try-except.
                    # If it returns False, we need to handle that.
                    # Let's assume self_modification.edit_function_source_code raises an error on failure
                    # or returns a more detailed status we're not yet using.
                    # For simplicity, we'll rely on it raising an exception for now if it fails.

                    # The design doc implies `self_modification.edit_function_source_code` is the target.
                    # Let's assume it exists and works as expected or raises an error.
                    # A more robust implementation might check a boolean return if that's what it does.
                    self.self_modification_service.edit_function_source_code(module_path, function_name, new_code_string)
                    logs.append(f"Successfully applied AST-based fix to {module_path}.{function_name}.")
                    logger.info(f"Successfully applied AST-based fix for {context} on {function_name}. Task ID: {task_id}")
                    result = {
                        "status": "SUCCESS_CODE_APPLIED_AST",
                        "modified_code_string": new_code_string, # The code that was applied
                        "logs": logs,
                        "error": None
                    }
                    self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="AST-based code modification applied.", step_desc=result.get("status"))
                    return result
                except Exception as e_ast_apply:
                    logger.error(f"Error applying AST-based fix for {context} on {function_name}: {e_ast_apply}. Task ID: {task_id}", exc_info=True)
                    logs.append(f"Failed to apply AST-based fix: {e_ast_apply}")
                    result = {"status": "ERROR_APPLYING_AST_FIX", "modified_code_string": None, "logs": logs, "error": str(e_ast_apply)}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_DURING_APPLY, reason=str(e_ast_apply), step_desc=result.get("status"))
                    return result

            else: # This is for contexts that are not SELF_FIX_TOOL, GRANULAR_CODE_REFACTOR, or SELF_FIX_AST
                logs.append(f"Context '{context}' not supported for modify_code.")
                result = {"status": "ERROR_UNSUPPORTED_CONTEXT", "modified_code_string": None, "logs": logs, "error": "Unsupported context"}
                self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                return result

            # LLM related block for SELF_FIX_TOOL and GRANULAR_CODE_REFACTOR
            if context in ["SELF_FIX_TOOL", "GRANULAR_CODE_REFACTOR"]:
                if not self.llm_provider: # Should be caught earlier if context required it and it was None
                    logger.error(f"LLM provider not configured for modify_code context {context}. Task ID: {task_id}")
                    logs.append("LLM provider not configured.")
                    result = {"status": "ERROR_LLM_PROVIDER_MISSING", "modified_code_string": None, "logs": logs, "error": "LLM provider not configured."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result

                self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Calling LLM for code modification")
                logger.info(f"Sending code modification prompt to LLM (model: {code_gen_model}, temp: {temperature}). Task ID: {task_id}")
                logs.append(f"Sending prompt to LLM ({code_gen_model}). Instruction: {modification_instruction[:50]}...")

                llm_response = await self.llm_provider.invoke_ollama_model_async(
                    prompt, model_name=code_gen_model, temperature=temperature, max_tokens=max_tokens
                )

                no_suggestion_marker = "// NO_CODE_SUGGESTION_POSSIBLE"
                if context == "GRANULAR_CODE_REFACTOR":
                    no_suggestion_marker = "// REFACTORING_SUGGESTION_IMPOSSIBLE"

                if not llm_response or no_suggestion_marker in llm_response or len(llm_response.strip()) < 5:
                    logger.warning(f"LLM did not provide a usable code suggestion for {context}. Response: {llm_response}. Task ID: {task_id}")
                    logs.append(f"LLM failed to provide suggestion or indicated impossibility. Output: {llm_response[:100] if llm_response else 'None'}")
                    result = {"status": "ERROR_LLM_NO_SUGGESTION", "modified_code_string": None, "logs": logs, "error": f"LLM provided no usable suggestion or indicated impossibility ({no_suggestion_marker})."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error"), step_desc=result.get("status"))
                    return result

                cleaned_llm_code = llm_response.strip()
                if cleaned_llm_code.startswith("```python"):
                    cleaned_llm_code = cleaned_llm_code[len("```python"):].strip()
                if cleaned_llm_code.endswith("```"):
                    cleaned_llm_code = cleaned_llm_code[:-len("```")].strip()
                cleaned_llm_code = cleaned_llm_code.replace("\\n", "\n")

                logs.append(f"LLM successfully generated code suggestion for {context}. Length: {len(cleaned_llm_code)}")
                logger.info(f"LLM generated code suggestion for {context} on {function_name}. Length: {len(cleaned_llm_code)}. Task ID: {task_id}")

                result = {
                    "status": "SUCCESS_CODE_GENERATED", # Note: for these LLM-based contexts, code is generated, not yet applied by CodeService
                    "modified_code_string": cleaned_llm_code,
                    "logs": logs,
                    "error": None
                }
                self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Code modification generated by LLM.", step_desc=result.get("status"))
                return result

            # If context was SELF_FIX_AST, it should have returned earlier.
            # This part of the code should ideally not be reached if context was SELF_FIX_AST.
            # Adding a fallback or assertion here might be good for defensive programming.
            logger.error(f"Reached unexpected part of modify_code for context {context}. This should not happen. Task ID: {task_id}") # Should be unreachable for SELF_FIX_AST
            result = {"status": "ERROR_UNEXPECTED_FLOW_MODIFY_CODE", "modified_code_string": None, "logs": logs, "error": "Unexpected flow in modify_code."}
            self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error"), step_desc=result.get("status"))
            return result


        except Exception as e:
            logger.error(f"Unexpected error in modify_code: {e}. Task ID: {task_id}", exc_info=True)
            result = {"status": "ERROR_MODIFY_CODE_UNEXPECTED", "modified_code_string": None,
                      "logs": logs if 'logs' in locals() else [f"Unexpected error in modify_code (early): {e}"],
                      "error": str(e)}
            self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=str(e), step_desc="Unexpected error in modify_code")
            return result


    async def _run_linter(self, code_string: str) -> Tuple[List[str], Optional[str]]:
        lint_messages: List[str] = []
        error_string: Optional[str] = None

        if not code_string.strip():
            return [], None

        try:
            process = await asyncio.create_subprocess_exec(
                'ruff', 'check', '--output-format=json', '--stdin-filename', '<stdin>', '-',
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate(input=code_string.encode('utf-8'))

            stdout_str = stdout.decode('utf-8', errors='replace')
            stderr_str = stderr.decode('utf-8', errors='replace')

            if process.returncode == 0 and stdout_str.strip():
                try:
                    ruff_issues = json.loads(stdout_str)
                    for issue in ruff_issues:
                        msg = (
                            f"LINT (Ruff): {issue.get('code')} at "
                            f"{issue.get('location',{}).get('row',0)}:{issue.get('location',{}).get('column',0)}: "
                            f"{issue.get('message','')} ({issue.get('filename', '<stdin>')})"
                        )
                        lint_messages.append(msg)
                    return lint_messages, None
                except json.JSONDecodeError as je:
                    error_string = f"Ruff JSON output parsing error: {je}. Stdout: {stdout_str[:200]}"
                    logger.warning(error_string)


            elif process.returncode != 0 and stdout_str.strip() :
                 try:
                    ruff_issues = json.loads(stdout_str)
                    for issue in ruff_issues:
                        msg = (
                            f"LINT (Ruff): {issue.get('code')} at "
                            f"{issue.get('location',{}).get('row',0)}:{issue.get('location',{}).get('column',0)}: "
                            f"{issue.get('message','')} ({issue.get('filename', '<stdin>')})"
                        )
                        lint_messages.append(msg)
                    return lint_messages, None
                 except json.JSONDecodeError as je:
                    error_string = f"Ruff JSON output parsing error (with issues): {je}. Stdout: {stdout_str[:200]}"
                    logger.warning(error_string)

            if stderr_str and not stdout_str:
                error_string = f"Ruff execution error (JSON mode): {stderr_str}"
                logger.warning(error_string)

        except FileNotFoundError:
            logger.info("Ruff not found (JSON attempt). Trying Ruff text.")
            error_string = "Ruff not found."
        except Exception as e_ruff_json: # pragma: no cover
            error_string = f"Unexpected error running Ruff (JSON): {e_ruff_json}"
            logger.error(error_string, exc_info=True)

        try:
            process = await asyncio.create_subprocess_exec(
                'ruff', 'check', '--output-format=text', '--stdin-filename', '<stdin>', '-',
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate(input=code_string.encode('utf-8'))
            stdout_str = stdout.decode('utf-8', errors='replace')
            stderr_str = stderr.decode('utf-8', errors='replace')

            if stdout_str.strip():
                for line in stdout_str.strip().splitlines():
                    lint_messages.append(f"LINT (Ruff Text): {line}")
                return lint_messages, None
            elif process.returncode == 0:
                return [], None

            if stderr_str:
                if not error_string or error_string == "Ruff not found.":
                     error_string = f"Ruff execution error (Text mode): {stderr_str}"
                logger.warning(f"Ruff execution error (Text mode): {stderr_str}")


        except FileNotFoundError:
            logger.info("Ruff not found (Text attempt). Trying Pyflakes.")
            if not error_string : error_string = "Ruff not found."
        except Exception as e_ruff_text: # pragma: no cover
            new_error = f"Unexpected error running Ruff (Text): {e_ruff_text}"
            if not error_string or error_string == "Ruff not found.": error_string = new_error
            logger.error(new_error, exc_info=True)

        try:
            process = await asyncio.create_subprocess_exec(
                'pyflakes', '-',
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate(input=code_string.encode('utf-8'))
            stdout_str = stdout.decode('utf-8', errors='replace')
            stderr_str = stderr.decode('utf-8', errors='replace')

            if stdout_str.strip():
                for line in stdout_str.strip().splitlines():
                    lint_messages.append(f"LINT (Pyflakes): {line}")
                return lint_messages, None
            elif process.returncode == 0:
                return [], None

            if stderr_str:
                error_string = f"Pyflakes execution error: {stderr_str}"
                logger.warning(error_string)

        except FileNotFoundError:
            logger.info("Pyflakes not found.")
            if error_string == "Ruff not found.":
                error_string = "Linter check failed: Ruff and Pyflakes not found."
            elif not error_string :
                error_string = "Pyflakes not found; Ruff also failed."
        except Exception as e_pyflakes: # pragma: no cover
            new_error = f"Unexpected error running Pyflakes: {e_pyflakes}"
            if not error_string or "not found" in error_string: error_string = new_error
            logger.error(new_error, exc_info=True)

        return lint_messages, error_string

    async def _generate_detail_for_component(
        self,
        component_definition: Dict[str, Any],
        full_outline: Dict[str, Any],
        llm_config: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        component_type = component_definition.get('type', 'unknown_type')
        component_name = component_definition.get('name', 'UnnamedComponent')
        component_signature = component_definition.get('signature', '')
        component_description = component_definition.get('description', '')
        component_body_placeholder = component_definition.get('body_placeholder', '')

        module_imports_list = full_outline.get('imports', [])
        module_imports_str = "\n".join([f"import {imp}" for imp in module_imports_list]) if module_imports_list else "# No specific module-level imports listed in outline."

        overall_context_summary = full_outline.get('description', 'No overall description provided in outline.')
        if component_type == "method" and full_outline.get("components"):
            for comp in full_outline["components"]:
                if comp.get("type") == "class" and any(meth.get("name") == component_name for meth in comp.get("methods",[])): # pragma: no branch
                    class_attrs = ", ".join([f"{attr.get('name')}: {attr.get('type')}" for attr in comp.get('attributes',[])])
                    overall_context_summary = (
                        f"Within class '{comp.get('name', 'UnknownClass')}' with attributes ({class_attrs}). "
                        f"Overall class description: {comp.get('description', '')}"
                    )
                    break

        logger.info(f"CodeService: Generating detail for component '{component_name}' (type: {component_type}).")

        if not self.llm_provider: # pragma: no cover
            logger.error("LLM provider not configured for CodeService, cannot generate component detail.")
            return None

        prompt = LLM_COMPONENT_DETAIL_PROMPT_TEMPLATE.format(
            overall_context_summary=overall_context_summary,
            component_type=component_type,
            component_name=component_name,
            component_signature=component_signature,
            component_description=component_description,
            component_body_placeholder=component_body_placeholder,
            module_imports=module_imports_str
        )

        model_to_use = get_model_for_task("code_generation")
        temperature = 0.2
        max_tokens_to_use = 1024

        if llm_config: # pragma: no cover
            model_to_use = llm_config.get("model_name", model_to_use)
            temperature = llm_config.get("temperature", temperature)
            max_tokens_to_use = llm_config.get("max_tokens", max_tokens_to_use)

        raw_llm_output = await self.llm_provider.invoke_ollama_model_async(
            prompt, model_name=model_to_use, temperature=temperature, max_tokens=max_tokens_to_use
        )

        if not raw_llm_output or \
           "# IMPLEMENTATION_ERROR:" in raw_llm_output or \
           len(raw_llm_output.strip()) < 5:
            logger.warning(f"LLM did not provide a usable code snippet for component '{component_name}'. Output: {raw_llm_output}")
            return None

        cleaned_code_snippet = raw_llm_output.strip()
        if cleaned_code_snippet.startswith("```python"): # pragma: no cover
            cleaned_code_snippet = cleaned_code_snippet[len("```python"):].strip()
        if cleaned_code_snippet.endswith("```"): # pragma: no cover
            cleaned_code_snippet = cleaned_code_snippet[:-len("```")].strip()

        cleaned_code_snippet = cleaned_code_snippet.replace("\\n", "\n")

        logger.info(f"Successfully generated code snippet for component '{component_name}'. Length: {len(cleaned_code_snippet)}")
        return cleaned_code_snippet

    def _assemble_components(
        self,
        outline: Dict[str, Any],
        component_details: Dict[str, Optional[str]]
    ) -> str:
        logger.info("CodeService: Assembling components into final code string.")

        if not outline: # pragma: no cover
            logger.warning("_assemble_components called with no outline.")
            return "# Error: Outline was not provided for assembly."

        code_parts = []

        module_docstring = outline.get("module_docstring")
        if module_docstring: # pragma: no branch
            code_parts.append(f'"""{module_docstring}"""')
            code_parts.append("\n\n")

        imports = outline.get("imports", [])
        if imports: # pragma: no branch
            if code_parts and not code_parts[-1].endswith("\n\n"):
                if code_parts[-1] == "\n": code_parts[-1] = "\n\n"
                else: code_parts.append("\n\n")
            elif not code_parts:
                 pass

            for imp in imports:
                code_parts.append(f"import {imp}")
            code_parts.append("\n\n")

        if not code_parts:
            pass
        elif "".join(code_parts).isspace():
            code_parts = []

        components = outline.get("components", [])
        for i, component_def in enumerate(components):
            component_type = component_def.get("type")
            component_name = component_def.get("name")

            if not component_name: # pragma: no cover
                logger.warning(f"Skipping component with no name: {component_def}")
                code_parts.append(f"# SKIPPED COMPONENT: No name provided in outline for component {i+1}")
                continue

            if component_type == "function":
                func_code = component_details.get(component_name)
                if func_code:
                    code_parts.append(func_code)
                else: # pragma: no cover
                    signature = component_def.get("signature", "()")
                    desc = component_def.get("description", "No description.")
                    placeholder_body = component_def.get("body_placeholder", "pass # TODO: Implement")
                    code_parts.append(f"# Function '{component_name}' was planned but not generated.")
                    code_parts.append(f"def {component_name}{signature}:")
                    func_docstring_lines = [
                        f"    \"\"\"Placeholder for: {desc.splitlines()[0] if desc else ''}"]
                    if desc and '\n' in desc: # pragma: no cover
                        for line in desc.splitlines()[1:]:
                            func_docstring_lines.append(f"    {line.strip()}")
                    func_docstring_lines.append(f"    Original placeholder: {placeholder_body.splitlines()[0] if placeholder_body else ''}")
                    if placeholder_body and '\n' in placeholder_body: # pragma: no cover
                        for line in placeholder_body.splitlines()[1:]:
                            func_docstring_lines.append(f"    {line.strip()}")
                    func_docstring_lines.append("    \"\"\"")
                    code_parts.extend(func_docstring_lines)
                    code_parts.append(f"    pass")
                code_parts.append("\n\n")

            elif component_type == "class":
                class_name = component_name
                code_parts.append(f"class {class_name}:")

                class_docstring = component_def.get("description")
                if class_docstring: # pragma: no branch
                    indented_docstring = f'    """{class_docstring}"""'
                    code_parts.append(indented_docstring)
                    code_parts.append("")

                attributes = component_def.get("attributes", [])
                if attributes: # pragma: no branch
                    has_init_method = any(m.get("name") == "__init__" for m in component_def.get("methods", []))
                    if not has_init_method and attributes: # pragma: no cover
                        code_parts.append("    # Defined attributes (from outline):")
                        for attr in attributes:
                            attr_name = attr.get('name', 'unknown_attr')
                            attr_type = attr.get('type', '')
                            attr_desc = attr.get('description', '')
                            type_hint_str = f": {attr_type}" if attr_type else ""
                            comment_str = f" # {attr_desc}" if attr_desc else ""
                            code_parts.append(f"    {attr_name}{type_hint_str}{comment_str}")
                        code_parts.append("")


                methods = component_def.get("methods", [])
                if not methods and not attributes and not class_docstring :
                     code_parts.append("    pass")

                for method_def in methods:
                    method_name = method_def.get("name")
                    method_key = f"{class_name}.{method_name}"
                    method_code = component_details.get(method_key)

                    if method_code:
                        indented_method_code = "\n".join([f"    {line}" for line in method_code.splitlines()])
                        code_parts.append(indented_method_code)
                    else: # pragma: no cover
                        signature = method_def.get("signature", "(self)")
                        desc = method_def.get("description", "No description.")
                        placeholder_body = method_def.get("body_placeholder", "pass # TODO: Implement")
                        code_parts.append(f"    # Method '{method_name}' was planned but not generated.")
                        code_parts.append(f"    def {method_name}{signature}:")
                        docstring_lines = [
                            f"        \"\"\"Placeholder for: {desc.splitlines()[0] if desc else ''}"]
                        if desc and '\n' in desc: # pragma: no cover
                            for line in desc.splitlines()[1:]:
                                docstring_lines.append(f"        {line.strip()}")
                        docstring_lines.append(f"        Original placeholder: {placeholder_body.splitlines()[0] if placeholder_body else ''}")
                        if placeholder_body and '\n' in placeholder_body: # pragma: no cover
                            for line in placeholder_body.splitlines()[1:]:
                                docstring_lines.append(f"        {line.strip()}")
                        docstring_lines.append("        \"\"\"")
                        code_parts.extend(docstring_lines)
                        code_parts.append(f"        pass")
                    code_parts.append("")

                if code_parts and code_parts[-1] == "":
                    code_parts[-1] = "\n\n"
                else:
                    code_parts.append("\n\n")


            else: # pragma: no cover
                logger.warning(f"Unsupported component type '{component_type}' in outline for assembly.")
                code_parts.append(f"# UNSUPPORTED COMPONENT TYPE: {component_type} - {component_name}")

        main_block = outline.get("main_execution_block")
        if main_block: # pragma: no branch
            code_parts.append("")
            code_parts.append(main_block)
            code_parts.append("")

        final_code = "\n".join(code_parts)
        final_code = re.sub(r"\n{3,}", "\n\n", final_code)

        logger.info(f"Code assembly complete. Total length: {len(final_code)}")
        return final_code.strip()

    async def review_code(
        self,
        code_string: str,
        language: str = "python",
        review_type: str = "general", # New parameter: "general", "security", "refactoring"
        review_context: Optional[str] = None, # e.g., "NEW_TOOL_REVIEW", "SPECIFIC_MODULE_XYZ"
        llm_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        task_id: Optional[str] = None
        logs: List[str] = []

        related_item_id = f"review_{review_type}_{review_context or 'any'}_{hash(code_string)}"

        if self.task_manager:
            task_desc = f"Review_code: Type: {review_type}, Context: {review_context or 'N/A'}, Code snippet (first 50 chars): {code_string[:50].replace(chr(10), ' ')}..."
            task = self.task_manager.add_task(
                description=task_desc,
                task_type=ActiveTaskType.CODE_REVIEW,
                related_item_id=related_item_id
            )
            task_id = task.task_id

        logs.append(f"review_code called. Type: {review_type}, Context: {review_context}, Lang: {language}. Task ID: {task_id}")

        if language != "python": # pragma: no cover
            self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason="Unsupported language for review.")
            return {"status": "ERROR_UNSUPPORTED_LANGUAGE", "review_summary": None, "llm_suggestions": [], "linter_findings": [], "logs": logs, "error": "Unsupported language for review."}

        # Initialize return structure
        result: Dict[str, Any] = {
            "status": "PENDING_REVIEW",
            "review_summary": None,
            "llm_suggestions": [],
            "linter_findings": [],
            "logs": logs,
            "error": None
        }

        # 1. Run Linter
        self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Running linter") # Re-using status
        try:
            linter_messages, linter_error = await self._run_linter(code_string)
            result["linter_findings"] = linter_messages
            if linter_error:
                logs.append(f"Linter execution error: {linter_error}")
                # Potentially update status if linter error is critical, but for now, just log it.
                # If LLM review can still proceed, we might not want to fail the whole review.
        except Exception as e_lint: # pragma: no cover
            logs.append(f"Unexpected error during linting: {e_lint}")
            result["linter_findings"] = [f"Error running linter: {e_lint}"]
            # Not necessarily fatal for the whole review process if LLM can still run

        # 2. Get LLM Review (Implementation will be in the next step)
        # This part will involve:
        # - Formatting a prompt using a new LLM_CODE_REVIEW_PROMPT_TEMPLATE
        # - Calling self.llm_provider.invoke_ollama_model_async
        # - Parsing the LLM response
        # - Populating result["review_summary"] and result["llm_suggestions"]
        # - Updating result["status"] and result["error"] based on LLM call.
        # For now, placeholder:
        # logs.append("LLM review part not yet implemented.")
        # result["status"] = "SUCCESS_REVIEW_COMPLETED_LINTER_ONLY" # Placeholder status

        # 2. Get LLM Review
        llm_review_successful = False
        if not self.llm_provider:
            logs.append("LLM provider not configured. Skipping LLM review part.")
            result["error"] = "LLM provider missing, only linter review performed."
            # Keep status based on linter, or update if linter also had issues.
            # For now, let's assume linter_only is an acceptable partial success.
            result["status"] = "SUCCESS_REVIEW_COMPLETED_LINTER_ONLY" if not result.get("error") else "ERROR_LINTER_ONLY_LLM_MISSING"
        else:
            self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Requesting LLM code review")
            try:
                review_model = get_model_for_task("code_review")
                review_temp = 0.1 # Generally want reviews to be factual
                review_max_tokens = 2048

                if llm_config: # pragma: no cover
                    review_model = llm_config.get("model_name", review_model)
                    review_temp = llm_config.get("temperature", review_temp)
                    review_max_tokens = llm_config.get("max_tokens", review_max_tokens)

                prompt_template_to_use = LLM_CODE_REVIEW_PROMPT_TEMPLATE # Default
                if review_type == "security":
                    prompt_template_to_use = LLM_SECURITY_REVIEW_PROMPT_TEMPLATE
                    logs.append("Using Security Review Prompt Template.")
                elif review_type == "refactoring":
                    prompt_template_to_use = LLM_REFACTORING_REVIEW_PROMPT_TEMPLATE
                    logs.append("Using Refactoring Review Prompt Template.")
                else: # Default or "general"
                    logs.append("Using General Code Review Prompt Template.")

                formatted_prompt = prompt_template_to_use.format(
                    code_to_review=code_string,
                    review_context=review_context or "N/A"
                )
                logs.append(f"Sending code review prompt to LLM. Model: {review_model}, Temp: {review_temp}, Type: {review_type}")

                raw_llm_output = await self.llm_provider.invoke_ollama_model_async(
                    formatted_prompt, model_name=review_model, temperature=review_temp, max_tokens=review_max_tokens
                )

                if raw_llm_output and raw_llm_output.strip():
                    logs.append(f"Raw LLM review output length: {len(raw_llm_output)}")
                    try:
                        # Attempt to parse the entire output as JSON
                        # Basic cleaning for common markdown issues
                        cleaned_json_str = raw_llm_output.strip()
                        if cleaned_json_str.startswith("```json"):
                            cleaned_json_str = cleaned_json_str[len("```json"):].strip()
                        if cleaned_json_str.endswith("```"):
                            cleaned_json_str = cleaned_json_str[:-len("```")].strip()

                        parsed_review = json.loads(cleaned_json_str)

                        result["review_summary"] = parsed_review.get("overall_summary")
                        result["llm_suggestions"] = parsed_review.get("suggestions", [])
                        logs.append(f"Successfully parsed LLM review. Summary: {result['review_summary'][:50]}..., Suggestions: {len(result['llm_suggestions'])}")
                        llm_review_successful = True
                    except json.JSONDecodeError as e_json:
                        logs.append(f"Failed to parse LLM review JSON: {e_json}. Raw output: {raw_llm_output[:200]}...")
                        result["error"] = f"LLM review output was not valid JSON: {e_json}"
                        # Store raw output as a fallback suggestion if parsing fails
                        result["llm_suggestions"] = [{"line_start":0, "line_end":0, "severity": "Info", "comment": f"LLM Raw Output (JSON Parse Failed):\n{raw_llm_output}"}]
                else:
                    logs.append("LLM returned empty response for code review.")
                    result["error"] = "LLM returned no response for review."

                if llm_review_successful:
                    result["status"] = "SUCCESS_REVIEW_COMPLETED"
                else:
                    result["status"] = "ERROR_LLM_REVIEW_FAILED"
                    if not result["error"]: result["error"] = "LLM review failed for unknown reasons."


            except Exception as e_llm_review: # pragma: no cover
                logger.error(f"Unexpected error during LLM code review: {e_llm_review}", exc_info=True)
                logs.append(f"LLM review exception: {e_llm_review}")
                result["status"] = "ERROR_LLM_REVIEW_UNEXPECTED"
                result["error"] = str(e_llm_review)

        # Final status determination
        if result["status"] == "PENDING_REVIEW": # Should have been updated by linter or LLM part
            if result["linter_findings"] and not llm_review_successful and not self.llm_provider: # Only linter ran and LLM was missing
                 result["status"] = "SUCCESS_REVIEW_COMPLETED_LINTER_ONLY"
            elif not result["linter_findings"] and not llm_review_successful and not self.llm_provider:
                 result["status"] = "SUCCESS_REVIEW_COMPLETED_LINTER_ONLY" # No findings from linter, LLM missing
            elif result["error"]: # Some error occurred
                 result["status"] = "ERROR_REVIEW_FAILED_PARTIALLY" # Generic partial failure
            else: # Should not happen
                 result["status"] = "ERROR_REVIEW_STATUS_UNCLEAR"


        if "SUCCESS" in result["status"]:
             self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Code review process completed.", step_desc=result["status"])
        elif result["error"]:
             self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error", "Review failed"), step_desc=result["status"])
        # else: some intermediate status or specific failure not covered by above

        return result


if __name__ == '__main__': # pragma: no cover
    import os
    import asyncio
    import tempfile
    import shutil

    class MockLLMProvider:
        async def invoke_ollama_model_async(self, prompt: str, model_name: str, temperature: float, max_tokens: int) -> str:
            logger.info(f"MockLLMProvider.invoke_ollama_model_async for model: {model_name}, prompt starts with: {prompt[:60].replace(chr(10), ' ')}...")

            if LLM_NEW_TOOL_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                 logger.info("MockLLMProvider: Matched NEW_TOOL prompt.")
                 if "generate bad code for lint test" in prompt:
                     logger.info("MockLLMProvider: Matched 'generate bad code for lint test' FOR NEW_TOOL.")
                     return '# METADATA: {"suggested_function_name": "new_tool_bad_lint", "suggested_tool_name": "newToolBadLint", "suggested_description": "Tool with bad lint."}\nBAD_CODE_EXAMPLE_FOR_LINT_TEST'
                 if "generate good code for lint test" in prompt:
                     logger.info("MockLLMProvider: Matched 'generate good code for lint test' FOR NEW_TOOL.")
                     return '# METADATA: {"suggested_function_name": "new_tool_good_lint", "suggested_tool_name": "newToolGoodLint", "suggested_description": "Tool with good lint."}\nGOOD_CODE_EXAMPLE_FOR_LINT_TEST'
                 if "generate code for linter failure test" in prompt:
                     logger.info("MockLLMProvider: Matched 'generate code for linter failure test' FOR NEW_TOOL.")
                     return '# METADATA: {"suggested_function_name": "new_tool_linter_fail", "suggested_tool_name": "newToolLinterFail", "suggested_description": "Tool for linter fail."}\nLINTER_FAILURE_EXAMPLE_FOR_LINT_TEST'
                 return '# METADATA: {"suggested_function_name": "mock_sum_function", "suggested_tool_name": "mockSumTool", "suggested_description": "A mock sum function."}\ndef mock_sum_function(a: int, b: int) -> int:\n    """Adds two integers."""\n    return a + b'

            elif LLM_CODE_FIX_PROMPT_TEMPLATE.splitlines()[1] in prompt:
                 logger.info("MockLLMProvider: Matched SELF_FIX_TOOL prompt.")
                 return "def function_to_be_fixed_by_main_svc(val: int) -> int:\n    return val + 10 # Fixed!\n"

            elif LLM_HIERARCHICAL_OUTLINE_PROMPT_TEMPLATE.splitlines()[1] in prompt:
                logger.info("MockLLMProvider: Matched HIERARCHICAL_OUTLINE prompt.")
                description_in_prompt = re.search(r"High-Level Requirement:\n(.*?)\n\nJSON Outline:", prompt, re.DOTALL)
                desc_text = description_in_prompt.group(1).strip() if description_in_prompt else ""

                base_outline = {
                    "module_name": "test_module.py",
                    "description": "A test module generated by mock.",
                    "imports": ["json", "os"],
                    "components": [],
                    "main_execution_block": "if __name__ == '__main__':\n    print('Mock test module ready!')"
                }

                if "generate bad code for lint test hierarchical" in desc_text:
                    logger.info("MockLLMProvider: Outline for HIERARCHICAL BAD LINT TEST.")
                    base_outline["components"].append({
                        "type": "function", "name": "buggy_function_bad_lint", "signature": "() -> None",
                        "description": "A function designed to have lint errors for hierarchical test.", "body_placeholder": "Implement with syntax errors for bad lint test."
                    })
                elif "generate good code for lint test hierarchical" in desc_text:
                    logger.info("MockLLMProvider: Outline for HIERARCHICAL GOOD LINT TEST.")
                    base_outline["components"].append({
                        "type": "function", "name": "good_function_good_lint", "signature": "() -> str",
                        "description": "A function designed to be lint-free for hierarchical test.", "body_placeholder": "Implement correctly for good lint test."
                    })
                elif "generate code for linter failure test hierarchical" in desc_text:
                    logger.info("MockLLMProvider: Outline for HIERARCHICAL LINTER FAILURE TEST.")
                    base_outline["components"].append({
                        "type": "function", "name": "function_linter_fail", "signature": "() -> None",
                        "description": "A function for hierarchical linter failure test.", "body_placeholder": "Implement for linter failure test."
                    })
                else:
                    logger.info("MockLLMProvider: Using default todo_cli.py outline for hierarchical generation.")
                    return json.dumps({
                        "module_name": "todo_cli.py",
                        "description": "A CLI tool for managing a to-do list, generated by mock LLM.",
                        "imports": ["json", "argparse"],
                        "components": [
                            {
                                "type": "function", "name": "load_todos", "signature": "(filepath: str) -> list",
                                "description": "Loads to-dos from a JSON file.", "body_placeholder": "Read JSON file, handle FileNotFoundError, return empty list if not found."
                            },
                            {
                                "type": "function", "name": "save_todos", "signature": "(filepath: str, todos: list) -> None",
                                "description": "Saves to-dos to a JSON file.", "body_placeholder": "Write JSON file, use indent=2 for readability."
                            },
                            {
                                "type": "class", "name": "TodoManager", "description": "Manages todo operations using a JSON file.",
                                "attributes": [{"name": "filepath", "type": "str", "description": "Path to the todo JSON file"}],
                                "methods": [
                                    {
                                        "type": "method", "name": "__init__", "signature": "(self, filepath: str)",
                                        "description": "Initializes TodoManager with the filepath.", "body_placeholder": "self.filepath = filepath"
                                    },
                                    {
                                        "type": "method", "name": "add_item", "signature": "(self, item_text: str)",
                                        "description": "Adds a new todo item.", "body_placeholder": "Load todos, append new item (dict with 'task' and 'done': False), then save todos. Use self.filepath."
                                    }
                                ]
                            }
                        ],
                        "main_execution_block": "if __name__ == '__main__':\n    parser = argparse.ArgumentParser(description='Manage your to-do list.')\n    # Add arguments for add, list, etc.\n    # args = parser.parse_args()\n    # Implement CLI logic based on args\n    print('Mock CLI Tool Ready!')"
                    })
                return json.dumps(base_outline)

            elif LLM_COMPONENT_DETAIL_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                logger.info("MockLLMProvider: Matched COMPONENT_DETAIL prompt.")
                if "buggy_function_bad_lint" in prompt:
                    logger.info("MockLLMProvider: Detail for buggy_function_bad_lint (hierarchical).")
                    return "def buggy_function_bad_lint() -> None:\n    BAD_CODE_EXAMPLE_FOR_LINT_TEST # Intentionally bad"
                elif "good_function_good_lint" in prompt:
                    logger.info("MockLLMProvider: Detail for good_function_good_lint (hierarchical).")
                    return "def good_function_good_lint() -> str:\n    return 'GOOD_CODE_EXAMPLE_FOR_LINT_TEST' # Intentionally good"
                elif "function_linter_fail" in prompt:
                    logger.info("MockLLMProvider: Detail for function_linter_fail (hierarchical).")
                    return "def function_linter_fail() -> None:\n    # This code will cause the mock linter to simulate a failure\n    LINTER_FAILURE_EXAMPLE_FOR_LINT_TEST"
                elif "load_todos" in prompt:
                    return "def load_todos(filepath: str) -> list:\n    \"\"\"Loads to-dos from a JSON file.\"\"\"\n    try:\n        with open(filepath, 'r') as f:\n            return json.load(f)\n    except FileNotFoundError:\n        return []"
                elif "save_todos" in prompt:
                    return "def save_todos(filepath: str, todos: list) -> None:\n    \"\"\"Saves to-dos to a JSON file.\"\"\"\n    with open(filepath, 'w') as f:\n        json.dump(todos, f, indent=2)"
                elif "TodoManager.__init__" in prompt:
                    return "def __init__(self, filepath: str):\n    \"\"\"Initializes TodoManager.\"\"\"\n    self.filepath = filepath"
                elif "TodoManager.add_item" in prompt:
                     return "def add_item(self, item_text: str):\n    \"\"\"Adds an item using the manager.\"\"\"\n    print(f\"Mock TodoManager added: {item_text} to {{self.filepath}}\") # Simplified for mock"

                logger.warning(f"MockLLMProvider: No specific mock for COMPONENT_DETAIL prompt part: {prompt[:100]}...")
                return f"# Code for component based on prompt: {prompt[:100]}...\npass # Default mock implementation"

            elif LLM_GRANULAR_REFACTOR_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                logger.info("MockLLMProvider: Matched GRANULAR_REFACTOR prompt.")
                original_code_match = re.search(r"Original Function Code:\n```python\n(.*?)\n```", prompt, re.DOTALL)
                if original_code_match:
                    original_code_content = original_code_match.group(1)
                    refactor_instruction_match = re.search(r"Refactoring Instruction:\n(.*?)\n\nConstraints:", prompt, re.DOTALL)
                    instruction = refactor_instruction_match.group(1).strip() if refactor_instruction_match else "No instruction found"
                    modified_code = f"# Refactored based on: {instruction}\n{original_code_content}"
                    return modified_code
                return "// REFACTORING_SUGGESTION_IMPOSSIBLE (Mock could not parse original code)" # pragma: no cover

            elif LLM_UNIT_TEST_SCAFFOLD_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                logger.info("MockLLMProvider: Matched UNIT_TEST_SCAFFOLD prompt.")
                return "import unittest\n\nclass TestGeneratedCode(unittest.TestCase):\n    def test_something(self):\n        self.fail('Not implemented')"

            logger.warning(f"MockLLMProvider: No specific mock for prompt starting with: {prompt[:60]}...")
            return "// NO_CODE_SUGGESTION_POSSIBLE (MockLLMProvider Fallback)"

    class MockSelfModService:
        def get_function_source_code(self, module_path, function_name):
            logger.info(f"MockSelfModService.get_function_source_code called for {module_path}.{function_name}")
            if module_path == "ai_assistant.custom_tools.dummy_tool_main_test_svc" and \
               function_name == "function_to_be_fixed_by_main_svc":
                return "def function_to_be_fixed_by_main_svc(val: int) -> int:\n    return val * 10 # Should be val + 10\n"
            return None

    async def main_illustrative_test():
        mock_llm_provider_instance = MockLLMProvider()
        mock_self_mod_service_instance = MockSelfModService()
        mock_notification_manager_instance = NotificationManager() # Added
        mock_task_manager_instance = TaskManager(notification_manager=mock_notification_manager_instance) # Added TaskManager

        code_service = CodeService(
            llm_provider=mock_llm_provider_instance,
            self_modification_service=mock_self_mod_service_instance,
            task_manager=mock_task_manager_instance, # Pass TaskManager
            notification_manager=mock_notification_manager_instance # Pass NotificationManager
        )

        original_run_linter = code_service._run_linter

        async def mock_run_linter(code_string: str) -> Tuple[List[str], Optional[str]]:
            logger.info(f"mock_run_linter called with code_string snippet: '{code_string[:50].replace(chr(10),' ')}...'")
            if "GOOD_CODE_EXAMPLE_FOR_LINT_TEST" in code_string:
                logger.info("mock_run_linter: Detected GOOD_CODE marker.")
                return ([], None)
            elif "BAD_CODE_EXAMPLE_FOR_LINT_TEST" in code_string:
                logger.info("mock_run_linter: Detected BAD_CODE marker.")
                return (["LINT (Mocked): E999 SyntaxError at 1:1: Example syntax error from BAD_CODE marker"], None)
            elif "LINTER_FAILURE_EXAMPLE_FOR_LINT_TEST" in code_string:
                logger.info("mock_run_linter: Detected LINTER_FAILURE marker.")
                return ([], "Mocked linter execution error: Linters not found due to LINTER_FAILURE marker.")
            logger.info("mock_run_linter: No specific marker detected, assuming good code for mock.")
            return ([], None)

        test_output_dir = tempfile.mkdtemp(prefix="codesvc_main_test_")
        print(f"Test outputs will be saved in: {test_output_dir}")

        print("\n--- Testing: generate_code (NEW_TOOL) with Linter ---")
        code_service._run_linter = mock_run_linter

        new_tool_bad_lint_path = os.path.join(test_output_dir, "new_tool_bad_lint.py")
        gen_result_bad_lint = await code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="generate bad code for lint test",
            target_path=new_tool_bad_lint_path
        )
        print(f"  Bad Lint - Status: {gen_result_bad_lint.get('status')}, Saved: {gen_result_bad_lint.get('saved_to_path')}")
        assert any("LINT (Mocked): E999 SyntaxError" in log for log in gen_result_bad_lint.get("logs", [])), "Bad lint message not found in logs"
        print("    Verified: Bad lint message in logs.")

        new_tool_good_lint_path = os.path.join(test_output_dir, "new_tool_good_lint.py")
        gen_result_good_lint = await code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="generate good code for lint test",
            target_path=new_tool_good_lint_path
        )
        print(f"  Good Lint - Status: {gen_result_good_lint.get('status')}, Saved: {gen_result_good_lint.get('saved_to_path')}")
        assert not any("LINT (Mocked):" in log for log in gen_result_good_lint.get("logs", [])), "Good lint test should have no lint messages"
        print("    Verified: No lint messages for good code.")

        new_tool_linter_fail_path = os.path.join(test_output_dir, "new_tool_linter_fail.py")
        gen_result_linter_fail = await code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="generate code for linter failure test",
            target_path=new_tool_linter_fail_path
        )
        print(f"  Linter Fail - Status: {gen_result_linter_fail.get('status')}, Saved: {gen_result_linter_fail.get('saved_to_path')}")
        assert any("Linter execution error: Mocked linter execution error" in log for log in gen_result_linter_fail.get("logs", [])), "Linter execution error not found in logs"
        print("    Verified: Linter execution error in logs.")

        code_service._run_linter = original_run_linter

        print("\n--- Testing: modify_code (SELF_FIX_TOOL) ---")
        mod_result_fix = await code_service.modify_code(
            context="SELF_FIX_TOOL",
            modification_instruction="Fix multiplication to addition.",
            module_path="dummy_module.py", function_name="function_to_be_fixed_by_main_svc"
        )
        print(f"  Status: {mod_result_fix.get('status')}")
        if mod_result_fix.get('modified_code_string'): print(f"  Code (first 50): {mod_result_fix['modified_code_string'][:50].replace(chr(10), ' ')}...")
        if mod_result_fix.get('error'): print(f"  Error: {mod_result_fix['error']}")

        print("\n--- Testing: generate_code (GENERATE_UNIT_TEST_SCAFFOLD) ---")
        scaffold_path = os.path.join(test_output_dir, "test_scaffold.py")
        scaffold_result = await code_service.generate_code(
            context="GENERATE_UNIT_TEST_SCAFFOLD",
            prompt_or_description="def example_func():\n  pass",
            additional_context={"module_name_hint": "example_module"},
            target_path=scaffold_path
        )
        print(f"  Status: {scaffold_result.get('status')}, Saved: {scaffold_result.get('saved_to_path')}")
        if scaffold_result.get('code_string'): print(f"  Code (first 80): {scaffold_result['code_string'][:80].replace(chr(10), ' ')}...")
        if scaffold_result.get('error'): print(f"  Error: {scaffold_result['error']}")

        print("\n--- Testing: generate_code (EXPERIMENTAL_HIERARCHICAL_OUTLINE) ---")
        outline_desc = "CLI tool for to-do list in JSON."
        outline_result = await code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_OUTLINE",
            prompt_or_description=outline_desc
        )
        print(f"  Status: {outline_result.get('status')}")
        if outline_result.get("parsed_outline"): print(f"  Outline Module: {outline_result['parsed_outline'].get('module_name')}, Components: {len(outline_result['parsed_outline'].get('components', []))}")
        if outline_result.get("error"): print(f"  Error: {outline_result.get('error')}")

        print("\n--- Testing: generate_code (EXPERIMENTAL_HIERARCHICAL_FULL_TOOL) ---")
        full_tool_desc = "Advanced CLI tool for to-do list with features."
        full_tool_result = await code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL",
            prompt_or_description=full_tool_desc
        )
        print(f"  Status: {full_tool_result.get('status')}")
        if full_tool_result.get("parsed_outline"): print(f"  Outline Module: {full_tool_result['parsed_outline'].get('module_name')}")
        if full_tool_result.get("component_details"):
            print(f"  Component Details: {len(full_tool_result['component_details'])} generated/attempted.")
            expected_comp_key = "TodoManager.add_item"
            if expected_comp_key in full_tool_result["component_details"]:
                 print(f"    Detail for '{expected_comp_key}': {'Generated' if full_tool_result['component_details'][expected_comp_key] else 'Failed/None'}")
        if full_tool_result.get("error"): print(f"  Error: {full_tool_result.get('error')}")

        print("\n--- Testing: generate_code (HIERARCHICAL_GEN_COMPLETE_TOOL) with Linter ---")
        code_service._run_linter = mock_run_linter

        hier_bad_lint_path = os.path.join(test_output_dir, "hier_bad_lint.py")
        complete_tool_result_bad_lint = await code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="generate bad code for lint test hierarchical",
            target_path=hier_bad_lint_path
        )
        print(f"  Hierarchical Bad Lint - Status: {complete_tool_result_bad_lint.get('status')}, Saved: {complete_tool_result_bad_lint.get('saved_to_path')}")
        assert any("LINT (Mocked): E999 SyntaxError" in log for log in complete_tool_result_bad_lint.get("logs", [])), "Bad lint message not found in HIERARCHICAL logs"
        assert "BAD_CODE_EXAMPLE_FOR_LINT_TEST" in complete_tool_result_bad_lint.get("code_string", ""), "Bad lint code marker not found in HIERARCHICAL assembled output"
        print("    Verified: Bad lint message and code marker in HIERARCHICAL logs/output.")

        hier_good_lint_path = os.path.join(test_output_dir, "hier_good_lint.py")
        complete_tool_result_good_lint = await code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="generate good code for lint test hierarchical",
            target_path=hier_good_lint_path
        )
        print(f"  Hierarchical Good Lint - Status: {complete_tool_result_good_lint.get('status')}, Saved: {complete_tool_result_good_lint.get('saved_to_path')}")
        assert not any("LINT (Mocked):" in log for log in complete_tool_result_good_lint.get("logs", [])), "Good lint HIERARCHICAL test should have no lint messages"
        assert "GOOD_CODE_EXAMPLE_FOR_LINT_TEST" in complete_tool_result_good_lint.get("code_string", ""), "Good lint code marker not found in HIERARCHICAL assembled output"
        print("    Verified: No lint messages and good code marker in HIERARCHICAL logs/output.")

        hier_linter_fail_path = os.path.join(test_output_dir, "hier_linter_fail.py")
        complete_tool_result_linter_fail = await code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="generate code for linter failure test hierarchical",
            target_path=hier_linter_fail_path
        )
        print(f"  Hierarchical Linter Fail - Status: {complete_tool_result_linter_fail.get('status')}, Saved: {complete_tool_result_linter_fail.get('saved_to_path')}")
        assert any("Linter execution error for assembled code: Mocked linter execution error" in log for log in complete_tool_result_linter_fail.get("logs", [])), "HIERARCHICAL linter execution error not found in logs"
        assert "LINTER_FAILURE_EXAMPLE_FOR_LINT_TEST" in complete_tool_result_linter_fail.get("code_string", ""), "Linter fail code marker not found in HIERARCHICAL assembled output"
        print("    Verified: HIERARCHICAL linter execution error and relevant code marker in logs/output.")

        code_service._run_linter = original_run_linter

        print("\n--- Testing: modify_code (GRANULAR_CODE_REFACTOR) ---")
        granular_original_code = "def process_data(data_list):\n    for item in data_list:\n        print(item)\n    return len(data_list)"
        granular_section_id = "for item in data_list:\n        print(item)"
        granular_instruction = "Add a comment '# Processing item' inside the loop before the print statement."

        granular_refactor_result = await code_service.modify_code(
            context="GRANULAR_CODE_REFACTOR",
            modification_instruction=granular_instruction,
            existing_code=granular_original_code,
            module_path="test_module.py",
            function_name="process_data",
            additional_context={"section_identifier": granular_section_id}
        )
        print(f"  Status: {granular_refactor_result.get('status')}")
        if granular_refactor_result.get('modified_code_string'):
            print(f"  Modified Code:\n{granular_refactor_result['modified_code_string']}")
        if granular_refactor_result.get('error'): print(f"  Error: {granular_refactor_result['error']}")

        try:
            print(f"\nIllustrative test finished. Cleaning up test output directory: {test_output_dir}")
            shutil.rmtree(test_output_dir)
            print(f"Successfully removed {test_output_dir}")
        except Exception as e_cleanup: # pragma: no cover
            print(f"Error cleaning up test directory: {e_cleanup}")


    if os.name == 'nt': # pragma: no cover
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main_illustrative_test())
