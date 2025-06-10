# ai_assistant/code_services/service.py
import logging
from typing import Dict, Any, Optional
import re
import json # For parsing metadata

# Assuming these imports are relative to the ai_assistant package root
from ..config import get_model_for_task, is_debug_mode
from ..core.fs_utils import write_to_file

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
- (For functions/methods) "body_placeholder": A short comment or note indicating what the implementation should achieve.
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

class CodeService:
    def __init__(self, llm_provider: Optional[Any] = None, self_modification_service: Optional[Any] = None):
        self.llm_provider = llm_provider
        self.self_modification_service = self_modification_service
        logger.info("CodeService initialized.")
        if is_debug_mode():
            print("[DEBUG] CodeService initialized with llm_provider:", llm_provider, "self_modification_service:", self_modification_service)
        if not self.llm_provider: # pragma: no cover
            logger.warning("CodeService initialized without an LLM provider. Code generation capabilities will be limited.")
        if not self.self_modification_service: # pragma: no cover
            logger.warning("CodeService initialized without a self-modification service. File operations will be limited.")

    async def generate_code(
        self,
        context: str,
        prompt_or_description: str, # For "NEW_TOOL", this is tool desc. For "SCAFFOLD", this is the code to test.
        language: str = "python",
        target_path: Optional[str] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        additional_context: Optional[Dict[str, Any]] = None # Use for module_name_hint
    ) -> Dict[str, Any]:
        logger.info(f"CodeService.generate_code called with context='{context}', description='{prompt_or_description[:50]}...'")

        if not self.llm_provider: # Check if provider is configured
            logger.error("LLM provider not configured for CodeService.") # pragma: no cover
            return {"status": "ERROR_LLM_PROVIDER_MISSING", "code_string": None, "metadata": None, "logs": ["LLM provider not configured."], "error": "LLM provider missing."}

        if language != "python": # pragma: no cover
            return {
                "status": "ERROR_UNSUPPORTED_LANGUAGE", "code_string": None, "metadata": None,
                "logs": [f"Language '{language}' not supported for {context} yet."],
                "error": "Unsupported language."
            }

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
            logs = [f"Using NEW_TOOL context. Prompt description: {prompt_or_description[:50]}..."]

            raw_llm_output = await self.llm_provider.invoke_ollama_model_async(
                formatted_prompt, model_name=model_to_use, temperature=temperature, max_tokens=max_tokens_to_use
            )

            if not raw_llm_output or not raw_llm_output.strip():
                logger.warning("LLM returned empty response for NEW_TOOL.")
                logs.append("LLM returned empty response.")
                return {"status": "ERROR_LLM_NO_CODE", "code_string": None, "metadata": None, "logs": logs, "error": "LLM provided no code."}
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
                return {"status": "ERROR_CODE_EMPTY_POST_METADATA" if parsed_metadata else "ERROR_LLM_NO_CODE", "code_string": None, "metadata": parsed_metadata, "logs": logs, "error": "No actual code block found or code was empty."}
            if not parsed_metadata: # pragma: no cover
                logs.append("Metadata not successfully parsed for NEW_TOOL, which is required.")
                return {"status": "ERROR_METADATA_PARSING", "code_string": cleaned_code, "metadata": None, "logs": logs, "error": "Metadata parsing failed for NEW_TOOL."}

            logs.append(f"Cleaned code length for NEW_TOOL: {len(cleaned_code)}")
            logger.info(f"Successfully generated new tool code and metadata for '{parsed_metadata.get('suggested_tool_name', 'UnknownTool')}'.")

            saved_to_path_val: Optional[str] = None
            final_status_new_tool = "SUCCESS_CODE_GENERATED"
            error_new_tool = None

            if target_path and cleaned_code:
                logs.append(f"Attempting to save generated NEW_TOOL code to {target_path}")
                if write_to_file(target_path, cleaned_code):
                    saved_to_path_val = target_path
                    logs.append(f"Successfully saved NEW_TOOL code to {target_path}")
                else: # pragma: no cover
                    final_status_new_tool = "ERROR_SAVING_CODE"
                    error_new_tool = f"Successfully generated code for NEW_TOOL, but failed to save to {target_path}."
                    logs.append(error_new_tool)
                    logger.error(error_new_tool)

            return {
                "status": final_status_new_tool,
                "code_string": cleaned_code,
                "metadata": parsed_metadata,
                "saved_to_path": saved_to_path_val, # New field
                "logs": logs,
                "error": error_new_tool
            }

        elif context == "GENERATE_UNIT_TEST_SCAFFOLD":
            code_to_test = prompt_or_description # Assume the code is passed in prompt_or_description
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
            logs = [f"Using EXPERIMENTAL_HIERARCHICAL_OUTLINE context. Description: {high_level_description[:50]}..."]

            formatted_prompt = LLM_HIERARCHICAL_OUTLINE_PROMPT_TEMPLATE.format(
                high_level_description=high_level_description
            )

            raw_llm_output = await self.llm_provider.invoke_ollama_model_async(
                formatted_prompt, model_name=model_to_use, temperature=temperature, max_tokens=max_tokens_to_use # Uses model_to_use, temp, max_tokens defined earlier in the method
            )

            if not raw_llm_output or not raw_llm_output.strip():
                logger.warning("LLM returned empty response for hierarchical outline generation.")
                logs.append("LLM returned empty response for outline.")
                return {"status": "ERROR_LLM_NO_OUTLINE", "outline_str": raw_llm_output, "parsed_outline": None, "code_string": None, "metadata": None, "logs": logs, "error": "LLM provided no outline."}

            logs.append(f"Raw LLM outline output length: {len(raw_llm_output)}")

            parsed_outline: Optional[Dict[str, Any]] = None
            error_message: Optional[str] = None
            final_status = "SUCCESS_OUTLINE_GENERATED"

            try:
                # LLM might wrap JSON in backticks, try to strip them
                cleaned_json_str = raw_llm_output.strip()
                if cleaned_json_str.startswith("```json"): # pragma: no cover
                    cleaned_json_str = cleaned_json_str[len("```json"):].strip()
                if cleaned_json_str.endswith("```"): # pragma: no cover
                    cleaned_json_str = cleaned_json_str[:-len("```")].strip()

                parsed_outline = json.loads(cleaned_json_str)
                logs.append("Successfully parsed JSON outline from LLM response.")
            except json.JSONDecodeError as e: # pragma: no cover
                logger.warning(f"Failed to parse JSON outline: {e}. Raw output: {raw_llm_output[:200]}...")
                logs.append(f"JSONDecodeError parsing outline: {e}")
                error_message = f"Failed to parse LLM JSON outline: {e}"
                final_status = "ERROR_OUTLINE_PARSING"

            return {"status": final_status, "outline_str": raw_llm_output, "parsed_outline": parsed_outline, "code_string": None, "metadata": None, "logs": logs, "error": error_message}

        elif context == "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL":
            high_level_description = prompt_or_description
            logs = [f"Using EXPERIMENTAL_HIERARCHICAL_FULL_TOOL context. Description: {high_level_description[:50]}..."]

            # Step 1 & 2: Generate Outline and then Component Details
            orchestration_result = await self.generate_code(
                context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL", # Call existing orchestration
                prompt_or_description=high_level_description,
                language=language,
                llm_config=llm_config,
                additional_context=additional_context
            )

            logs.extend(orchestration_result.get("logs", []))
            parsed_outline = orchestration_result.get("parsed_outline")
            component_details = orchestration_result.get("component_details", {}) # Ensure Dict[str, Optional[str]] type

            if orchestration_result["status"] not in ["SUCCESS_HIERARCHICAL_DETAILS_GENERATED", "PARTIAL_HIERARCHICAL_DETAILS_GENERATED"]:
                logs.append("Outline or detail generation failed, cannot proceed to assembly.") # pragma: no cover
                return { # Propagate error from previous stage
                    "status": orchestration_result["status"],
                    "parsed_outline": parsed_outline, "component_details": component_details,
                    "code_string": None, "metadata": None,
                    "logs": logs, "error": orchestration_result.get("error", "Outline or detail generation failed.")
                }

            if not parsed_outline or component_details is None: # Check if None explicitly for component_details
                logs.append("Missing outline or component details after successful generation steps. Cannot assemble.") # pragma: no cover
                return {
                    "status": "ERROR_ASSEMBLY_MISSING_DATA",
                    "parsed_outline": parsed_outline, "component_details": component_details,
                    "code_string": None, "metadata": None,
                    "logs": logs, "error": "Internal error: Missing data for assembly."
                }

            # Step 3: Assemble Components
            logs.append("Attempting to assemble generated components.")
            try:
                assembled_code = self._assemble_components(parsed_outline, component_details)
                logs.append(f"Assembly successful. Assembled code length: {len(assembled_code)}")

                final_status = "SUCCESS_HIERARCHICAL_ASSEMBLED"
                # If some details failed but assembly proceeded with placeholders:
                if orchestration_result["status"] == "PARTIAL_HIERARCHICAL_DETAILS_GENERATED": # pragma: no cover
                    final_status = "PARTIAL_HIERARCHICAL_ASSEMBLED"

                saved_to_path_hierarchical: Optional[str] = None
                current_error_hierarchical = orchestration_result.get("error") # Preserve error from detail gen if any

                if target_path and assembled_code:
                    logs.append(f"Attempting to save assembled hierarchical code to {target_path}")
                    if write_to_file(target_path, assembled_code):
                        saved_to_path_hierarchical = target_path
                        logs.append(f"Successfully saved assembled code to {target_path}")
                    else: # pragma: no cover
                        # If saving fails, it's a more significant error for this context
                        final_status = "ERROR_SAVING_ASSEMBLED_CODE"
                        current_error_hierarchical = f"Successfully assembled code, but failed to save to {target_path}."
                        logs.append(current_error_hierarchical)
                        logger.error(current_error_hierarchical)

                return {
                    "status": final_status, # This was set based on assembly and detail gen status
                    "parsed_outline": parsed_outline,
                    "component_details": component_details,
                    "code_string": assembled_code,
                    "metadata": None,
                    "saved_to_path": saved_to_path_hierarchical, # New field
                    "logs": logs,
                    "error": current_error_hierarchical
                }
            except Exception as e_assemble: # pragma: no cover
                logger.error(f"Error during code assembly: {e_assemble}", exc_info=True)
                logs.append(f"Exception during assembly: {e_assemble}")
                return {
                    "status": "ERROR_ASSEMBLY_FAILED",
                    "parsed_outline": parsed_outline, "component_details": component_details,
                    "code_string": None, "metadata": None,
                    "logs": logs, "error": f"Assembly failed: {e_assemble}"
                }

        else: # pragma: no cover
            return {
                "status": "ERROR_UNSUPPORTED_CONTEXT", "code_string": None, "metadata": None,
                "logs": [f"generate_code for context '{context}' not supported yet."],
                "error": "Unsupported context for generate_code."
            }

    async def modify_code(
        self, context: str, modification_instruction: str,
        existing_code: Optional[str] = None, language: str = "python",
        module_path: Optional[str] = None, function_name: Optional[str] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        additional_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        logger.info(f"CodeService.modify_code called for context='{context}', module='{module_path}', function='{function_name}'")
        logs = [f"modify_code invoked with context='{context}', module='{module_path}', function='{function_name}'."]

        if context != "SELF_FIX_TOOL":
            logs.append(f"Context '{context}' not supported for modify_code.")
            return {"status": "ERROR_UNSUPPORTED_CONTEXT", "modified_code_string": None, "logs": logs, "error": "Unsupported context"}

        if not module_path or not function_name:
            logs.append("Missing module_path or function_name for SELF_FIX_TOOL.")
            return {"status": "ERROR_MISSING_DETAILS", "modified_code_string": None, "logs": logs, "error": "Missing details for self-fix."}

        if language != "python":
            logs.append(f"Language '{language}' not supported for SELF_FIX_TOOL.")
            return {"status": "ERROR_UNSUPPORTED_LANGUAGE", "modified_code_string": None, "logs": logs, "error": "Unsupported language."}

        actual_existing_code = existing_code
        if actual_existing_code is None:
            logger.info(f"No existing_code provided for {module_path}.{function_name}, attempting to fetch.")
            logs.append(f"Fetching original code for {module_path}.{function_name}.")
            if not self.self_modification_service:
                logger.error("Self modification service not configured for modify_code.")
                logs.append("Self modification service not configured.")
                return {"status": "ERROR_SELF_MOD_SERVICE_MISSING", "modified_code_string": None, "logs": logs, "error": "Self modification service not configured."}
            actual_existing_code = self.self_modification_service.get_function_source_code(module_path, function_name)

        if actual_existing_code is None: # Check again after fetch attempt
            logger.warning(f"Could not retrieve original code for {module_path}.{function_name}.")
            logs.append(f"Failed to retrieve original source for {module_path}.{function_name}.")
            return {"status": "ERROR_NO_ORIGINAL_CODE", "modified_code_string": None, "logs": logs, "error": "Cannot get original code."}

        prompt = LLM_CODE_FIX_PROMPT_TEMPLATE.format(
            module_path=module_path, function_name=function_name,
            problem_description=modification_instruction, original_code=actual_existing_code
        )

        current_llm_config = llm_config if llm_config else {}
        code_gen_model = current_llm_config.get("model_name", get_model_for_task("code_generation"))
        temperature = current_llm_config.get("temperature", 0.3)
        max_tokens = current_llm_config.get("max_tokens", 1024)

        logger.info(f"CodeService: Sending code fix prompt to LLM (model: {code_gen_model}).")
        logs.append(f"Sending code fix prompt to LLM (model: {code_gen_model}).")

        if not self.llm_provider:
            logger.error("LLM provider not configured for modify_code.")
            logs.append("LLM provider not configured.")
            return {
                "status": "ERROR_LLM_PROVIDER_MISSING", "modified_code_string": None,
                "logs": logs, "error": "LLM provider not configured."
            }

        llm_response = await self.llm_provider.invoke_ollama_model_async(
            prompt, model_name=code_gen_model, temperature=temperature, max_tokens=max_tokens
        )

        if not llm_response or "// NO_CODE_SUGGESTION_POSSIBLE" in llm_response or len(llm_response.strip()) < 10:
            logger.warning(f"LLM did not provide a usable code suggestion. Response: {llm_response}")
            logs.append(f"LLM failed to provide suggestion. Output: {llm_response[:100] if llm_response else 'None'}")
            return {"status": "ERROR_LLM_NO_SUGGESTION", "modified_code_string": None, "logs": logs, "error": "LLM provided no usable suggestion."}

        cleaned_llm_code = llm_response.strip()
        if cleaned_llm_code.startswith("```python"): # pragma: no cover
            cleaned_llm_code = cleaned_llm_code[len("```python"):].strip()
        if cleaned_llm_code.endswith("```"): # pragma: no cover
            cleaned_llm_code = cleaned_llm_code[:-len("```")].strip()

        logs.append(f"LLM successfully generated code suggestion. Length: {len(cleaned_llm_code)}")
        logger.info(f"LLM generated code suggestion for {function_name}. Length: {len(cleaned_llm_code)}")
        return {
            "status": "SUCCESS_CODE_GENERATED",
            "modified_code_string": cleaned_llm_code,
            "logs": logs,
            "error": None
        }

    # --- Conceptual Placeholders for Hierarchical Generation ---

    async def _generate_hierarchical_outline(
        self,
        high_level_description: str,
        context: str, # e.g., HIERARCHICAL_GEN_MODULE
        llm_config: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]: # Returns the parsed outline structure (e.g., dict from JSON)
        """
        (Conceptual) Step 1 of Hierarchical Generation: Generate the code outline.
        This would use a specific "outline generation" prompt.
        """
        logger.info(f"Conceptual: _generate_hierarchical_outline for '{high_level_description[:50]}...'")
        # 1. Select/format outline generation prompt.
        # 2. Call self.llm_provider.invoke_ollama_model_async(...)
        # 3. Parse the structured outline (e.g., JSON) from LLM response.
        #    Handle parsing errors.
        # Example:
        # if successful_parse:
        #     return parsed_outline_dict
        # else:
        #     return None
        return {"status": "conceptual_outline_placeholder"} # Placeholder

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

    # --- End Conceptual Placeholders ---

if __name__ == '__main__': # pragma: no cover
    import os
    import asyncio
    import tempfile # For __main__ test outputs
    import shutil   # For __main__ test outputs

    # Mock providers for the illustrative test main()
    # These would need to be actual objects with the expected methods for the test to fully run.
    class MockLLMProvider:
        async def invoke_ollama_model_async(self, prompt, model_name, temperature, max_tokens):
            logger.info(f"MockLLMProvider.invoke_ollama_model_async called with model: {model_name}")
            if "generate_code" in prompt: # Simple check for generate_code vs modify_code
                 return '# METADATA: {"suggested_function_name": "mock_sum_function", "suggested_tool_name": "mockSumTool", "suggested_description": "A mock sum function."}\ndef mock_sum_function(a: int, b: int) -> int:\n    """Adds two integers."""\n    return a + b'
            elif "function_to_be_fixed_by_main_svc" in prompt :
                 return "def function_to_be_fixed_by_main_svc(val: int) -> int:\n    return val + 10 # Fixed!\n"
            return "// NO_CODE_SUGGESTION_POSSIBLE"

    class MockSelfModService:
        def get_function_source_code(self, module_path, function_name):
            logger.info(f"MockSelfModService.get_function_source_code called for {module_path}.{function_name}")
            if module_path == "ai_assistant.custom_tools.dummy_tool_main_test_svc" and \
               function_name == "function_to_be_fixed_by_main_svc":
                return "def function_to_be_fixed_by_main_svc(val: int) -> int:\n    return val * 10 # Should be val + 10\n"
            return None

    async def main_illustrative_test():
        # Instantiate with mock objects that have the required methods
        mock_llm_provider_instance = MockLLMProvider()
        mock_self_mod_service_instance = MockSelfModService()

        code_service = CodeService(
            llm_provider=mock_llm_provider_instance,
            self_modification_service=mock_self_mod_service_instance
        )

        # Setup test output directory for __main__
        test_output_dir = tempfile.mkdtemp(prefix="codeservice_test_outputs_")
        print(f"Test outputs will be saved in: {test_output_dir}")


        print("\n--- Testing generate_code (NEW_TOOL context) ---")
        new_tool_path = os.path.join(test_output_dir, "newly_generated_tool.py")
        gen_result = await code_service.generate_code(
            context="NEW_TOOL",
            prompt_or_description="A Python function that takes two integers and returns their sum.",
            # llm_config={"model_name": get_model_for_task("code_generation")} # Using default from class for this test
            target_path=new_tool_path
        )
        print(f"Generate Code Result: Status: {gen_result.get('status')}, Saved to: {gen_result.get('saved_to_path')}")
        if gen_result.get('code_string'):
            print(f"Generated Code (first 150 chars): {gen_result['code_string'][:150]}...")
        if gen_result.get('metadata'):
            print(f"Generated Metadata: {gen_result['metadata']}")
        if gen_result.get('error'): # pragma: no cover
            print(f"Error: {gen_result['error']}")
        # print(f"Logs: {gen_result.get('logs')}")


        # Test modify_code
        # For this test, we rely on MockSelfModService to provide the "original" code
        test_module_path_for_main = "ai_assistant.custom_tools.dummy_tool_main_test_svc"

        print("\n--- Testing modify_code (SELF_FIX_TOOL context) ---")
        # We'll simulate that the file doesn't exist locally, so CodeService relies on MockSelfModService
        mod_result = await code_service.modify_code(
            context="SELF_FIX_TOOL",
            existing_code=None, # Force fetching via self.self_modification_service
            modification_instruction="The function should add 10, not multiply by 10.",
            module_path=test_module_path_for_main, # Used by MockSelfModService
            function_name="function_to_be_fixed_by_main_svc" # Used by MockSelfModService
        )
        print(f"Modify Code Result: {mod_result.get('status')}")
        if mod_result.get('modified_code_string'):
            print(f"Modified Code (first 150 chars): {mod_result['modified_code_string'][:150]}...")
        if mod_result.get('error'):
            print(f"Error: {mod_result['error']}")
        print(f"Logs: {mod_result.get('logs')}")
        # No need to create/delete dummy files as mocks handle the code provision

        print("\n--- Testing generate_code (GENERATE_UNIT_TEST_SCAFFOLD) ---")
        sample_code_to_test = "def my_function(x, y):\n    return x + y\n\nclass MyClass:\n    def do_stuff(self):\n        pass"
        test_scaffold_path = os.path.join(test_output_dir, "test_my_module_scaffold.py")
        scaffold_result = await code_service.generate_code(
            context="GENERATE_UNIT_TEST_SCAFFOLD",
            prompt_or_description=sample_code_to_test, # This is the code_to_test
            additional_context={"module_name_hint": "my_module"},
            target_path=test_scaffold_path
        )
        print(f"Generate Unit Test Scaffold Result: Status: {scaffold_result.get('status')}, Saved to: {scaffold_result.get('saved_to_path')}")
        if scaffold_result.get("code_string"):
            # The mock provider for main() doesn't actually return a scaffold, so this might be empty.
            # This test is more about plumbing the context through.
            print(f"Generated Scaffold (first 200 chars):\n{scaffold_result['code_string'][:200]}...")
        if scaffold_result.get("error"): # pragma: no cover
            print(f"Error: {scaffold_result.get('error')}")
        # print(f"Logs: {scaffold_result.get('logs')}")


        print("\n--- Testing generate_code (EXPERIMENTAL_HIERARCHICAL_OUTLINE) ---")
        outline_desc = "A Python CLI tool to manage a simple to-do list stored in a JSON file. It needs add, remove, and list functions."
        # Mock the LLM provider for this specific call if you want to control output in __main__
        # For now, it will make a real call if provider is configured.
        # To test parsing, you might need to mock self.llm_provider.invoke_ollama_model_async here
        # for this specific test call if not running in a full unit test environment.

        # Example of how one might mock for main test, if needed for controlled output:
        # original_invoke = mock_llm_provider_instance.invoke_ollama_model_async
        # async def mock_invoke_for_outline(*args, **kwargs):
        #     if "JSON Outline:" in args[0]: # Check if it's the outline prompt
        #         return json.dumps({"module_name": "todo.py", "imports": ["json"], "components": []})
        #     return await original_invoke(*args, **kwargs) # Call original for other tests
        # mock_llm_provider_instance.invoke_ollama_model_async = mock_invoke_for_outline

        outline_result = await code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_OUTLINE",
            prompt_or_description=outline_desc
        )
        print(f"Generate Outline Result: Status: {outline_result.get('status')}")
        if outline_result.get("parsed_outline"):
            print(f"Parsed Outline (first level keys): {list(outline_result['parsed_outline'].keys())}")
        elif outline_result.get("outline_str"): # pragma: no cover
            print(f"Raw Outline Str (first 200 chars): {outline_result['outline_str'][:200]}...")
        if outline_result.get("error"): # pragma: no cover
            print(f"Error: {outline_result.get('error')}")
        # print(f"Logs: {outline_result.get('logs')}")

        # mock_llm_provider_instance.invoke_ollama_model_async = original_invoke # Restore if mocked

        print("\n--- Testing generate_code (EXPERIMENTAL_HIERARCHICAL_FULL_TOOL) ---")
        full_tool_desc = "A Python CLI tool to manage a simple to-do list stored in a JSON file. It needs add, remove, and list functions using argparse."

        # To test this without full LLM calls for outline AND details,
        # we'd need to mock _generate_detail_for_component or have the LLM provider return
        # very predictable outline and then predictable details.
        # For __main__ test, this will make actual LLM calls.

        full_tool_result = await code_service.generate_code(
            context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL",
            prompt_or_description=full_tool_desc
            # No target_path for this context as it doesn't produce a single final code string directly
        )
        print(f"Generate Full Tool (Outline+Details) Result: Status: {full_tool_result.get('status')}")
        if full_tool_result.get("parsed_outline"):
            print(f"Parsed Outline (keys): {list(full_tool_result['parsed_outline'].keys())}")
        if full_tool_result.get("component_details"):
            print("Component Details Generated:")
            for name, code_prev_obj in full_tool_result["component_details"].items():
                code_prev = str(code_prev_obj)
                print(f"  Component: {name}, Code (first 30 chars): {code_prev[:30].replace(chr(10), ' ')}...")
        if full_tool_result.get("error"): # pragma: no cover
            print(f"Error: {full_tool_result.get('error')}")
        # print(f"Logs: {full_tool_result.get('logs')}")


        print("\n--- Testing generate_code (HIERARCHICAL_GEN_COMPLETE_TOOL) ---")
        complete_tool_desc = "A Python module with a function to add two numbers and a class MyMath with a method to multiply them."
        complete_tool_path = os.path.join(test_output_dir, "hierarchically_generated_tool.py")

        complete_tool_result = await code_service.generate_code(
            context="HIERARCHICAL_GEN_COMPLETE_TOOL",
            prompt_or_description=complete_tool_desc,
            target_path=complete_tool_path
        )
        print(f"Generate Complete Tool Result: Status: {complete_tool_result.get('status')}, Saved to: {complete_tool_result.get('saved_to_path')}")
        if complete_tool_result.get("code_string"):
            print(f"Assembled Code (first 300 chars):\n{complete_tool_result['code_string'][:300]}...")
        else: # pragma: no cover
            print(f"No final code string generated. Outline: {complete_tool_result.get('parsed_outline') is not None}, Details: {complete_tool_result.get('component_details') is not None}")
        if complete_tool_result.get("error"): # pragma: no cover
            print(f"Error: {complete_tool_result.get('error')}")
        # print(f"Logs: {complete_tool_result.get('logs')}")

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
