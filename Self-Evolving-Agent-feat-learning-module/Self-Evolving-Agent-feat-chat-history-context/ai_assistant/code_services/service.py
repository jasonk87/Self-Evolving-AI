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

class CodeService:
    def __init__(self, llm_provider: Optional[Any] = None,
                 self_modification_service: Optional[Any] = None,
                 task_manager: Optional[TaskManager] = None): # New parameter
        self.llm_provider = llm_provider
        self.self_modification_service = self_modification_service
        self.task_manager = task_manager # Store it
        logger.info("CodeService initialized.")
        if is_debug_mode(): # pragma: no cover
            print(f"[DEBUG] CodeService initialized with llm_provider: {llm_provider}, self_modification_service: {self_modification_service}, task_manager: {task_manager}")
        if not self.llm_provider: # pragma: no cover
            logger.warning("CodeService initialized without an LLM provider. Code generation capabilities will be limited.")
        if not self.self_modification_service: # pragma: no cover
            logger.warning("CodeService initialized without a self-modification service. File operations will be limited.")
        if not self.task_manager: # pragma: no cover
            logger.info("CodeService initialized without a TaskManager. Task status updates will be skipped.")

    def _update_task(self, task_id: Optional[str], status: ActiveTaskStatus, reason: Optional[str] = None, step_desc: Optional[str] = None):
        if task_id and self.task_manager:
            self.task_manager.update_task_status(task_id, status, reason=reason, step_desc=step_desc)

    async def generate_code(
        self,
        context: str,
        prompt_or_description: str, # For "NEW_TOOL", this is tool desc. For "SCAFFOLD", this is the code to test.
        language: str = "python",
        target_path: Optional[str] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        additional_context: Optional[Dict[str, Any]] = None # Use for module_name_hint
    ) -> Dict[str, Any]:
        task_id: Optional[str] = None
        result: Dict[str, Any] = {} # Initialize result
        # Determine task_type based on context for TaskManager
        task_type_for_manager: ActiveTaskType = ActiveTaskType.MISC_CODE_GENERATION # Default
        related_id_for_task: Optional[str] = prompt_or_description[:70]

        if context == "NEW_TOOL":
            task_type_for_manager = ActiveTaskType.AGENT_TOOL_CREATION
        elif context == "HIERARCHICAL_GEN_COMPLETE_TOOL":
            # This could be for agent tools or user projects. For now, assume AGENT_TOOL if not specified.
            # A more robust solution might involve passing more context to determine this.
            task_type_for_manager = ActiveTaskType.AGENT_TOOL_CREATION
        elif context == "GENERATE_UNIT_TEST_SCAFFOLD":
            task_type_for_manager = ActiveTaskType.MISC_CODE_GENERATION # Or a new UNIT_TEST_GENERATION
            related_id_for_task = additional_context.get("module_name_hint", "scaffold_target") if additional_context else "scaffold_target"
        elif context == "EXPERIMENTAL_HIERARCHICAL_OUTLINE":
            task_type_for_manager = ActiveTaskType.PLANNING_CODE_STRUCTURE # Assumed Enum member
        elif context == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
             task_type_for_manager = ActiveTaskType.MISC_CODE_GENERATION # As it's detail generation

        if self.task_manager:
            task_desc = f"Generate_code: {context}, Target: {prompt_or_description[:50]}..."
            task = self.task_manager.add_task(
                task_type=task_type_for_manager,
                description=task_desc,
                related_item_id=related_id_for_task
            )
            task_id = task.task_id

        try:
            logger.info(f"CodeService.generate_code called with context='{context}', description='{prompt_or_description[:50]}...' (Task ID: {task_id})")

            if not self.llm_provider: # Check if provider is configured
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

            # Default LLM parameters
        model_to_use = get_model_for_task("code_generation") # Can be specialized later
        temperature = 0.3 # Scaffolds should be somewhat deterministic
        max_tokens_to_use = 2048 # Allow for larger scaffolds

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

            if cleaned_code: # Only run linter if there's code
                lint_messages, lint_run_error = await self._run_linter(cleaned_code)
                if lint_run_error:
                    logs.append(f"Linter execution error: {lint_run_error}")
                    # Optionally, you could set error_new_tool here or change status,
                    # but current requirement is to only log.
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
                "saved_to_path": saved_to_path_val, # New field
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

            # For scaffolds, we expect the entire output to be the code after cleaning. No metadata line.
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
                "metadata": None, # No specific metadata for scaffolds
                "saved_to_path": saved_to_path_scaffold, # New field
                "logs": logs,
                "error": error_scaffold
            }

        elif context == "EXPERIMENTAL_HIERARCHICAL_OUTLINE":
            high_level_description = prompt_or_description
            # Initialize logs for this specific context handling
            logs = [f"Context: EXPERIMENTAL_HIERARCHICAL_OUTLINE. Desc: {high_level_description[:50]}... (Task ID: {task_id})"]

            self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Calling _generate_hierarchical_outline")

            # Call the refactored private method
            outline_gen_result = await self._generate_hierarchical_outline(high_level_description, llm_config)

            # Append logs from the private method call to the current context's logs
            logs.extend(outline_gen_result.get("logs", []))

            # Format the result to match the expected structure for this context
            result = {
                "status": outline_gen_result.get("status", "ERROR_UNDEFINED_OUTLINE_FAILURE"), # Default status if not in result
                "outline_str": outline_gen_result.get("outline_str"),
                "parsed_outline": outline_gen_result.get("parsed_outline"),
                "code_string": None,  # Explicitly None as per requirements for this context
                "metadata": None,     # Explicitly None as per requirements for this context
                "logs": logs,
                "error": outline_gen_result.get("error")
            }

            # Update task status based on the outcome of the outline generation
            if result["status"] == "SUCCESS_OUTLINE_GENERATED":
                self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Hierarchical outline generated successfully.")
            else:
                # Use a more specific failure status if possible, or fall back to FAILED_UNKNOWN
                failure_reason = result.get("error", "Outline generation failed.")
                step_description = result.get("status", "Outline generation failed")
                self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=failure_reason, step_desc=step_description)

            return result

        elif context == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
            high_level_description = prompt_or_description
            logs = [f"Context: EXPERIMENTAL_HIERARCHICAL_FULL_TOOL. Desc: {high_level_description[:50]}... (Task ID: {task_id})"]

            self._update_task(task_id, ActiveTaskStatus.PLANNING_CODE_STRUCTURE, step_desc="Generating outline via _generate_hierarchical_outline")

            # Directly call _generate_hierarchical_outline
            outline_gen_result = await self._generate_hierarchical_outline(high_level_description, llm_config)

            # Append logs from the outline generation
            logs.extend(outline_gen_result.get("logs", []))
            parsed_outline = outline_gen_result.get("parsed_outline")
            current_status = outline_gen_result.get("status")
            current_error = outline_gen_result.get("error")

            if current_status != "SUCCESS_OUTLINE_GENERATED" or not parsed_outline:
                logs.append("Outline generation failed or outline is empty, cannot proceed to detail generation.")
                result = {
                    "status": current_status if current_status else "ERROR_OUTLINE_GENERATION_FAILED",
                    "parsed_outline": parsed_outline,
                    "component_details": None,
                    "code_string": None,
                    "metadata": None,
                    "logs": logs,
                    "error": current_error if current_error else "Outline generation failed or outline was empty."
                }
                self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error"), step_desc=result.get("status"))
                return result

            self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Generating details for components based on new outline")
            component_details: Dict[str, Optional[str]] = {}
            all_details_succeeded = True
            any_detail_succeeded = False

            components_to_generate = []
            # Extract functions and methods from outline to prepare for detail generation
            if parsed_outline.get("components"): # pragma: no branch
                for component_def in parsed_outline["components"]:
                    if component_def.get("type") == "function":
                        components_to_generate.append(component_def)
                    elif component_def.get("type") == "class" and component_def.get("methods"): # pragma: no branch
                        for method_def in component_def["methods"]:
                            method_key = f"{component_def.get('name', 'UnknownClass')}.{method_def.get('name', 'UnknownMethod')}"
                            components_to_generate.append({
                                **method_def,
                                "name": method_key,
                                "original_name": method_def.get("name"),
                                "class_context": component_def
                            })

            logs.append(f"Found {len(components_to_generate)} components (functions/methods) for detail generation.")

            for comp_def_for_detail_gen in components_to_generate:
                current_comp_key = comp_def_for_detail_gen.get("name")

                logs.append(f"Generating details for component key: {current_comp_key}")
                # Use the parsed_outline obtained from the direct call
                detail_code = await self._generate_detail_for_component(
                    component_definition=comp_def_for_detail_gen,
                    full_outline=parsed_outline,
                    llm_config=llm_config
                )
                if detail_code:
                    component_details[current_comp_key] = detail_code
                    logs.append(f"Successfully generated details for {current_comp_key}.")
                    any_detail_succeeded = True
                else: # pragma: no cover
                    component_details[current_comp_key] = None
                    logs.append(f"Failed to generate details for {current_comp_key}.")
                    all_details_succeeded = False

            # Determine status and error based on detail generation outcomes
            # current_error already holds error from outline generation, if any.
            # We might want to append or prioritize detail generation errors.
            detail_gen_status = "ERROR_DETAIL_GENERATION_FAILED"
            if not current_error: # Only set detail error if no outline error
                current_error = None # Reset for detail specific errors.

            if all_details_succeeded and any_detail_succeeded :
                detail_gen_status = "SUCCESS_HIERARCHICAL_DETAILS_GENERATED"
            elif any_detail_succeeded:
                 detail_gen_status = "PARTIAL_HIERARCHICAL_DETAILS_GENERATED"
                 if not current_error: current_error = "Some component details failed generation."
            else:
                 if not components_to_generate:
                     detail_gen_status = "SUCCESS_HIERARCHICAL_DETAILS_GENERATED" # No components, so details are "successful"
                     logs.append("No components found in outline for detail generation.")
                 elif not current_error: # Only set if no prior error from outline or partial success
                     current_error = "All component details failed generation."

            logs.append(f"Detail generation phase status: {detail_gen_status}")

            result = {
                "status": detail_gen_status,
                "parsed_outline": parsed_outline,
                "component_details": component_details,
                "code_string": None, # This context does NOT assemble code
                "metadata": None,
                "logs": logs,
                "error": current_error # This will now include errors from outline or detail phase
            }

            if detail_gen_status == "SUCCESS_HIERARCHICAL_DETAILS_GENERATED":
                 self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Outline and all component details generated.")
            elif detail_gen_status == "PARTIAL_HIERARCHICAL_DETAILS_GENERATED":
                 self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error", "Partial success in generating component details."), step_desc=detail_gen_status)
            else: # ERROR_DETAIL_GENERATION_FAILED or if outline failed initially
                 self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error", "Failed to generate component details."), step_desc=detail_gen_status)
            return result

        elif context == "HIERARCHICAL_GEN_COMPLETE_TOOL":
            high_level_description = prompt_or_description
            logs = [f"Context: HIERARCHICAL_GEN_COMPLETE_TOOL. Desc: {high_level_description[:50]}... (Task ID: {task_id})"]

            # Step 1: Generate Outline
            self._update_task(task_id, ActiveTaskStatus.PLANNING_CODE_STRUCTURE, step_desc="Generating outline for complete tool")
            outline_gen_result = await self._generate_hierarchical_outline(high_level_description, llm_config)

            logs.extend(outline_gen_result.get("logs", []))
            parsed_outline = outline_gen_result.get("parsed_outline")
            current_status = outline_gen_result.get("status")
            current_error = outline_gen_result.get("error")

            if current_status != "SUCCESS_OUTLINE_GENERATED" or not parsed_outline:
                logs.append("Outline generation failed or produced no data. Cannot proceed with detail generation or assembly.")
                result = {
                    "status": current_status if current_status else "ERROR_OUTLINE_GENERATION_FAILED",
                    "parsed_outline": parsed_outline,
                    "component_details": None,
                    "code_string": None,
                    "metadata": None,
                    "logs": logs,
                    "error": current_error if current_error else "Outline generation failed or was empty."
                }
                self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc="Outline generation failed")
                return result

            # Step 2: Generate Details for each component
            self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Generating details for components")
            component_details: Dict[str, Optional[str]] = {}
            all_details_succeeded = True
            any_detail_succeeded = False

            components_to_generate = []
            if parsed_outline.get("components"): # pragma: no branch
                for component_def in parsed_outline["components"]:
                    if component_def.get("type") == "function":
                        components_to_generate.append(component_def)
                    elif component_def.get("type") == "class" and component_def.get("methods"): # pragma: no branch
                        for method_def in component_def["methods"]:
                            method_key = f"{component_def.get('name', 'UnknownClass')}.{method_def.get('name', 'UnknownMethod')}"
                            components_to_generate.append({
                                **method_def,
                                "name": method_key,
                                "original_name": method_def.get("name"),
                                "class_context": component_def
                            })

            logs.append(f"Found {len(components_to_generate)} components for detail generation.")

            if components_to_generate:
                for comp_def_for_detail in components_to_generate:
                    comp_name_key = comp_def_for_detail.get("name")
                    logs.append(f"Generating details for component: {comp_name_key}")
                    detail_code = await self._generate_detail_for_component(
                        component_definition=comp_def_for_detail,
                        full_outline=parsed_outline, # Pass the successfully generated outline
                        llm_config=llm_config
                    )
                    if detail_code:
                        component_details[comp_name_key] = detail_code
                        logs.append(f"Successfully generated details for {comp_name_key}.")
                        any_detail_succeeded = True
                    else: # pragma: no cover
                        component_details[comp_name_key] = None
                        logs.append(f"Failed to generate details for {comp_name_key}.")
                        all_details_succeeded = False
            else:
                logs.append("No components listed in outline for detail generation. Proceeding to assembly if main block exists.")
                all_details_succeeded = True # No details failed as none were attempted
                any_detail_succeeded = True # Considered successful if no components needed detailing

            # Determine status after detail generation
            detail_gen_status = "ERROR_DETAIL_GENERATION_FAILED"
            # current_error might still hold an error from outline phase; don't overwrite unless detail phase also fails
            if not current_error: current_error = None # Clear for detail-specific errors

            if all_details_succeeded and any_detail_succeeded:
                detail_gen_status = "SUCCESS_HIERARCHICAL_DETAILS_GENERATED"
            elif any_detail_succeeded: # pragma: no cover
                detail_gen_status = "PARTIAL_HIERARCHICAL_DETAILS_GENERATED"
                if not current_error: current_error = "Some component details failed generation."
            else: # No detail succeeded AND there were components to generate
                if components_to_generate: # only error if components were expected
                    if not current_error: current_error = "All component details failed generation."
                else: # No components to generate, and none succeeded (which is fine)
                    detail_gen_status = "SUCCESS_HIERARCHICAL_DETAILS_GENERATED"


            logs.append(f"Detail generation phase status: {detail_gen_status}")

            # If detail generation completely failed and there was no prior outline error, set status
            if detail_gen_status == "ERROR_DETAIL_GENERATION_FAILED" and not current_error :
                 current_error = "Detail generation failed for all components."


            # Step 3: Assemble Components
            self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Assembling code components")
            assembled_code: Optional[str] = None
            assembly_status = "ERROR_ASSEMBLY_FAILED"

            # Only proceed to assembly if the outline was good and we have either some details or no components were needed
            if parsed_outline and (detail_gen_status != "ERROR_DETAIL_GENERATION_FAILED" or not components_to_generate) :
                try:
                    assembled_code = self._assemble_components(parsed_outline, component_details)
                    logs.append(f"Assembly attempt complete. Assembled code length: {len(assembled_code or '')}")

                    if not assembled_code and (parsed_outline.get("components") or parsed_outline.get("main_execution_block")):
                        assembly_status = "ERROR_ASSEMBLY_EMPTY_CODE"
                        if not current_error: current_error = "Assembly resulted in empty code despite having an outline."
                        logs.append(current_error)
                    elif assembled_code:
                        if detail_gen_status == "SUCCESS_HIERARCHICAL_DETAILS_GENERATED":
                            assembly_status = "SUCCESS_HIERARCHICAL_ASSEMBLED"
                        elif detail_gen_status == "PARTIAL_HIERARCHICAL_DETAILS_GENERATED":
                            assembly_status = "PARTIAL_HIERARCHICAL_ASSEMBLED"
                        else: # ERROR_DETAIL_GENERATION_FAILED but some placeholders might be assembled
                            assembly_status = "SUCCESS_HIERARCHICAL_ASSEMBLED_PLACEHOLDERS"
                    else: # No components, no main block, empty outline essentially.
                        assembly_status = "SUCCESS_HIERARCHICAL_ASSEMBLED" # Assembled "nothing" successfully
                        logs.append("Assembly resulted in empty code as outline was effectively empty.")

                except Exception as e_assemble: # pragma: no cover
                    logger.error(f"Error during code assembly: {e_assemble}", exc_info=True)
                    logs.append(f"Exception during assembly: {e_assemble}")
                    if not current_error: current_error = f"Assembly failed: {e_assemble}"
                    assembly_status = "ERROR_ASSEMBLY_FAILED"
            else:
                logs.append("Skipping assembly due to failures in outline or detail generation.")
                assembly_status = detail_gen_status # Carry over the error status from detail/outline
                if not current_error: current_error = "Assembly skipped due to prior errors."

            logs.append(f"Assembly phase status: {assembly_status}")

            # Step 4: Save to file (if assembly was attempted and produced code)
            saved_to_path_val: Optional[str] = None
            final_status = assembly_status # This will be updated if saving fails

            if assembled_code: # Lint and Save only if code was assembled
                self._update_task(task_id, ActiveTaskStatus.GENERATING_CODE, step_desc="Running linter on assembled code")
                lint_messages, lint_run_error = await self._run_linter(assembled_code)
                if lint_run_error:
                    logs.append(f"Linter execution error for assembled code: {lint_run_error}")
                    # Optionally, append to current_error or set final_status, but for now, just log.
                if lint_messages:
                    logs.append("Linting issues found in assembled code:")
                    logs.extend(lint_messages)

                if target_path:
                    self._update_task(task_id, ActiveTaskStatus.APPLYING_CHANGES, step_desc=f"Saving assembled code to {target_path}")
                    logs.append(f"Attempting to save assembled code to {target_path}")
                    if write_to_file(target_path, assembled_code):
                        saved_to_path_val = target_path
                        logs.append(f"Successfully saved assembled code to {target_path}")
                    else: # pragma: no cover
                        final_status = "ERROR_SAVING_ASSEMBLED_CODE"
                        if not current_error: current_error = f"Failed to save assembled code to {target_path}."
                        logs.append(current_error)
                        logger.error(current_error)
            elif not assembled_code and target_path and assembly_status not in ["ERROR_ASSEMBLY_FAILED", "ERROR_ASSEMBLY_EMPTY_CODE"] and not current_error:
                # This case means assembly was "successful" but produced no code (e.g. empty outline)
                # and no prior errors occurred. We shouldn't try to save.
                logs.append("No assembled code to save (outline might have been empty or only placeholders).")
            elif not assembled_code and target_path : # Assembly failed or resulted in empty code with errors
                 logs.append(f"No assembled code to save due to status: {assembly_status}. Error: {current_error}")


            result = {
                "status": final_status,
                "parsed_outline": parsed_outline, # From step 1
                "component_details": component_details, # From step 2
                "code_string": assembled_code, # From step 3
                "metadata": None, # Hierarchical gen doesn't produce this type of metadata directly
                "saved_to_path": saved_to_path_val, # From step 4
                "logs": logs,
                "error": current_error
            }

            # Final task status update
            if "SUCCESS_HIERARCHICAL_ASSEMBLED" in final_status: # Catches ASSEMBLED and ASSEMBLED_PLACEHOLDERS
                 self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Hierarchical generation, assembly, and saving (if path provided) complete.")
            else:
                 self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=current_error, step_desc=final_status) # Or more specific failure status
            return result

        else: # pragma: no cover
            result = {
                "status": "ERROR_UNSUPPORTED_CONTEXT", "code_string": None, "metadata": None,
                "logs": [f"generate_code for context '{context}' not supported yet."],
                "error": "Unsupported context for generate_code."
            }
            self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
            return result

        # Fallback for any path that might not have explicitly returned and updated task status
        # This should ideally not be reached if all context handlers manage their own terminal status updates.
        # If result is not yet defined (e.g. very early error before context handling):
        # if 'result' not in locals():
        #    result = {"status": "ERROR_UNDEFINED_FAILURE", "error": "Process ended prematurely."} # pragma: no cover
        # self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=result.get("error", "Undefined error"), step_desc=result.get("status"))
        # return result

    # --- Hierarchical Generation Methods ---

    async def _generate_hierarchical_outline(
        self,
        high_level_description: str,
        llm_config: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generates a structural outline for code based on a high-level description.
        Returns a dictionary containing status, parsed_outline, outline_str, logs, and error.
        """
        logs = [f"Initiating hierarchical outline generation for: {high_level_description[:70]}..."]

        if not self.llm_provider: # pragma: no cover
            logger.error("LLM provider not configured for CodeService.")
            return {"status": "ERROR_LLM_PROVIDER_MISSING", "parsed_outline": None, "outline_str": None, "logs": logs, "error": "LLM provider missing."}

        # Default LLM parameters for outline generation, potentially overridden by llm_config
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
        final_status = "SUCCESS_OUTLINE_GENERATED" # Optimistic default

        try:
            cleaned_json_str = raw_llm_output.strip()
            if cleaned_json_str.startswith("```json"): # pragma: no cover
                cleaned_json_str = cleaned_json_str[len("```json"):].strip()
            if cleaned_json_str.endswith("```"): # pragma: no cover
                cleaned_json_str = cleaned_json_str[:-len("```")].strip()

            # Handle common escapes that might be present in LLM JSON output
            cleaned_json_str = cleaned_json_str.replace('\\n', '\n').replace('\\"', '\"')

            parsed_outline = json.loads(cleaned_json_str)
            logs.append("Successfully parsed JSON outline from LLM response.")
        except json.JSONDecodeError as e: # pragma: no cover
            logger.warning(f"Failed to parse JSON outline: {e}. Raw output snippet: {raw_llm_output[:200]}...")
            logs.append(f"JSONDecodeError parsing outline: {e}. Raw output snippet: {raw_llm_output[:100]}")
            error_message = f"Failed to parse LLM JSON outline: {e}"
            final_status = "ERROR_OUTLINE_PARSING"

        return {"status": final_status, "parsed_outline": parsed_outline, "outline_str": raw_llm_output, "logs": logs, "error": error_message}

    # --- End Hierarchical Generation Methods ---

    async def modify_code(
        self, context: str, modification_instruction: str,
        existing_code: Optional[str] = None, language: str = "python",
        module_path: Optional[str] = None, function_name: Optional[str] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        additional_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        task_id: Optional[str] = None
        result: Dict[str, Any] = {}

        if self.task_manager:
            task_desc = f"Modify_code: {context}, Target: {module_path}.{function_name}"
            related_id = f"{module_path}.{function_name}" if module_path and function_name else "unknown_target"
            task = self.task_manager.add_task(
                task_type=ActiveTaskType.AGENT_TOOL_MODIFICATION, # Assuming modify_code is for agent tools
                description=task_desc,
                related_item_id=related_id
            )
            task_id = task.task_id

        try:
            logger.info(f"CodeService.modify_code called for context='{context}', module='{module_path}', function='{function_name}'. Task ID: {task_id}")
            logs = [f"modify_code invoked with context='{context}', module='{module_path}', function='{function_name}'. Task ID: {task_id}"]

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

            # LLM Configuration
            current_llm_config = llm_config if llm_config else {}
            code_gen_model = current_llm_config.get("model_name", get_model_for_task("code_modification"))
            temperature = current_llm_config.get("temperature", 0.2)
            max_tokens = current_llm_config.get("max_tokens", 2048)

            prompt = "" # Initialize prompt variable
            if context == "SELF_FIX_TOOL":
                if not module_path or not function_name:
                    logs.append("Missing module_path or function_name for SELF_FIX_TOOL.")
                    result = {"status": "ERROR_MISSING_DETAILS", "modified_code_string": None, "logs": logs, "error": "Missing details for self-fix."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result
                if actual_existing_code is None:
                     logs.append("Original code is missing for SELF_FIX_TOOL.")
                     result = {"status": "ERROR_NO_ORIGINAL_CODE", "modified_code_string": None, "logs": logs, "error": "Original code missing."}
                     self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                     return result

                prompt = LLM_CODE_FIX_PROMPT_TEMPLATE.format(
                    module_path=module_path, function_name=function_name,
                    problem_description=modification_instruction, original_code=actual_existing_code
                )
                logs.append(f"Using SELF_FIX_TOOL. Target: {module_path}.{function_name}")

            elif context == "GRANULAR_CODE_REFACTOR":
                if not module_path or not function_name:
                    logs.append("Missing module_path or function_name for GRANULAR_CODE_REFACTOR.")
                    result = {"status": "ERROR_MISSING_DETAILS", "modified_code_string": None, "logs": logs, "error": "Missing module_path or function_name for prompt."}
                    self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                    return result
                if actual_existing_code is None:
                     logs.append("Original code is missing for GRANULAR_CODE_REFACTOR.")
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

            else:
                logs.append(f"Context '{context}' not supported for modify_code.")
                result = {"status": "ERROR_UNSUPPORTED_CONTEXT", "modified_code_string": None, "logs": logs, "error": "Unsupported context"}
                self._update_task(task_id, ActiveTaskStatus.FAILED_PRE_REVIEW, reason=result.get("error"), step_desc=result.get("status"))
                return result

            if not self.llm_provider:
                logger.error(f"LLM provider not configured for modify_code. Task ID: {task_id}")
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
                "status": "SUCCESS_CODE_GENERATED",
                "modified_code_string": cleaned_llm_code,
                "logs": logs,
                "error": None
            }
            self._update_task(task_id, ActiveTaskStatus.COMPLETED_SUCCESSFULLY, reason="Code modification generated.", step_desc=result.get("status"))
            return result

        except Exception as e: # Catch-all for unexpected errors during the process
            logger.error(f"Unexpected error in modify_code: {e}. Task ID: {task_id}", exc_info=True) # pragma: no cover
            result = {"status": "ERROR_MODIFY_CODE_UNEXPECTED", "modified_code_string": None, "logs": logs, "error": str(e)} # pragma: no cover
            self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=str(e), step_desc="Unexpected error") # pragma: no cover
            return result # pragma: no cover

    async def _run_linter(self, code_string: str) -> Tuple[List[str], Optional[str]]:
        if actual_existing_code is None and (context == "SELF_FIX_TOOL" or context == "GRANULAR_CODE_REFACTOR"):
            if not module_path or not function_name: # Required for fetching
                logs.append(f"Missing module_path or function_name for {context} when existing_code is not provided.")
                return {"status": "ERROR_MISSING_DETAILS", "modified_code_string": None, "logs": logs, "error": "Missing module_path or function_name."}

            logger.info(f"No existing_code provided for {module_path}.{function_name}, attempting to fetch.")
            logs.append(f"Fetching original code for {module_path}.{function_name}.")
            if not self.self_modification_service: # pragma: no cover
                logger.error("Self modification service not configured for modify_code.")
                logs.append("Self modification service not configured.")
                return {"status": "ERROR_SELF_MOD_SERVICE_MISSING", "modified_code_string": None, "logs": logs, "error": "Self modification service not configured."}
            actual_existing_code = self.self_modification_service.get_function_source_code(module_path, function_name)

            if actual_existing_code is None: # Check again after fetch attempt
                logger.warning(f"Could not retrieve original code for {module_path}.{function_name}.")
                logs.append(f"Failed to retrieve original source for {module_path}.{function_name}.")
                return {"status": "ERROR_NO_ORIGINAL_CODE", "modified_code_string": None, "logs": logs, "error": "Cannot get original code."}

        # No specific logic for modify_code after LLM call for now, just return the result
        # The 'finally' block in the try/except structure will handle terminal status update
        pass # Placeholder for any post-LLM processing if needed in future.
        except Exception as e: # Catch-all for unexpected errors during the process
            logger.error(f"Unexpected error in modify_code: {e}. Task ID: {task_id}", exc_info=True) # pragma: no cover
            result = {"status": "ERROR_MODIFY_CODE_UNEXPECTED", "modified_code_string": None,
                      "logs": logs if 'logs' in locals() else [], # Ensure logs is defined
                      "error": str(e)} # pragma: no cover
            self._update_task(task_id, ActiveTaskStatus.FAILED_UNKNOWN, reason=str(e), step_desc="Unexpected error in modify_code") # pragma: no cover
            return result # pragma: no cover
        # finally: # This finally block would always run, even on early returns. We need to ensure 'result' is defined.
            # The current structure handles returns explicitly, so a single finally might be too broad.
            # if task_id and self.task_manager:
            #    final_status = ActiveTaskStatus.COMPLETED_SUCCESSFULLY if result.get("status") == "SUCCESS_CODE_GENERATED" else ActiveTaskStatus.FAILED_UNKNOWN
            #    self._update_task(task_id, final_status, reason=result.get("error"), step_desc=result.get("status"))


    async def _run_linter(self, code_string: str) -> Tuple[List[str], Optional[str]]:
        lint_messages: List[str] = []
        error_string: Optional[str] = None

        if not code_string.strip():
            return [], None

        # Attempt 1: Ruff JSON
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
                    return lint_messages, None # Ruff JSON success
                except json.JSONDecodeError as je:
                    # Ruff ran, but output wasn't valid JSON. Fall through to Ruff Text.
                    error_string = f"Ruff JSON output parsing error: {je}. Stdout: {stdout_str[:200]}"
                    logger.warning(error_string)


            elif process.returncode != 0 and stdout_str.strip() : # Ruff found issues, output is JSON
                 try:
                    ruff_issues = json.loads(stdout_str)
                    for issue in ruff_issues:
                        msg = (
                            f"LINT (Ruff): {issue.get('code')} at "
                            f"{issue.get('location',{}).get('row',0)}:{issue.get('location',{}).get('column',0)}: "
                            f"{issue.get('message','')} ({issue.get('filename', '<stdin>')})"
                        )
                        lint_messages.append(msg)
                    # Even with issues, linter ran successfully.
                    return lint_messages, None
                 except json.JSONDecodeError as je:
                    error_string = f"Ruff JSON output parsing error (with issues): {je}. Stdout: {stdout_str[:200]}"
                    logger.warning(error_string) # Fall through

            # If Ruff JSON output was empty or some other error, it will fall through.
            # If stderr has content, it might be a Ruff crash.
            if stderr_str and not stdout_str: # Likely a ruff crash
                error_string = f"Ruff execution error (JSON mode): {stderr_str}"
                logger.warning(error_string) # Log and then try Ruff text mode.

        except FileNotFoundError:
            logger.info("Ruff not found (JSON attempt). Trying Ruff text.")
            error_string = "Ruff not found." # Will be updated if pyflakes also not found
        except Exception as e_ruff_json: # pragma: no cover
            error_string = f"Unexpected error running Ruff (JSON): {e_ruff_json}"
            logger.error(error_string, exc_info=True)


        # Attempt 2: Ruff Text (if JSON failed or wasn't conclusive)
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

            if stdout_str.strip(): # Ruff text output means lint issues found
                for line in stdout_str.strip().splitlines():
                    lint_messages.append(f"LINT (Ruff Text): {line}")
                return lint_messages, None # Ruff Text success (even with issues)
            elif process.returncode == 0: # No output, no error code = clean by ruff text
                return [], None

            if stderr_str: # Fallback error if Ruff text also failed
                # Overwrite previous error_string if it was just "Ruff not found" from JSON attempt
                if not error_string or error_string == "Ruff not found.":
                     error_string = f"Ruff execution error (Text mode): {stderr_str}"
                logger.warning(f"Ruff execution error (Text mode): {stderr_str}")


        except FileNotFoundError:
            logger.info("Ruff not found (Text attempt). Trying Pyflakes.")
            # If error_string is already "Ruff not found.", this confirms it for both modes.
            # If it's something else (e.g. JSON parse error), we keep that more specific error for now.
            if not error_string : error_string = "Ruff not found."
        except Exception as e_ruff_text: # pragma: no cover
            new_error = f"Unexpected error running Ruff (Text): {e_ruff_text}"
            if not error_string or error_string == "Ruff not found.": error_string = new_error
            logger.error(new_error, exc_info=True)


        # Attempt 3: Pyflakes (if Ruff failed)
        try:
            process = await asyncio.create_subprocess_exec(
                'pyflakes', '-', # Pyflakes reads from stdin with '-'
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate(input=code_string.encode('utf-8'))
            stdout_str = stdout.decode('utf-8', errors='replace')
            stderr_str = stderr.decode('utf-8', errors='replace')

            if stdout_str.strip(): # Pyflakes outputs issues to stdout
                for line in stdout_str.strip().splitlines():
                    lint_messages.append(f"LINT (Pyflakes): {line}")
                return lint_messages, None # Pyflakes success (even with issues)
            elif process.returncode == 0: # No stdout, no error code = clean by pyflakes
                return [], None

            if stderr_str: # Pyflakes critical error (e.g. invalid syntax it can't parse)
                # This is a pyflakes execution error, not just lint issues.
                error_string = f"Pyflakes execution error: {stderr_str}"
                logger.warning(error_string)

        except FileNotFoundError:
            logger.info("Pyflakes not found.")
            if error_string == "Ruff not found.": # Both are missing
                error_string = "Linter check failed: Ruff and Pyflakes not found."
            elif not error_string : # Ruff had a different error, Pyflakes not found
                error_string = "Pyflakes not found; Ruff also failed."
            # If error_string had a more specific Ruff error, keep it.
        except Exception as e_pyflakes: # pragma: no cover
            new_error = f"Unexpected error running Pyflakes: {e_pyflakes}"
            if not error_string or "not found" in error_string: error_string = new_error
            logger.error(new_error, exc_info=True)

        return lint_messages, error_string # Return any collected messages and the final error string

    async def _generate_detail_for_component(
        self,
        component_definition: Dict[str, Any], # e.g., a function/method dict from the outline
        full_outline: Dict[str, Any], # The complete outline for context
        # context: str, # Original context like "HIERARCHICAL_GEN_MODULE", if needed for LLM params
        llm_config: Optional[Dict[str, Any]] # Specific LLM config for this component
    ) -> Optional[str]: # Returns the generated code string for the component
        """
        Step 2 of Hierarchical Generation: Generate code for a single component.
        Uses LLM_COMPONENT_DETAIL_PROMPT_TEMPLATE.
        """
        component_type = component_definition.get('type', 'unknown_type')
        component_name = component_definition.get('name', 'UnnamedComponent')
        component_signature = component_definition.get('signature', '')
        component_description = component_definition.get('description', '')
        component_body_placeholder = component_definition.get('body_placeholder', '')

        module_imports_list = full_outline.get('imports', [])
        module_imports_str = "\n".join([f"import {imp}" for imp in module_imports_list]) if module_imports_list else "# No specific module-level imports listed in outline."

        # Construct overall_context_summary (can be improved)
        # For now, just use the overall module/class description if available.
        # A more advanced version would serialize relevant parts of full_outline.
        overall_context_summary = full_outline.get('description', 'No overall description provided in outline.')
        if component_type == "method" and full_outline.get("components"): # Try to find class context
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

        # LLM parameters
        # Use defaults, but allow override from llm_config if provided at higher level or per component
        model_to_use = get_model_for_task("code_generation") # Or "code_detail_generation"
        temperature = 0.2 # More deterministic for filling in details
        max_tokens_to_use = 1024 # Adjust as needed for typical component size

        if llm_config: # pragma: no cover
            model_to_use = llm_config.get("model_name", model_to_use)
            temperature = llm_config.get("temperature", temperature)
            max_tokens_to_use = llm_config.get("max_tokens", max_tokens_to_use)

        raw_llm_output = await self.llm_provider.invoke_ollama_model_async(
            prompt, model_name=model_to_use, temperature=temperature, max_tokens=max_tokens_to_use
        )

        if not raw_llm_output or \
           "# IMPLEMENTATION_ERROR:" in raw_llm_output or \
           len(raw_llm_output.strip()) < 5: # Arbitrary check for minimal viable code
            logger.warning(f"LLM did not provide a usable code snippet for component '{component_name}'. Output: {raw_llm_output}")
            return None

        # Basic cleaning: remove potential markdown backticks
        cleaned_code_snippet = raw_llm_output.strip()
        if cleaned_code_snippet.startswith("```python"): # pragma: no cover
            cleaned_code_snippet = cleaned_code_snippet[len("```python"):].strip()
        if cleaned_code_snippet.endswith("```"): # pragma: no cover
            cleaned_code_snippet = cleaned_code_snippet[:-len("```")].strip()

        # Replace escaped newlines
        cleaned_code_snippet = cleaned_code_snippet.replace("\\n", "\n")

        logger.info(f"Successfully generated code snippet for component '{component_name}'. Length: {len(cleaned_code_snippet)}")
        return cleaned_code_snippet

    def _assemble_components(
        self,
        outline: Dict[str, Any],
        component_details: Dict[str, Optional[str]] # maps component name/id to its generated code string
    ) -> str:
        """
        Step 3 of Hierarchical Generation: Assemble the final code from an outline
        and generated details for each component.
        """
        logger.info("CodeService: Assembling components into final code string.")

        if not outline: # pragma: no cover
            logger.warning("_assemble_components called with no outline.")
            return "# Error: Outline was not provided for assembly."

        code_parts = []

        # 1. Add module-level docstring if present in outline
        module_docstring = outline.get("module_docstring") # Assuming this key might exist
        if module_docstring: # pragma: no branch
            code_parts.append(f'"""{module_docstring}"""')
            code_parts.append("\n\n") # Ensures two blank lines after module docstring

        # 2. Add imports
        imports = outline.get("imports", [])
        if imports: # pragma: no branch
            if code_parts and not code_parts[-1].endswith("\n\n"): # If module docstring was there and ended with \n\n
                if code_parts[-1] == "\n": code_parts[-1] = "\n\n" # Adjust if only one \n
                else: code_parts.append("\n\n") # Should not happen if docstring added \n\n
            elif not code_parts: # No module docstring
                 pass # Imports will be first, no preceding newlines needed from here

            for imp in imports:
                code_parts.append(f"import {imp}")
            code_parts.append("\n\n") # Two blank lines after all imports

        # Remove potentially redundant starting newlines if nothing was added before components
        if not code_parts:
            pass
        elif "".join(code_parts).isspace(): # If only newlines were added
            code_parts = [] # Reset if only whitespace/newlines, let components add their own needed space

        # 3. Add components (functions and classes)
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
                    # Add placeholder if code for this function is missing
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
                code_parts.append("\n\n") # Ensure two blank lines after a function

            elif component_type == "class":
                class_name = component_name
                # Basic class definition line (bases, keywords not handled in this simple outline version)
                code_parts.append(f"class {class_name}:")

                class_docstring = component_def.get("description") # Class description as docstring
                if class_docstring: # pragma: no branch
                    # Indent docstring
                    indented_docstring = f'    """{class_docstring}"""'
                    code_parts.append(indented_docstring)
                    code_parts.append("") # Blank line after class docstring if present

                # Class attributes (as comments or type hints if possible, simplified here)
                attributes = component_def.get("attributes", [])
                if attributes: # pragma: no branch
                    # For now, just list them as comments within the class, or as type hints if __init__ not detailed
                    # A more robust way would be to ensure __init__ handles them or use dataclass/attrs.
                    # This simple version will just put them as comments for now if no __init__ method handles them.
                    # If an __init__ method is generated by LLM, it should handle attribute initialization.
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
                if not methods and not attributes and not class_docstring : # If class is empty
                     code_parts.append("    pass") # Add pass to empty class

                for method_def in methods:
                    method_name = method_def.get("name")
                    method_key = f"{class_name}.{method_name}"
                    method_code = component_details.get(method_key)

                    if method_code:
                        # Indent the whole method code block
                        indented_method_code = "\n".join([f"    {line}" for line in method_code.splitlines()])
                        code_parts.append(indented_method_code)
                    else: # pragma: no cover
                        # Add placeholder if code for this method is missing
                        signature = method_def.get("signature", "(self)")
                        desc = method_def.get("description", "No description.")
                        placeholder_body = method_def.get("body_placeholder", "pass # TODO: Implement")
                        code_parts.append(f"    # Method '{method_name}' was planned but not generated.")
                        code_parts.append(f"    def {method_name}{signature}:")
                    docstring_lines = [ # Start with 8 spaces for method docstring
                        f"        \"\"\"Placeholder for: {desc.splitlines()[0] if desc else ''}"]
                    if desc and '\n' in desc: # pragma: no cover
                        for line in desc.splitlines()[1:]:
                            docstring_lines.append(f"        {line.strip()}")
                    docstring_lines.append(f"        Original placeholder: {placeholder_body.splitlines()[0] if placeholder_body else ''}")
                    if placeholder_body and '\n' in placeholder_body: # pragma: no cover
                        for line in placeholder_body.splitlines()[1:]:
                            docstring_lines.append(f"        {line.strip()}")
                    docstring_lines.append("        \"\"\"") # End of docstring
                    code_parts.extend(docstring_lines)
                    code_parts.append(f"        pass") # Pass statement for the method
                    # Add a single newline after each method, _assemble_components will handle overall spacing
                    code_parts.append("")

                # Add two newlines after the class block
                if code_parts and code_parts[-1] == "": # If last part was a blank line from a method
                    code_parts[-1] = "\n\n" # Make it two newlines
                else: # Class might be empty or end without a blank line
                    code_parts.append("\n\n")


            else: # pragma: no cover
                logger.warning(f"Unsupported component type '{component_type}' in outline for assembly.")
                code_parts.append(f"# UNSUPPORTED COMPONENT TYPE: {component_type} - {component_name}")

        # 4. Add main execution block if present
        main_block = outline.get("main_execution_block")
        if main_block: # pragma: no branch
            code_parts.append("") # Ensure a blank line before it
            code_parts.append(main_block)
            code_parts.append("")

        # Join all parts with double newlines between top-level blocks for PEP8,
        # but single newlines were mostly added already.
        # A simple join and then cleanup might be easier.
        final_code = "\n".join(code_parts)

        # Basic cleanup for excessive newlines (e.g., >2 consecutive newlines)
        final_code = re.sub(r"\n{3,}", "\n\n", final_code)

        logger.info(f"Code assembly complete. Total length: {len(final_code)}")
        return final_code.strip()

    # --- End Hierarchical Generation Methods ---

# The _generate_hierarchical_outline method which was previously here has been moved up before modify_code.

if __name__ == '__main__': # pragma: no cover
    import os
    import asyncio
    import tempfile # For __main__ test outputs
    import shutil   # For __main__ test outputs

    # Mock providers for the illustrative test main()
    class MockLLMProvider: # Simplified for brevity, real one in tests is more complex
        async def invoke_ollama_model_async(self, prompt: str, model_name: str, temperature: float, max_tokens: int) -> str:
            logger.info(f"MockLLMProvider.invoke_ollama_model_async for model: {model_name}, prompt starts with: {prompt[:60].replace(chr(10), ' ')}...")

            # Check for NEW_TOOL prompt
            if LLM_NEW_TOOL_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                 logger.info("MockLLMProvider: Matched NEW_TOOL prompt.")
                 return '# METADATA: {"suggested_function_name": "mock_sum_function", "suggested_tool_name": "mockSumTool", "suggested_description": "A mock sum function."}\ndef mock_sum_function(a: int, b: int) -> int:\n    """Adds two integers."""\n    return a + b'

            # Check for SELF_FIX_TOOL prompt
            elif LLM_CODE_FIX_PROMPT_TEMPLATE.splitlines()[1] in prompt: # Using a unique line from the template
                 logger.info("MockLLMProvider: Matched SELF_FIX_TOOL prompt.")
                 return "def function_to_be_fixed_by_main_svc(val: int) -> int:\n    return val + 10 # Fixed!\n"

            # Check for HIERARCHICAL_OUTLINE prompt
            elif LLM_HIERARCHICAL_OUTLINE_PROMPT_TEMPLATE.splitlines()[1] in prompt:
                logger.info("MockLLMProvider: Matched HIERARCHICAL_OUTLINE prompt.")
                description_in_prompt = re.search(r"High-Level Requirement:\n(.*?)\n\nJSON Outline:", prompt, re.DOTALL)
                desc_text = description_in_prompt.group(1).strip() if description_in_prompt else ""

                base_outline = {
                    "module_name": "test_module.py",
                    "description": "A test module.",
                    "imports": ["json", "os"],
                    "components": [],
                    "main_execution_block": "if __name__ == '__main__':\n    print('Test module ready!')"
                }

                if "generate bad code for lint test hierarchical" in desc_text:
                    logger.info("MockLLMProvider: Outline for HIERARCHICAL BAD LINT TEST.")
                    base_outline["components"].append({
                        "type": "function", "name": "buggy_function_bad_lint", "signature": "() -> None",
                        "description": "A function designed to have lint errors.", "body_placeholder": "Implement with syntax errors."
                    })
                elif "generate good code for lint test hierarchical" in desc_text:
                    logger.info("MockLLMProvider: Outline for HIERARCHICAL GOOD LINT TEST.")
                    base_outline["components"].append({
                        "type": "function", "name": "good_function_good_lint", "signature": "() -> str",
                        "description": "A function designed to be lint-free.", "body_placeholder": "Implement correctly."
                    })
                elif "generate code for linter failure test hierarchical" in desc_text:
                    logger.info("MockLLMProvider: Outline for HIERARCHICAL LINTER FAILURE TEST.")
                    base_outline["components"].append({
                        "type": "function", "name": "function_linter_fail", "signature": "() -> None",
                        "description": "A function that might represent code triggering linter issues.", "body_placeholder": "Implement."
                    })
                else: # Default todo_cli outline for other hierarchical tests
                    logger.info("MockLLMProvider: Using default todo_cli.py outline.")
                    return json.dumps({
                        "module_name": "todo_cli.py",
                        "description": "A CLI tool for managing a to-do list.",
                        "imports": ["json", "argparse"],
                        "components": [
                            {
                                "type": "function", "name": "load_todos", "signature": "(filepath: str) -> list",
                                "description": "Loads to-dos from a JSON file.", "body_placeholder": "Read JSON file, handle errors."
                            },
                            {
                                "type": "function", "name": "save_todos", "signature": "(filepath: str, todos: list) -> None",
                                "description": "Saves to-dos to a JSON file.", "body_placeholder": "Write JSON file, handle errors."
                            },
                            {
                                "type": "class", "name": "TodoManager", "description": "Manages todo operations.",
                                "attributes": [{"name": "filepath", "type": "str", "description": "Path to the todo file"}],
                                "methods": [
                                    {
                                        "type": "method", "name": "__init__", "signature": "(self, filepath: str)",
                                        "description": "Initializes TodoManager.", "body_placeholder": "self.filepath = filepath"
                                    },
                                    {
                                        "type": "method", "name": "add_item", "signature": "(self, item_text: str)",
                                        "description": "Adds an item using the manager.", "body_placeholder": "Load, add, save."
                                    }
                                ]
                            }
                        ],
                        "main_execution_block": "if __name__ == '__main__':\n    # main_cli_logic()\n    print('CLI Tool Ready!')"
                    })
                return json.dumps(base_outline)

            # Check for COMPONENT_DETAIL prompt
            elif LLM_COMPONENT_DETAIL_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                logger.info("MockLLMProvider: Matched COMPONENT_DETAIL prompt.")
                # Specific component mocks for linting tests
                if "buggy_function_bad_lint" in prompt:
                    logger.info("MockLLMProvider: Detail for buggy_function_bad_lint.")
                    return "BAD_CODE_EXAMPLE_FOR_LINT_TEST" # This exact string is caught by mock_run_linter
                elif "good_function_good_lint" in prompt:
                    logger.info("MockLLMProvider: Detail for good_function_good_lint.")
                    return "GOOD_CODE_EXAMPLE_FOR_LINT_TEST" # Caught by mock_run_linter
                elif "function_linter_fail" in prompt:
                    logger.info("MockLLMProvider: Detail for function_linter_fail.")
                    return "LINTER_FAILURE_EXAMPLE_FOR_LINT_TEST" # Caught by mock_run_linter

                # Existing component mocks for todo_cli
                elif "load_todos" in prompt:
                    return "def load_todos(filepath: str) -> list:\n    \"\"\"Loads to-dos from a JSON file.\"\"\"\n    try:\n        with open(filepath, 'r') as f:\n            return json.load(f)\n    except FileNotFoundError:\n        return []"
                elif "save_todos" in prompt:
                    return "def save_todos(filepath: str, todos: list) -> None:\n    \"\"\"Saves to-dos to a JSON file.\"\"\"\n    with open(filepath, 'w') as f:\n        json.dump(todos, f, indent=2)"
                elif "TodoManager.__init__" in prompt:
                    return "def __init__(self, filepath: str):\n    \"\"\"Initializes TodoManager.\"\"\"\n    self.filepath = filepath"
                elif "TodoManager.add_item" in prompt:
                     return "def add_item(self, item_text: str):\n    \"\"\"Adds an item using the manager.\"\"\"\n    # todos = self.load_todos(self.filepath)\n    # todos.append({'task': item_text, 'done': False})\n    # self.save_todos(self.filepath, todos)\n    print(f\"Added: {item_text} to {{self.filepath}}\") # Simplified for mock"

                logger.warning(f"MockLLMProvider: No specific mock for COMPONENT_DETAIL prompt part: {prompt[:100]}...") # pragma: no cover
                return f"# Code for component based on prompt: {prompt[:100]}...\npass # Default mock implementation"

            # Check for GRANULAR_REFACTOR prompt
            elif LLM_GRANULAR_REFACTOR_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                logger.info("MockLLMProvider: Matched GRANULAR_REFACTOR prompt.")
                # This mock will be simple and assume the 'original_code' is passed in the prompt,
                # and the 'refactor_instruction' is to add a comment.
                # A real LLM would do more complex logic.
                # We need to extract original_code from the prompt to return it modified.
                original_code_match = re.search(r"Original Function Code:\n```python\n(.*?)\n```", prompt, re.DOTALL)
                if original_code_match:
                    original_code_content = original_code_match.group(1)
                    # Simulate adding a comment based on refactor_instruction
                    refactor_instruction_match = re.search(r"Refactoring Instruction:\n(.*?)\n\nConstraints:", prompt, re.DOTALL)
                    instruction = refactor_instruction_match.group(1).strip() if refactor_instruction_match else "No instruction found"

                    # A simple modification: add instruction as a comment
                    modified_code = f"# Refactored based on: {instruction}\n{original_code_content}"
                    return modified_code
                return "// REFACTORING_SUGGESTION_IMPOSSIBLE (Mock could not parse original code)" # pragma: no cover

            elif LLM_UNIT_TEST_SCAFFOLD_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                logger.info("MockLLMProvider: Matched UNIT_TEST_SCAFFOLD prompt.")
                return "import unittest\n\nclass TestGeneratedCode(unittest.TestCase):\n    def test_something(self):\n        self.fail('Not implemented')"

            # Specific prompts for lint testing (primarily for NEW_TOOL context now)
            if "generate bad code for lint test" in prompt and LLM_NEW_TOOL_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                logger.info("MockLLMProvider: Matched 'generate bad code for lint test' FOR NEW_TOOL.")
                # For NEW_TOOL, it expects metadata + code
                return '# METADATA: {"suggested_function_name": "new_tool_bad_lint", "suggested_tool_name": "newToolBadLint", "suggested_description": "Tool with bad lint."}\nBAD_CODE_EXAMPLE_FOR_LINT_TEST'
            if "generate good code for lint test" in prompt and LLM_NEW_TOOL_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                logger.info("MockLLMProvider: Matched 'generate good code for lint test' FOR NEW_TOOL.")
                return '# METADATA: {"suggested_function_name": "new_tool_good_lint", "suggested_tool_name": "newToolGoodLint", "suggested_description": "Tool with good lint."}\nGOOD_CODE_EXAMPLE_FOR_LINT_TEST'
            if "generate code for linter failure test" in prompt and LLM_NEW_TOOL_PROMPT_TEMPLATE.splitlines()[0] in prompt:
                logger.info("MockLLMProvider: Matched 'generate code for linter failure test' FOR NEW_TOOL.")
                return '# METADATA: {"suggested_function_name": "new_tool_linter_fail", "suggested_tool_name": "newToolLinterFail", "suggested_description": "Tool for linter fail."}\nLINTER_FAILURE_EXAMPLE_FOR_LINT_TEST'

            logger.warning(f"MockLLMProvider: No specific mock for prompt starting with: {prompt[:60]}...")
            return "// NO_CODE_SUGGESTION_POSSIBLE (MockLLMProvider Fallback)"

    class MockSelfModService: # Unchanged from previous version, seems fine
        def get_function_source_code(self, module_path, function_name):
            logger.info(f"MockSelfModService.get_function_source_code called for {module_path}.{function_name}")
            if module_path == "ai_assistant.custom_tools.dummy_tool_main_test_svc" and \
               function_name == "function_to_be_fixed_by_main_svc":
                return "def function_to_be_fixed_by_main_svc(val: int) -> int:\n    return val * 10 # Should be val + 10\n"
            return None

    async def main_illustrative_test():
        # Instantiate with mock objects that have the required methods
        mock_llm_provider_instance = MockLLMProvider() # Uses the updated MockLLMProvider
        mock_self_mod_service_instance = MockSelfModService()

        code_service = CodeService(
            llm_provider=mock_llm_provider_instance,
            self_modification_service=mock_self_mod_service_instance
        )

        original_run_linter = code_service._run_linter # Save original

        async def mock_run_linter(code_string: str) -> Tuple[List[str], Optional[str]]:
            logger.info(f"mock_run_linter called with code_string: '{code_string[:30]}...'")
            if code_string == "GOOD_CODE_EXAMPLE_FOR_LINT_TEST":
                return ([], None)
            elif code_string == "BAD_CODE_EXAMPLE_FOR_LINT_TEST":
                return (["LINT (Mocked): E999 SyntaxError at 1:1: Example syntax error"], None)
            elif code_string == "LINTER_FAILURE_EXAMPLE_FOR_LINT_TEST":
                return ([], "Mocked linter execution error: Linters not found.")
            return (["LINT (Mocked): Unexpected code for lint test"], None) # Default mock response

        # Setup test output directory for __main__
        test_output_dir = tempfile.mkdtemp(prefix="codeservice_main_test_")
        print(f"Test outputs will be saved in: {test_output_dir}")

        # --- Test NEW_TOOL (including Linter) ---
        print("\n--- Testing: generate_code (NEW_TOOL) with Linter ---")
        code_service._run_linter = mock_run_linter # Monkeypatch

        # Test 1: Bad code for linting
        new_tool_bad_lint_path = os.path.join(test_output_dir, "new_tool_bad_lint.py")
        gen_result_bad_lint = await code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="generate bad code for lint test", # MockLLMProvider returns "BAD_CODE_EXAMPLE_FOR_LINT_TEST"
            target_path=new_tool_bad_lint_path
        )
        print(f"  Bad Lint - Status: {gen_result_bad_lint.get('status')}, Saved: {gen_result_bad_lint.get('saved_to_path')}")
        assert any("LINT (Mocked): E999 SyntaxError" in log for log in gen_result_bad_lint.get("logs", [])), "Bad lint message not found in logs"
        print("    Verified: Bad lint message in logs.")

        # Test 2: Good code for linting
        new_tool_good_lint_path = os.path.join(test_output_dir, "new_tool_good_lint.py")
        gen_result_good_lint = await code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="generate good code for lint test", # MockLLMProvider returns "GOOD_CODE_EXAMPLE_FOR_LINT_TEST"
            target_path=new_tool_good_lint_path
        )
        print(f"  Good Lint - Status: {gen_result_good_lint.get('status')}, Saved: {gen_result_good_lint.get('saved_to_path')}")
        assert not any("LINT (Mocked):" in log for log in gen_result_good_lint.get("logs", [])), "Good lint test should have no lint messages"
        print("    Verified: No lint messages for good code.")

        # Test 3: Linter execution failure
        new_tool_linter_fail_path = os.path.join(test_output_dir, "new_tool_linter_fail.py")
        gen_result_linter_fail = await code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="generate code for linter failure test", # MockLLMProvider returns "LINTER_FAILURE_EXAMPLE_FOR_LINT_TEST"
            target_path=new_tool_linter_fail_path
        )
        print(f"  Linter Fail - Status: {gen_result_linter_fail.get('status')}, Saved: {gen_result_linter_fail.get('saved_to_path')}")
        assert any("Linter execution error: Mocked linter execution error" in log for log in gen_result_linter_fail.get("logs", [])), "Linter execution error not found in logs"
        print("    Verified: Linter execution error in logs.")

        code_service._run_linter = original_run_linter # Restore original

        # --- Test SELF_FIX_TOOL (Standard, no linter here by design) ---
        print("\n--- Testing: modify_code (SELF_FIX_TOOL) ---")
        mod_result_fix = await code_service.modify_code(
            context="SELF_FIX_TOOL",
            modification_instruction="Fix multiplication to addition.",
            module_path="dummy_module.py", function_name="function_to_be_fixed_by_main_svc"
        )
        print(f"  Status: {mod_result_fix.get('status')}")
        if mod_result_fix.get('modified_code_string'): print(f"  Code (first 50): {mod_result_fix['modified_code_string'][:50].replace(chr(10), ' ')}...")
        if mod_result_fix.get('error'): print(f"  Error: {mod_result_fix['error']}")

        # --- Test GENERATE_UNIT_TEST_SCAFFOLD ---
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

        # --- Test EXPERIMENTAL_HIERARCHICAL_OUTLINE ---
        print("\n--- Testing: generate_code (EXPERIMENTAL_HIERARCHICAL_OUTLINE) ---")
        outline_desc = "CLI tool for to-do list in JSON." # This description will be used by the mock
        outline_result = await code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_OUTLINE",
            prompt_or_description=outline_desc
        )
        print(f"  Status: {outline_result.get('status')}")
        if outline_result.get("parsed_outline"): print(f"  Outline Module: {outline_result['parsed_outline'].get('module_name')}, Components: {len(outline_result['parsed_outline'].get('components', []))}")
        if outline_result.get("error"): print(f"  Error: {outline_result.get('error')}")

        # --- Test EXPERIMENTAL_HIERARCHICAL_FULL_TOOL (Outline + Details, No Assembly) ---
        print("\n--- Testing: generate_code (EXPERIMENTAL_HIERARCHICAL_FULL_TOOL) ---")
        full_tool_desc = "Advanced CLI tool for to-do list with features."
        full_tool_result = await code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL",
            prompt_or_description=full_tool_desc # This will also use the mock outline due to template matching
        )
        print(f"  Status: {full_tool_result.get('status')}")
        if full_tool_result.get("parsed_outline"): print(f"  Outline Module: {full_tool_result['parsed_outline'].get('module_name')}")
        if full_tool_result.get("component_details"):
            print(f"  Component Details: {len(full_tool_result['component_details'])} generated/attempted.")
            # Example: Check one specific component based on the mock outline
            expected_comp_key = "TodoManager.add_item"
            if expected_comp_key in full_tool_result["component_details"]:
                 print(f"    Detail for '{expected_comp_key}': {'Generated' if full_tool_result['component_details'][expected_comp_key] else 'Failed/None'}")
        if full_tool_result.get("error"): print(f"  Error: {full_tool_result.get('error')}")

        # --- Test HIERARCHICAL_GEN_COMPLETE_TOOL (including Linter) ---
        print("\n--- Testing: generate_code (HIERARCHICAL_GEN_COMPLETE_TOOL) with Linter ---")
        code_service._run_linter = mock_run_linter # Monkeypatch

        # Test 1: Bad code for linting from assembly
        hier_bad_lint_path = os.path.join(test_output_dir, "hier_bad_lint.py")
        # MockLLMProvider's HIERARCHICAL_OUTLINE + COMPONENT_DETAIL will result in "BAD_CODE_EXAMPLE_FOR_LINT_TEST"
        # if _assemble_components simply joins them. We'll adjust the component detail mock for this.
        # For this test, let's assume the assembly of "load_todos" (good) and a new "buggy_function" (bad)
        # will result in a string that our mock_run_linter will interpret as "BAD_CODE_EXAMPLE_FOR_LINT_TEST".
        # This requires MockLLMProvider to yield "BAD_CODE_EXAMPLE_FOR_LINT_TEST" when asked for *assembled* code.
        # The current mock structure for hierarchical generation is complex. Let's simplify:
        # We will make the "HIERARCHICAL_GEN_COMPLETE_TOOL" prompt itself trigger a specific assembled code.

        complete_tool_result_bad_lint = await code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="generate bad code for lint test hierarchical", # Updated prompt
            target_path=hier_bad_lint_path
        )
        print(f"  Bad Lint - Status: {complete_tool_result_bad_lint.get('status')}, Saved: {complete_tool_result_bad_lint.get('saved_to_path')}")
        assert any("LINT (Mocked): E999 SyntaxError" in log for log in complete_tool_result_bad_lint.get("logs", [])), "Bad lint message not found in hierarchical logs"
        if complete_tool_result_bad_lint.get("code_string"):
             assert "BAD_CODE_EXAMPLE_FOR_LINT_TEST" in complete_tool_result_bad_lint.get("code_string", ""), "Bad lint code not found in assembled output"
        print("    Verified: Bad lint message and code in hierarchical logs.")

        # Test 2: Good code for linting from assembly
        hier_good_lint_path = os.path.join(test_output_dir, "hier_good_lint.py")
        complete_tool_result_good_lint = await code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="generate good code for lint test hierarchical", # Updated prompt
            target_path=hier_good_lint_path
        )
        print(f"  Good Lint - Status: {complete_tool_result_good_lint.get('status')}, Saved: {complete_tool_result_good_lint.get('saved_to_path')}")
        assert not any("LINT (Mocked):" in log for log in complete_tool_result_good_lint.get("logs", [])), "Good lint hierarchical test should have no lint messages"
        if complete_tool_result_good_lint.get("code_string"):
            assert "GOOD_CODE_EXAMPLE_FOR_LINT_TEST" in complete_tool_result_good_lint.get("code_string", ""), "Good lint code not found in assembled output"
        print("    Verified: No lint messages and good code in hierarchical logs.")

        # Test 3: Linter execution failure for assembled code
        hier_linter_fail_path = os.path.join(test_output_dir, "hier_linter_fail.py")
        complete_tool_result_linter_fail = await code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description="generate code for linter failure test hierarchical", # Updated prompt
            target_path=hier_linter_fail_path
        )
        print(f"  Linter Fail - Status: {complete_tool_result_linter_fail.get('status')}, Saved: {complete_tool_result_linter_fail.get('saved_to_path')}")
        assert any("Linter execution error for assembled code: Mocked linter execution error" in log for log in complete_tool_result_linter_fail.get("logs", [])), "Hierarchical linter execution error not found"
        if complete_tool_result_linter_fail.get("code_string"):
            assert "LINTER_FAILURE_EXAMPLE_FOR_LINT_TEST" in complete_tool_result_linter_fail.get("code_string", ""), "Linter fail code not found in assembled output"
        print("    Verified: Hierarchical linter execution error and relevant code in logs.")

        code_service._run_linter = original_run_linter # Restore

        # --- Test GRANULAR_CODE_REFACTOR (Standard, no linter here by design) ---
        print("\n--- Testing: modify_code (GRANULAR_CODE_REFACTOR) ---")
        granular_original_code = "def process_data(data_list):\n    for item in data_list:\n        print(item)\n    return len(data_list)"
        granular_section_id = "for item in data_list:\n        print(item)"
        granular_instruction = "Add a comment '# Processing item' inside the loop before the print statement."

        granular_refactor_result = await code_service.modify_code(
            context="GRANULAR_CODE_REFACTOR",
            modification_instruction=granular_instruction,
            existing_code=granular_original_code, # Provide existing code directly
            module_path="test_module.py", # Still needed for prompt
            function_name="process_data",   # Still needed for prompt
            additional_context={"section_identifier": granular_section_id}
        )
        print(f"  Status: {granular_refactor_result.get('status')}")
        if granular_refactor_result.get('modified_code_string'):
            print(f"  Modified Code:\n{granular_refactor_result['modified_code_string']}")
        if granular_refactor_result.get('error'): print(f"  Error: {granular_refactor_result['error']}")


        # Cleanup
        try:
            print(f"\nIllustrative test finished. Cleaning up test output directory: {test_output_dir}")
            shutil.rmtree(test_output_dir)
            print(f"Successfully removed {test_output_dir}")
        except Exception as e_cleanup: # pragma: no cover
            print(f"Error cleaning up test directory: {e_cleanup}")


    if os.name == 'nt': # pragma: no cover
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main_illustrative_test())
