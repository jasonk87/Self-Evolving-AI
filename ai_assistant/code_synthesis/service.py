# ai_assistant/code_synthesis/service.py
from .data_structures import CodeTaskRequest, CodeTaskResult, CodeTaskType, CodeTaskStatus
from typing import Dict, Any, Optional, Tuple, List
import re
import os
import json
import sys
import asyncio
import logging

from ai_assistant.core import self_modification
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async
from ai_assistant.config import get_model_for_task

# --- Prompt Templates ---

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

LLM_NEW_TOOL_PROMPT_TEMPLATE_SYNTHESIS = """Based on the following high-level description of a desired tool, your task is to generate a single Python function and associated metadata.

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

logger = logging.getLogger(__name__)

class CodeSynthesisService:
    """
    Main service class for the Unified Code Writing System (UCWS).
    Acts as an entry point for all code synthesis tasks.
    """

    def __init__(self):
        """
        Initializes the CodeSynthesisService.
        """
        logger.info("CodeSynthesisService initialized.")

    async def _run_linter(self, code_string: str) -> Tuple[List[str], Optional[str]]:
        """
        Runs 'ruff' linter on the provided code string.
        Returns a tuple: (list_of_lint_messages, error_string_if_execution_failed).
        """
        lint_messages: List[str] = []
        error_string: Optional[str] = None

        if not code_string or not code_string.strip():
            return [], None

        try:
            # Try JSON output first for parsing
            process = await asyncio.create_subprocess_exec(
                'ruff', 'check', '--output-format=json', '--stdin-filename', '<stdin>', '-',
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate(input=code_string.encode('utf-8'))

            stdout_str = stdout.decode('utf-8', errors='replace')
            stderr_str = stderr.decode('utf-8', errors='replace')

            # Ruff usually returns 1 if violations found, 0 if success.
            # However, if we get JSON, we can parse it.
            if stdout_str.strip():
                try:
                    ruff_issues = json.loads(stdout_str)
                    if isinstance(ruff_issues, list):
                        for issue in ruff_issues:
                            msg = (
                                f"LINT: {issue.get('code')} at "
                                f"{issue.get('location',{}).get('row',0)}:{issue.get('location',{}).get('column',0)}: "
                                f"{issue.get('message','')}."
                            )
                            lint_messages.append(msg)
                except json.JSONDecodeError:
                    # Fallback if not JSON or mixed output
                    if "syntax error" in stdout_str.lower():
                         lint_messages.append(f"LINT RAW: {stdout_str.strip()}")

            if stderr_str:
                 # Check if it's a critical error or just info
                 if "error" in stderr_str.lower():
                     error_string = f"Ruff execution stderr: {stderr_str}"

        except FileNotFoundError:
            error_string = "Ruff linter not found in environment."
        except Exception as e:
            error_string = f"Exception running linter: {e}"

        return lint_messages, error_string

    async def submit_task(self, request: CodeTaskRequest) -> CodeTaskResult:
        """
        Primary method to request code synthesis.
        Dispatches to specific handlers based on request.task_type.
        """
        print(f"CodeSynthesisService: Received task {request.request_id} of type {request.task_type.name}")

        if request.task_type == CodeTaskType.NEW_TOOL_CREATION_LLM:
            return await self._handle_new_tool_creation_llm(request)
        elif request.task_type == CodeTaskType.EXISTING_TOOL_SELF_FIX_LLM:
            return await self._handle_existing_tool_self_fix_llm(request)
        elif request.task_type == CodeTaskType.EXISTING_TOOL_SELF_FIX_AST:
            return await self._handle_existing_tool_self_fix_ast(request)
        elif request.task_type == CodeTaskType.HIERARCHICAL_GENERATION_OUTLINE:
            return await self._handle_hierarchical_outline(request)
        elif request.task_type == CodeTaskType.HIERARCHICAL_GENERATION_FULL:
            return await self._handle_hierarchical_full(request)
        else:
            print(f"Warning: Unsupported task type: {request.task_type}") # pragma: no cover
            return CodeTaskResult(
                request_id=request.request_id,
                status=CodeTaskStatus.FAILURE_UNSUPPORTED_TASK,
                error_message=f"Task type {request.task_type.name} is not supported."
            )

    async def _handle_new_tool_creation_llm(self, request: CodeTaskRequest) -> CodeTaskResult:
        """Handles new tool creation using LLM, with added error handling for LLM calls."""
        print(f"CodeSynthesisService: Handling NEW_TOOL_CREATION_LLM for request {request.request_id}")
        
        tool_description = request.context_data.get("description")
        if not tool_description:
            return CodeTaskResult(
                request_id=request.request_id,
                status=CodeTaskStatus.FAILURE_PRECONDITION,
                error_message="Missing 'description' in context_data for new tool creation."
            )

        prompt = LLM_NEW_TOOL_PROMPT_TEMPLATE_SYNTHESIS.format(description=tool_description)

        llm_config = request.llm_config_overrides or {}
        model_name = llm_config.get("model_name", get_model_for_task("code_generation"))
        temperature = llm_config.get("temperature", 0.3)
        max_tokens = llm_config.get("max_tokens", 2048) # Increased for potentially larger tools

        print(f"CodeSynthesisService: Sending new tool prompt to LLM (model: {model_name})...")

        max_retries = 3
        current_prompt = prompt
        attempt_log = []

        for attempt in range(max_retries):
            print(f"CodeSynthesisService: Generation Attempt {attempt + 1}/{max_retries}")
            try:
                llm_response = await invoke_ollama_model_async(
                    current_prompt, model_name=model_name, temperature=temperature, max_tokens=max_tokens
                )
            except Exception as e:
                error_msg = f"LLM invocation failed for new tool generation: {e}"
                print(f"CodeSynthesisService: {error_msg}")
                return CodeTaskResult(
                    request_id=request.request_id,
                    status=CodeTaskStatus.FAILURE_LLM_GENERATION,
                    error_message=error_msg,
                    metadata={"llm_model_used": model_name, "attempt_log": attempt_log}
                )

            if not llm_response or not llm_response.strip():
                attempt_log.append(f"Attempt {attempt+1}: Empty response.")
                continue

            parsed_metadata: Optional[Dict[str, str]] = None
            actual_code_str: str = ""

            # Attempt to separate metadata and code
            if llm_response.startswith("# METADATA:"):
                try:
                    lines = llm_response.split('\n', 1)
                    metadata_line = lines[0]
                    metadata_json_str_match = re.search(r"{\s*.*?\s*}", metadata_line)
                    if metadata_json_str_match:
                        metadata_json_str = metadata_json_str_match.group(0)
                        parsed_metadata = json.loads(metadata_json_str)
                        actual_code_str = lines[1] if len(lines) > 1 else ""
                    else:
                        actual_code_str = llm_response # Assume no valid metadata line
                except Exception as e:
                    print(f"CodeSynthesisService: Error parsing metadata for new tool: {e}. Treating response as code only.")
                    actual_code_str = llm_response.lstrip("# METADATA:") if llm_response.startswith("# METADATA:") else llm_response
            else:
                actual_code_str = llm_response

            cleaned_llm_code = actual_code_str.strip()
            if cleaned_llm_code.startswith("```python"):
                cleaned_llm_code = cleaned_llm_code[len("```python"):].strip()
            if cleaned_llm_code.endswith("```"):
                cleaned_llm_code = cleaned_llm_code[:-len("```")].strip()

            if not cleaned_llm_code:
                attempt_log.append(f"Attempt {attempt+1}: No code found.")
                continue

            # Run Linter
            lint_messages, lint_error = await self._run_linter(cleaned_llm_code)

            if lint_error:
                print(f"CodeSynthesisService: Linter execution failed: {lint_error}. Accepting code with warning.")
                attempt_log.append(f"Attempt {attempt+1}: Linter execution error ({lint_error}). Code accepted.")
                # If linter itself fails, we might still accept the code or fallback.
                # Let's accept it but log the issue.

                response_metadata_log = {
                    "llm_model_used": model_name,
                    "attempt_log": attempt_log,
                    "parsed_tool_metadata": parsed_metadata,
                    "generated_code_length": len(cleaned_llm_code)
                }
                return CodeTaskResult(
                    request_id=request.request_id,
                    status=CodeTaskStatus.SUCCESS,
                    generated_code=cleaned_llm_code,
                    metadata=response_metadata_log
                )

            if lint_messages:
                print(f"CodeSynthesisService: Linting issues found on attempt {attempt+1}: {lint_messages[:2]}")
                attempt_log.append(f"Attempt {attempt+1}: Lint errors: {lint_messages}")

                # Feedback loop
                error_feedback = "\n".join(lint_messages)
                correction_instruction = (
                    f"\nThe generated code had the following linting/syntax errors:\n{error_feedback}\n"
                    f"Code causing errors:\n```python\n{cleaned_llm_code}\n```\n"
                    "Please regenerate the code fixing these errors. Ensure valid Python syntax. "
                    "Provide the complete corrected code with the metadata line."
                )
                current_prompt = f"{prompt}\n\n{correction_instruction}"
                continue # Retry
            else:
                # Success!
                print(f"CodeSynthesisService: Code passed linting on attempt {attempt+1}.")
                response_metadata_log = {
                    "llm_model_used": model_name,
                    "attempt_log": attempt_log,
                    "parsed_tool_metadata": parsed_metadata,
                    "generated_code_length": len(cleaned_llm_code)
                }
                return CodeTaskResult(
                    request_id=request.request_id,
                    status=CodeTaskStatus.SUCCESS,
                    generated_code=cleaned_llm_code,
                    metadata=response_metadata_log
                )

        # Max retries reached
        error_msg = f"Failed to generate valid code after {max_retries} attempts. Last lint errors: {attempt_log[-1] if attempt_log else 'Unknown'}"
        print(f"CodeSynthesisService: {error_msg}")
        return CodeTaskResult(
            request_id=request.request_id,
            status=CodeTaskStatus.FAILURE_MAX_RETRIES_REACHED,
            error_message=error_msg,
            metadata={"llm_model_used": model_name, "attempt_log": attempt_log}
        )

    async def _handle_existing_tool_self_fix_llm(self, request: CodeTaskRequest) -> CodeTaskResult:
        """Handles fixing existing tools using LLM (full function replacement)."""
        context = request.context_data
        module_path = context.get("module_path")
        function_name = context.get("function_name")
        problem_description = context.get("problem_description")
        original_code_from_context = context.get("original_code")

        if not all([module_path, function_name, problem_description]):
            # Log carefully as module_path or function_name might be None
            mp_log = str(module_path) if module_path is not None else "None"
            fn_log = str(function_name) if function_name is not None else "None"
            print(f"CodeSynthesisService: Precondition failed for EXISTING_TOOL_SELF_FIX_LLM. Module: {mp_log}, Function: {fn_log}. Missing one or more required fields.")
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_PRECONDITION,
                                  error_message="Missing module_path, function_name, or problem_description.")

        # Now module_path, function_name, and problem_description are guaranteed to be truthy.
        # Pylance should be happier with the assertions below.
        print(f"CodeSynthesisService: Handling EXISTING_TOOL_SELF_FIX_LLM for {module_path}.{function_name}.")

        assert isinstance(module_path, str), "module_path must be a string after validation"
        assert isinstance(function_name, str), "function_name must be a string after validation"

        if original_code_from_context:
            original_code = original_code_from_context
        else:
            original_code = self_modification.get_function_source_code(module_path, function_name)
        if not original_code:
            error_msg = f"Could not retrieve original code for {module_path}.{function_name}."
            print(f"CodeSynthesisService: {error_msg}")
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_PRECONDITION,
                                  error_message=error_msg)

        prompt = LLM_CODE_FIX_PROMPT_TEMPLATE.format(
            module_path=module_path, function_name=function_name,
            problem_description=problem_description, original_code=original_code
        )

        llm_config = request.llm_config_overrides or {}
        model_name = llm_config.get("model_name", get_model_for_task("code_generation"))
        temperature = llm_config.get("temperature", 0.3)
        max_tokens = llm_config.get("max_tokens", 1024)

        print(f"CodeSynthesisService: Sending code fix prompt to LLM (model: {model_name})...")

        max_retries = 3
        current_prompt = prompt
        attempt_log = []

        for attempt in range(max_retries):
            print(f"CodeSynthesisService: Fix Attempt {attempt + 1}/{max_retries}")

            llm_response = await invoke_ollama_model_async(current_prompt, model_name=model_name, temperature=temperature, max_tokens=max_tokens)

            if not llm_response or "// NO_CODE_SUGGESTION_POSSIBLE" in llm_response or len(llm_response.strip()) < 10:
                msg = f"Attempt {attempt+1}: No usable suggestion."
                attempt_log.append(msg)
                continue

            cleaned_llm_code = llm_response.strip()
            if cleaned_llm_code.startswith("```python"):
                cleaned_llm_code = cleaned_llm_code[len("```python"):].strip()
            if cleaned_llm_code.endswith("```"):
                cleaned_llm_code = cleaned_llm_code[:-len("```")].strip()

            # Run Linter
            lint_messages, lint_error = await self._run_linter(cleaned_llm_code)

            if lint_error:
                # Linter failed, proceed cautiously or fallback
                attempt_log.append(f"Attempt {attempt+1}: Linter execution error ({lint_error}). Accepting code.")
                response_metadata = {
                    "llm_model_used": model_name,
                    "attempt_log": attempt_log,
                    "llm_generated_code_length": len(cleaned_llm_code)
                }
                return CodeTaskResult(
                    request_id=request.request_id,
                    status=CodeTaskStatus.SUCCESS,
                    generated_code=cleaned_llm_code,
                    metadata=response_metadata
                )

            if lint_messages:
                attempt_log.append(f"Attempt {attempt+1}: Lint errors: {lint_messages}")
                # Feedback loop
                error_feedback = "\n".join(lint_messages)
                correction_instruction = (
                    f"\nThe corrected code you provided has the following linting/syntax errors:\n{error_feedback}\n"
                    f"Code causing errors:\n```python\n{cleaned_llm_code}\n```\n"
                    "Please regenerate the code fixing these errors. Only output the raw Python code."
                )
                current_prompt = f"{prompt}\n\n{correction_instruction}"
                continue
            else:
                # Success
                print(f"CodeSynthesisService: Fix passed linting on attempt {attempt+1}.")
                response_metadata = {
                    "llm_model_used": model_name,
                    "attempt_log": attempt_log,
                    "llm_generated_code_length": len(cleaned_llm_code)
                }
                return CodeTaskResult(
                    request_id=request.request_id,
                    status=CodeTaskStatus.SUCCESS,
                    generated_code=cleaned_llm_code,
                    metadata=response_metadata
                )

        error_msg = f"Failed to generate valid fix after {max_retries} attempts."
        return CodeTaskResult(
            request_id=request.request_id,
            status=CodeTaskStatus.FAILURE_MAX_RETRIES_REACHED,
            error_message=error_msg,
            metadata={"attempt_log": attempt_log}
        )

    async def _handle_existing_tool_self_fix_ast(self, request: CodeTaskRequest) -> CodeTaskResult:
        """Handles fixing existing tools by applying a provided code string using AST."""
        print(f"CodeSynthesisService: Handling EXISTING_TOOL_SELF_FIX_AST for request {request.request_id}")
        
        context = request.context_data
        module_path = context.get("module_path")
        function_name = context.get("function_name")
        new_code_string = context.get("new_code_string")
        project_root_path_from_context = context.get("project_root_path")

        if not all([module_path, function_name, new_code_string]):
            return CodeTaskResult(
                request_id=request.request_id,
                status=CodeTaskStatus.FAILURE_PRECONDITION,
                error_message="Missing module_path, function_name, or new_code_string for AST fix."
            )

        # At this point, module_path, function_name, and new_code_string are guaranteed to be truthy.
        # Add assertions to satisfy Pylance and ensure they are strings.
        assert isinstance(module_path, str), "module_path must be a string after validation"
        assert isinstance(function_name, str), "function_name must be a string after validation"
        assert isinstance(new_code_string, str), "new_code_string must be a string after validation"

        # Determine project_root_path. If not provided in context, calculate relative to this file.
        # This assumes CodeSynthesisService is located at ai_assistant/code_synthesis/service.py
        project_root_path = project_root_path_from_context or \
                            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

        try:
            modification_result_msg = self_modification.edit_function_source_code(
                module_path=module_path,
                function_name=function_name,
                new_code_string=new_code_string,
                project_root_path=project_root_path
            )
            
            if "success" in modification_result_msg.lower():
                return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.SUCCESS,
                                      modified_code_path=f"{module_path}.{function_name}", metadata={"message": modification_result_msg})
            else:
                return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_CODE_APPLICATION,
                                      error_message=modification_result_msg, metadata={"details": "AST modification reported failure."})
        except Exception as e:
            print(f"CodeSynthesisService: Exception during AST self-fix for {module_path}.{function_name}: {e}")
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_CODE_APPLICATION,
                                  error_message=f"Exception during AST self-fix: {e}", metadata={"traceback": str(e)})

    # --- Hierarchical Generation Methods ---

    async def _handle_hierarchical_outline(self, request: CodeTaskRequest) -> CodeTaskResult:
        """Generates a structured outline for hierarchical code generation."""
        print(f"CodeSynthesisService: Handling HIERARCHICAL_GENERATION_OUTLINE for request {request.request_id}")

        description = request.context_data.get("description")
        if not description:
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_PRECONDITION,
                                  error_message="Missing 'description' for outline generation.")

        llm_config = request.llm_config_overrides or {}
        model_name = llm_config.get("model_name", get_model_for_task("code_outline_generation"))
        temperature = llm_config.get("temperature", 0.3)
        max_tokens = llm_config.get("max_tokens", 2048)

        prompt = LLM_HIERARCHICAL_OUTLINE_PROMPT_TEMPLATE.format(high_level_description=description)

        try:
            llm_response = await invoke_ollama_model_async(prompt, model_name=model_name, temperature=temperature, max_tokens=max_tokens)
        except Exception as e:
            error_msg = f"LLM invocation failed for outline generation: {e}"
            print(f"CodeSynthesisService: {error_msg}")
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_LLM_GENERATION, error_message=error_msg)

        if not llm_response or not llm_response.strip():
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_LLM_GENERATION, error_message="LLM returned empty outline.")

        try:
            cleaned_json_str = llm_response.strip()
            if cleaned_json_str.startswith("```json"):
                cleaned_json_str = cleaned_json_str[len("```json"):].strip()
            if cleaned_json_str.endswith("```"):
                cleaned_json_str = cleaned_json_str[:-len("```")].strip()
            cleaned_json_str = cleaned_json_str.replace('\\n', '\n').replace('\\"', '\"') # Basic cleanup

            parsed_outline = json.loads(cleaned_json_str)
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.SUCCESS,
                                  metadata={"parsed_outline": parsed_outline, "raw_llm_response": llm_response})
        except json.JSONDecodeError as e:
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_LLM_GENERATION,
                                  error_message=f"Failed to parse outline JSON: {e}", metadata={"raw_llm_response": llm_response})

    async def _handle_hierarchical_full(self, request: CodeTaskRequest) -> CodeTaskResult:
        """Executes full hierarchical flow: outline -> details -> assembly."""
        print(f"CodeSynthesisService: Handling HIERARCHICAL_GENERATION_FULL for request {request.request_id}")

        # 1. Generate Outline
        outline_req = CodeTaskRequest(task_type=CodeTaskType.HIERARCHICAL_GENERATION_OUTLINE,
                                      context_data=request.context_data, llm_config_overrides=request.llm_config_overrides)
        outline_result = await self._handle_hierarchical_outline(outline_req)

        if outline_result.status != CodeTaskStatus.SUCCESS or not outline_result.metadata:
            return CodeTaskResult(request_id=request.request_id, status=outline_result.status,
                                  error_message=f"Outline generation failed: {outline_result.error_message}",
                                  metadata=outline_result.metadata)

        parsed_outline = outline_result.metadata.get("parsed_outline")
        if not parsed_outline:
             return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_LLM_GENERATION,
                                  error_message="Outline generation succeeded but no parsed outline found.")

        # 2. Generate Details
        component_details: Dict[str, Optional[str]] = {}
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

        any_detail_success = False
        all_details_success = True
        llm_config = request.llm_config_overrides

        for comp_def in components_to_generate:
            detail_code = await self._generate_detail_for_component(comp_def, parsed_outline, llm_config)
            if detail_code:
                component_details[comp_def.get("name")] = detail_code
                any_detail_success = True
            else:
                component_details[comp_def.get("name")] = None
                all_details_success = False

        if not any_detail_success and components_to_generate:
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_LLM_GENERATION,
                                  error_message="All component detail generations failed.")

        # 3. Assemble
        try:
            assembled_code = self._assemble_components(parsed_outline, component_details)
        except Exception as e:
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_CODE_APPLICATION,
                                  error_message=f"Assembly failed: {e}")

        # 4. Final Linting (Optional but good)
        # We can run linter here, similar to other tasks
        lint_msgs, lint_err = await self._run_linter(assembled_code)

        final_status = CodeTaskStatus.SUCCESS if all_details_success else CodeTaskStatus.PARTIAL_SUCCESS

        return CodeTaskResult(request_id=request.request_id, status=final_status,
                              generated_code=assembled_code,
                              metadata={"parsed_outline": parsed_outline, "component_details_count": len(component_details), "lint_messages": lint_msgs})

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
        if component_type == "method" and component_definition.get('class_context'):
             comp = component_definition['class_context']
             class_attrs = ", ".join([f"{attr.get('name')}: {attr.get('type')}" for attr in comp.get('attributes',[])])
             overall_context_summary = (
                f"Within class '{comp.get('name', 'UnknownClass')}' with attributes ({class_attrs}). "
                f"Overall class description: {comp.get('description', '')}"
             )

        prompt = LLM_COMPONENT_DETAIL_PROMPT_TEMPLATE.format(
            overall_context_summary=overall_context_summary,
            component_type=component_type,
            component_name=component_name,
            component_signature=component_signature,
            component_description=component_description,
            component_body_placeholder=component_body_placeholder,
            module_imports=module_imports_str
        )

        model_name = get_model_for_task("code_generation")
        temperature = 0.2
        max_tokens = 1024
        if llm_config:
            model_name = llm_config.get("model_name", model_name)
            temperature = llm_config.get("temperature", temperature)
            max_tokens = llm_config.get("max_tokens", max_tokens)

        try:
            raw_llm_output = await invoke_ollama_model_async(prompt, model_name=model_name, temperature=temperature, max_tokens=max_tokens)
        except Exception as e:
            print(f"CodeSynthesisService: Error generating detail for {component_name}: {e}")
            return None

        if not raw_llm_output or "# IMPLEMENTATION_ERROR:" in raw_llm_output or len(raw_llm_output.strip()) < 5:
             return None

        cleaned_code_snippet = raw_llm_output.strip()
        if cleaned_code_snippet.startswith("```python"):
            cleaned_code_snippet = cleaned_code_snippet[len("```python"):].strip()
        if cleaned_code_snippet.endswith("```"):
            cleaned_code_snippet = cleaned_code_snippet[:-len("```")].strip()

        return cleaned_code_snippet

    def _assemble_components(
        self,
        outline: Dict[str, Any],
        component_details: Dict[str, Optional[str]]
    ) -> str:
        code_parts = []

        module_docstring = outline.get("module_docstring")
        if module_docstring:
            code_parts.append(f'"""{module_docstring}"""')
            code_parts.append("\n\n")

        imports = outline.get("imports", [])
        if imports:
            for imp in imports:
                code_parts.append(f"import {imp}")
            code_parts.append("\n\n")

        if not code_parts: pass # No header

        components = outline.get("components", [])
        for i, component_def in enumerate(components):
            component_type = component_def.get("type")
            component_name = component_def.get("name")

            if not component_name: continue

            if component_type == "function":
                func_code = component_details.get(component_name)
                if func_code:
                    code_parts.append(func_code)
                else:
                    # Placeholder
                    signature = component_def.get("signature", "()")
                    desc = component_def.get("description", "No description.")
                    code_parts.append(f"# Placeholder for function '{component_name}': {desc}")
                    code_parts.append(f"def {component_name}{signature}:")
                    code_parts.append("    pass")
                code_parts.append("\n\n")

            elif component_type == "class":
                class_name = component_name
                code_parts.append(f"class {class_name}:")

                class_docstring = component_def.get("description")
                if class_docstring:
                    indented_docstring = f'    """{class_docstring}"""'
                    code_parts.append(indented_docstring)
                    code_parts.append("")

                methods = component_def.get("methods", [])
                if not methods and not class_docstring: code_parts.append("    pass")

                for method_def in methods:
                    method_name = method_def.get("name")
                    method_key = f"{class_name}.{method_name}"
                    method_code = component_details.get(method_key)

                    if method_code:
                        indented_method_code = "\n".join([f"    {line}" for line in method_code.splitlines()])
                        code_parts.append(indented_method_code)
                    else:
                        signature = method_def.get("signature", "(self)")
                        code_parts.append(f"    # Placeholder for method '{method_name}'")
                        code_parts.append(f"    def {method_name}{signature}:")
                        code_parts.append("        pass")
                    code_parts.append("")

                if code_parts[-1] == "": code_parts.append("\n") # spacing between classes

        main_block = outline.get("main_execution_block")
        if main_block:
            code_parts.append("")
            code_parts.append(main_block)
            code_parts.append("")

        final_code = "\n".join(code_parts)
        final_code = re.sub(r"\n{3,}", "\n\n", final_code) # Normalize newlines

        return final_code.strip()

if __name__ == '__main__': # pragma: no cover
    # Existing test code...
    pass
