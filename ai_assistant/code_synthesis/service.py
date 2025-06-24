# ai_assistant/code_synthesis/service.py
from .data_structures import CodeTaskRequest, CodeTaskResult, CodeTaskType, CodeTaskStatus
from typing import Dict, Any, Optional
import re
import os
import json
import sys

from ai_assistant.core import self_modification
from ai_assistant.llm_interface.ollama_client import invoke_ollama_model_async # Already imported
from ai_assistant.config import get_model_for_task
# from ai_assistant.core.reflection import global_reflection_log # Not directly used in service for now


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

class CodeSynthesisService:
    """
    Main service class for the Unified Code Writing System (UCWS).
    Acts as an entry point for all code synthesis tasks.
    """

    def __init__(self):
        """
        Initializes the CodeSynthesisService.
        """
        print("CodeSynthesisService initialized.")

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
        try:
            llm_response = await invoke_ollama_model_async(
                prompt, model_name=model_name, temperature=temperature, max_tokens=max_tokens
            )
        except Exception as e:
            error_msg = f"LLM invocation failed for new tool generation: {e}"
            print(f"CodeSynthesisService: {error_msg}")
            return CodeTaskResult(
                request_id=request.request_id,
                status=CodeTaskStatus.FAILURE_LLM_GENERATION,
                error_message=error_msg,
                metadata={"llm_model_used": model_name, "llm_prompt_preview": prompt[:300]+"..."}
            )

        response_metadata_log = {
            "llm_model_used": model_name,
            "llm_prompt_preview": prompt[:300]+"...",
            "llm_response_preview": (llm_response[:200] + "...") if llm_response else "None"
        }

        if not llm_response or not llm_response.strip():
            error_msg = "LLM did not provide a response for new tool generation."
            print(f"CodeSynthesisService: {error_msg}")
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_LLM_GENERATION,
                                  error_message=error_msg, metadata=response_metadata_log)

        parsed_metadata: Optional[Dict[str, str]] = None
        actual_code_str: str = ""

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

        if not cleaned_llm_code or not parsed_metadata:
            error_msg = "LLM response for new tool generation was missing code or parsable metadata."
            print(f"CodeSynthesisService: {error_msg}")
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_LLM_GENERATION,
                                  error_message=error_msg, generated_code=cleaned_llm_code, metadata=response_metadata_log)
        
        response_metadata_log["parsed_tool_metadata"] = parsed_metadata
        response_metadata_log["generated_code_length"] = len(cleaned_llm_code)
        return CodeTaskResult(
            request_id=request.request_id,
            status=CodeTaskStatus.SUCCESS,
            generated_code=cleaned_llm_code,
            metadata=response_metadata_log
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

        llm_response = await invoke_ollama_model_async(prompt, model_name=model_name, temperature=temperature, max_tokens=max_tokens)

        response_metadata = {
            "llm_model_used": model_name,
            "llm_prompt_preview": prompt[:300]+"...",
            "llm_response_preview": (llm_response[:200] + "...") if llm_response else "None"
        }

        if not llm_response or "// NO_CODE_SUGGESTION_POSSIBLE" in llm_response or len(llm_response.strip()) < 10:
            error_msg = f"LLM did not provide a usable code suggestion. Response: {llm_response}"
            print(f"CodeSynthesisService: {error_msg}")
            return CodeTaskResult(request_id=request.request_id, status=CodeTaskStatus.FAILURE_LLM_GENERATION,
                                  error_message=error_msg, metadata=response_metadata)

        cleaned_llm_code = llm_response.strip()
        if cleaned_llm_code.startswith("```python"): # pragma: no cover
            cleaned_llm_code = cleaned_llm_code[len("```python"):].strip()
        if cleaned_llm_code.endswith("```"): # pragma: no cover
            cleaned_llm_code = cleaned_llm_code[:-len("```")].strip()

        print(f"CodeSynthesisService: LLM generated code suggestion for {function_name}.")
        response_metadata["llm_generated_code_length"] = len(cleaned_llm_code)
        return CodeTaskResult(
            request_id=request.request_id,
            status=CodeTaskStatus.SUCCESS,
            generated_code=cleaned_llm_code,
            metadata=response_metadata
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

if __name__ == '__main__': # pragma: no cover
    import asyncio
    import os
    import sys

    async def main_test_service():
        service = CodeSynthesisService()

        # Test NEW_TOOL_CREATION_LLM
        print("\n--- Testing NEW_TOOL_CREATION_LLM ---")
        new_tool_req_data = {"description": "Create a Python tool that calculates the factorial of a non-negative integer."}
        # Mock invoke_ollama_model_async for this specific test
        new_tool_request = CodeTaskRequest(
            task_type=CodeTaskType.NEW_TOOL_CREATION_LLM,
            context_data=new_tool_req_data
        )
        result1 = await service.submit_task(new_tool_request)
        print(f"Result for {new_tool_request.task_type.name} ({new_tool_request.request_id}): {result1.status.name} - {result1.error_message or ''}")

        DUMMY_MODULE_DIR = "ai_assistant_dummy_tools_ucws"
        DUMMY_MODULE_NAME = "dummy_math_tools_ucws"
        DUMMY_FILE_PATH = os.path.join(DUMMY_MODULE_DIR, f"{DUMMY_MODULE_NAME}.py")

        os.makedirs(DUMMY_MODULE_DIR, exist_ok=True)
        if not os.path.exists(os.path.join(DUMMY_MODULE_DIR, "__init__.py")):
            with open(os.path.join(DUMMY_MODULE_DIR, "__init__.py"), "w") as f: f.write("")

        if not os.path.exists(DUMMY_FILE_PATH):
            with open(DUMMY_FILE_PATH, "w") as f:
                f.write("def add(a, b):\n    return a + b # Original simple add\n")

        project_root_for_test = os.path.abspath(".")
        if project_root_for_test not in sys.path:
             sys.path.insert(0, project_root_for_test)


        tool_fix_llm_req_data = {
            "module_path": f"{DUMMY_MODULE_DIR}.{DUMMY_MODULE_NAME}",
            "function_name": "add",
            "problem_description": "The add function should handle string concatenation as well.",
        }
        tool_fix_llm_request = CodeTaskRequest(
            task_type=CodeTaskType.EXISTING_TOOL_SELF_FIX_LLM,
            context_data=tool_fix_llm_req_data
        )

        original_invoke = invoke_ollama_model_async
        async def mock_invoke(*args, **kwargs):
            print(f"Mocked LLM call for {tool_fix_llm_request.task_type.name}. Returning a sample fix.")
            return "def add(a, b):\n    # LLM-generated fix for string concatenation\n    if isinstance(a, str) and isinstance(b, str):\n        return a + b\n    elif isinstance(a, (int,float)) and isinstance(b, (int,float)):\n        return a + b\n    else:\n        return str(a) + str(b) # Fallback for mixed types"

        from ai_assistant.llm_interface import ollama_client as ollama_client_module
        ollama_client_module.invoke_ollama_model_async = mock_invoke

        result2 = await service.submit_task(tool_fix_llm_request)
        print(f"Result for {tool_fix_llm_request.task_type.name} ({tool_fix_llm_request.request_id}): {result2.status.name} - {result2.error_message or ''}")
        if result2.generated_code:
            print(f"Generated code:\n{result2.generated_code}")

        ollama_client_module.invoke_ollama_model_async = original_invoke

        # Clean up dummy files
        try:
            os.remove(DUMMY_FILE_PATH)
            os.remove(os.path.join(DUMMY_MODULE_DIR, "__init__.py"))
            if not os.listdir(DUMMY_MODULE_DIR): os.rmdir(DUMMY_MODULE_DIR)
        except OSError as e:
            print(f"Error during cleanup: {e}")


    if __name__ == "__main__":
        asyncio.run(main_test_service())
