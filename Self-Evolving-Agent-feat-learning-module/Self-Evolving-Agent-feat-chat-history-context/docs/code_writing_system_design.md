# Unified Code Writing System - Design Document (Phase 1)

## 1. Introduction & Goals

The Unified Code Writing System (UCWS) will be a centralized service within the AI assistant responsible for all tasks involving code generation and modification.
Its primary goals are:
-   **Standardization**: Provide a consistent approach and interface for all code synthesis and manipulation tasks.
-   **Quality**: Improve the quality, reliability, and maintainability of generated/modified code through standardized processes, prompts, and potential review stages.
-   **Modularity & Reusability**: Encapsulate code writing logic in one place, making it easier to manage, update, and reuse across different parts of the assistant.
-   **Context-Awareness (Tiering)**: Adapt its operations based on the context of the code task (e.g., generating a new tool, self-fixing existing agent code, scaffolding a user project).
-   **Extensibility**: Allow for future enhancements like integration with more sophisticated testing, static analysis, or security scanning tools.

## 2. Proposed Module Structure

The UCWS will reside in a new package: `ai_assistant.code_services`.

Initial modules might include:
-   `ai_assistant/code_services/main.py` (or `service.py`): Defines the main `CodeService` class and its interface.
-   `ai_assistant/code_services/prompts.py`: Stores standardized LLM prompt templates for various code tasks.
-   `ai_assistant/code_services/utils.py`: Helper functions for code manipulation, cleaning, or AST interactions if needed beyond what `self_modification.py` offers at a low level.
-   `ai_assistant/code_services/contexts.py`: (Future) Might define different context handlers or configurations (e.g., `ToolGenerationContext`, `SelfModificationContext`).

## 3. Core `CodeService` Class and Interface

The central component will be the `CodeService` class.

```python
# Conceptual interface for ai_assistant.code_services.main.CodeService

class CodeService:
    def __init__(self, llm_provider, self_modification_service): # Example dependencies
        # llm_provider: An interface to invoke LLMs (e.g., OllamaClient wrapper)
        # self_modification_service: Provides low-level file/AST ops (current self_modification.py functions)
        # Note: In the initial implementation, direct imports/calls for LLM and self_modification
        # may be used within methods, with the plan to fully utilize these injected
        # dependencies as their interfaces are formalized.
        pass

    async def generate_code(
        self,
        context: str, # e.g., "NEW_TOOL", "PROJECT_FILE", "UNIT_TEST_SCAFFOLD", "EXPERIMENTAL_HIERARCHICAL_OUTLINE", "EXPERIMENTAL_HIERARCHICAL_FULL_TOOL", "HIERARCHICAL_GEN_COMPLETE_TOOL"
        prompt_or_description: str,
        language: str = "python",
        target_path: Optional[str] = None, # e.g., filepath for a new tool or project file
        llm_config: Optional[Dict[str, Any]] = None, # e.g., model_name, temperature
        additional_context: Optional[Dict[str, Any]] = None # e.g., related project files, class definitions
    ) -> Dict[str, Any]: # Returns a dict with 'status', 'code_string', 'logs', 'error'
        """Generates new code based on a description/prompt and context."""
        # 1. Select appropriate LLM prompt template based on context & language.
        # 2. Format prompt with description and additional_context.
        # 3. Invoke LLM via llm_provider.
        # 4. Clean/validate LLM output (parsing metadata for NEW_TOOL, JSON for outlines, code cleaning for all).
        # 5. If target_path is provided and context is suitable (e.g., NEW_TOOL, GENERATE_UNIT_TEST_SCAFFOLD, HIERARCHICAL_GEN_COMPLETE_TOOL),
        #    attempt to save the final code_string to target_path using fs_utils.write_to_file. Update status and include 'saved_to_path' in result.
        # 6. Return result dictionary.
        pass

    async def modify_code(
        self,
        context: str, # e.g., "SELF_FIX_TOOL", "REFACTOR_PROJECT_FILE"
        existing_code: Optional[str] = None,
        modification_instruction: str, # Description of the problem or desired change
        language: str = "python",
        module_path: Optional[str] = None, # For self-modification, to identify the target
        function_name: Optional[str] = None, # For self-modification
        llm_config: Optional[Dict[str, Any]] = None,
        additional_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]: # Returns a dict with 'status', 'modified_code_string', 'logs', 'error', 'diff'
        """Modifies existing code based on an instruction."""
        # If existing_code is not provided, the service will attempt to fetch it
        # using module_path and function_name via the self_modification_service.
        # 1. Select appropriate LLM prompt template for code modification.
        # 2. Format prompt with existing_code, instruction, and additional_context.
        # 3. Invoke LLM.
        # 4. Clean/validate LLM output.
        # 5. (Optional) Calculate diff between original and modified.
        # 6. (Optional, based on context, e.g., for SELF_FIX_TOOL)
        #    Use self_modification_service to apply the change if module_path/function_name provided.
        # 7. Return result.
        pass

    # Other potential methods:
    # async def review_code(self, code_string: str, context: str) -> Dict[str, Any]: ...
    # async def generate_tests_for_code(self, code_string: str, context: str) -> Dict[str, Any]: ...
```
Contexts for `generate_code` include:
-   `"NEW_TOOL"`: Generates a new tool function along with metadata. `prompt_or_description` is the tool's description.
-   `"GENERATE_UNIT_TEST_SCAFFOLD"`: Generates a unit test scaffold for provided code. `prompt_or_description` is the code to test; `additional_context` can contain `module_name_hint`. Saves to `target_path` if provided.
-   `"EXPERIMENTAL_HIERARCHICAL_OUTLINE"`: Generates a JSON outline for hierarchical code generation.
-   `"EXPERIMENTAL_HIERARCHICAL_FULL_TOOL"`: Generates outline and then details for each component (no assembly).
-   `"HIERARCHICAL_GEN_COMPLETE_TOOL"`: Full hierarchical flow: outline, details, then assembly into final code. Saves to `target_path` if provided.


## 4. Input Parameters (Details)

### For `generate_code`:
-   `context`: String enum or constant defining the type of code generation task. Helps in selecting prompts and post-processing steps. Examples: `"NEW_TOOL"`, `"GENERATE_UNIT_TEST_SCAFFOLD"`, `"HIERARCHICAL_GEN_COMPLETE_TOOL"`.
-   `prompt_or_description`: The main user request or detailed description of what code is needed. For `"GENERATE_UNIT_TEST_SCAFFOLD"`, this is the actual code to generate tests for.
-   `language`: Target programming language (defaults to Python).
-   `target_path`: Optional. If provided, and if the `context` results in a saveable code string (e.g., "NEW_TOOL", "HIERARCHICAL_GEN_COMPLETE_TOOL", "GENERATE_UNIT_TEST_SCAFFOLD"), `CodeService` will attempt to save the generated code to this path using `fs_utils.write_to_file`. The CLI's new tool generation flow currently calls `CodeService` with `target_path=None` for the initial tool generation and then calls it again with a `target_path` for scaffold generation.
-   `llm_config`: Dictionary to override default LLM settings (model, temperature, max_tokens).
-   `additional_context`: Any other relevant information. For `"GENERATE_UNIT_TEST_SCAFFOLD"`, this can include `module_name_hint`. For hierarchical generation, it can provide broader context.

### For `modify_code`:
-   `context`: String enum or constant defining the type of code modification.
-   `existing_code`: The actual code string to be modified.
-   `modification_instruction`: Detailed description of the bug to fix or the change to implement.
-   `language`: Language of the `existing_code`.
-   `module_path`, `function_name`: Used in contexts like "SELF_FIX_TOOL" to identify the target for applying the modification using `self_modification.py` utilities.
-   `llm_config`: As above.
-   `additional_context`: As above.

## 5. Output Structure (Details)

Both methods will return a dictionary, generally including:
-   `status`: String indicating success, failure, or specific outcomes (e.g., "SUCCESS_CODE_GENERATED", "SUCCESS_HIERARCHICAL_ASSEMBLED", "PARTIAL_HIERARCHICAL_ASSEMBLED", "ERROR_NO_ORIGINAL_CODE", "ERROR_LLM_NO_SUGGESTION", "ERROR_UNSUPPORTED_CONTEXT", "ERROR_MISSING_DETAILS", "ERROR_APPLYING_CHANGE", "ERROR_SAVING_CODE", "ERROR_SAVING_ASSEMBLED_CODE", "ERROR_ASSEMBLY_FAILED").
-   `code_string` / `modified_code_string`: The generated or modified code. `None` if generation failed or not applicable for the context.
-   `logs`: A list of log messages or a structured log object detailing the steps taken by the service.
-   `error`: Error message if status indicates failure.
-   `diff`: (For `modify_code`) An optional diff string (e.g., unified diff) showing changes.
-   `saved_to_path`: Optional[str]. If `target_path` was provided and file saving was successful, this field will contain the absolute path to where the file was saved. `None` otherwise.
-   `parsed_outline`: (For hierarchical contexts) The JSON-like dictionary representing the code structure.
-   `component_details`: (For hierarchical contexts) A dictionary mapping component names to their generated code strings.

## 6. Initial Focus & Refactoring Path

-   The first capability to be integrated into this `CodeService` will be the "LLM-based tool fix" logic currently being added to `ActionExecutor`.
    -   The `LLM_CODE_FIX_PROMPT_TEMPLATE` will move to `code_services/prompts.py`.
    *   The logic from `ActionExecutor._generate_code_fix_with_llm` (getting original code, calling LLM, cleaning output) will form the basis of an implementation for `CodeService.modify_code` when the context is "SELF_FIX_TOOL".
-   `ActionExecutor` will then call `CodeService.modify_code(...)` instead of directly invoking the LLM for code fixes. It will still be responsible for the overall orchestration of the self-fix (testing, reversion).
-   The `self_modification.py` module will provide the low-level primitives for reading files, parsing ASTs, and writing changes to disk, to be used by `CodeService`.

#### CLI Interaction for New Tool Generation & Saving

The current CLI flow for generating new tools (`/generate_tool_code_with_llm`) has been refactored to:
1.  Call `CodeService.generate_code(context="NEW_TOOL", ..., target_path=None)` to get the code string and metadata.
2.  Perform interactive steps with the user (e.g., code review if applicable, confirmation of filenames and registration details based on metadata).
3.  If approved, the CLI then directly uses the `fs_utils.write_to_file` utility to save the (potentially refined) code to the user-confirmed path in `ai_assistant/custom_tools/`.
This approach allows `CodeService` to be capable of saving, while giving the CLI flexibility to manage user interaction before persisting the file.

##### Automated Unit Test Scaffold Generation for New Tools

To improve developer productivity and encourage testing, the CLI workflow for new tool generation (`/generate_tool_code_with_llm`) has been further enhanced:

1.  After a new tool is successfully generated by `CodeService` (using the "NEW_TOOL" context), reviewed (if applicable), saved by the CLI to its designated path (e.g., `ai_assistant/custom_tools/my_new_tool.py`), and registered with the tool system:
2.  The CLI immediately makes a second call to `CodeService.generate_code`.
3.  This call uses the `"GENERATE_UNIT_TEST_SCAFFOLD"` context.
4.  The `prompt_or_description` for this call is the source code of the newly generated tool.
5.  `additional_context` includes `{"module_name_hint": "ai_assistant.custom_tools.my_new_tool"}` (the actual module path of the new tool).
6.  A `target_path` is provided, typically `tests/custom_tools/test_my_new_tool.py`.
7.  `CodeService` then generates the unit test scaffold and saves it to this `target_path` (utilizing its internal call to `fs_utils.write_to_file`).
8.  The CLI logs the success or failure of this scaffold generation and saving step.

This provides an immediate, basic test structure for any newly created tool, facilitating subsequent test-driven development or refinement.

## 7. Future Considerations (Phase 2+)

-   Context-specific configurations (e.g., different LLM models/prompts for tool gen vs. bug fixing).
-   Integration with static analysis tools (e.g., linters, security scanners) run automatically on generated/modified code.
-   More sophisticated testing capabilities invoked by `CodeService`.
-   Interactive mode where the service can ask clarifying questions if a prompt is ambiguous.
-   Management of different code generation "strategies" (e.g., different LLMs, few-shot prompting vs. zero-shot).

## 8. Handling Large or Complex Code Generation Tasks (Future Strategies)

As the complexity of code generation tasks increases (e.g., generating multi-function modules, entire classes, or complex project structures), relying on a single LLM call to produce the entire output may become unreliable or exceed token limits. The Unified Code Writing System (UCWS) should be designed to accommodate more sophisticated strategies for such scenarios.

Potential strategies include:

### a. Hierarchical Code Generation (Outline then Detail)

-   **Concept**: For a complex request (e.g., generating a multi-function module, a class with several methods, or a small project scaffold), this strategy first instructs the LLM to produce a structured outline or plan of the code. Then, for each part of this outline, a separate, more focused LLM call is made to generate the detailed code. Finally, these generated pieces are assembled.

-   **Triggering**:
    -   A new `context` for `CodeService.generate_code`, e.g., `HIERARCHICAL_GEN_TOOL`, `HIERARCHICAL_GEN_CLASS`, `HIERARCHICAL_GEN_MODULE`.
    -   Alternatively, a `strategy='hierarchical'` parameter could be added to `generate_code`, with the `prompt_or_description` perhaps including hints about the desired top-level structure.

-   **Workflow**:

    1.  **Outline Generation**:
        *   **Input to `CodeService`**: High-level description of the desired code (e.g., "a Python class for managing a to-do list with add, remove, and list functionalities, storing data in JSON").
        *   **Prompt Construction**: A specialized prompt template (e.g., `LLM_HIERARCHICAL_OUTLINE_PROMPT`) instructs the LLM to return a structured representation of the code components.
        *   **Expected Outline Structure (LLM Output 1)**: Preferably JSON, defining modules, classes, function/method signatures, and brief descriptions or placeholders for implementation logic. (Refer to "Phase 1 Prototype: Outline Generation" below for details on the implemented `EXPERIMENTAL_HIERARCHICAL_OUTLINE` context).

    2.  **Detail Generation (Iterative)**:
        *   For each component in the parsed outline (e.g., each method body for each class):
            *   **Prompt Construction**: A specialized prompt (e.g., `LLM_COMPONENT_DETAIL_PROMPT`) is used. This prompt includes:
                *   The specific instruction (e.g., "Implement the body for the function `add_task`").
                *   The signature of the function/method.
                *   The description/`body_placeholder` from the outline.
                *   Crucially, relevant parts of the overall outline as context (e.g., class name, attributes, other method signatures in the same class, required imports). This is passed via `additional_context`.
            *   **Expected Detail Structure (LLM Output 2.x)**: Raw Python code for the function/method body.
            *   **`CodeService` Action & Prompting**: The `_generate_detail_for_component` method in `CodeService` implements this step. It formats the `LLM_COMPONENT_DETAIL_PROMPT_TEMPLATE` (now added to `service.py`). This prompt is populated with:
                *   `overall_context_summary`: Derived from the `full_outline`, including parent class details (name, attributes, description) if the component is a method.
                *   `component_type`, `component_name`, `component_signature`, `component_description`, `component_body_placeholder`: All from the specific `component_definition`.
                *   `module_imports`: Extracted from the `full_outline`.
            The method then calls the LLM provider. The LLM is instructed to generate the full component code (signature and body) for consistency. Output is cleaned (markdown removal, newline normalization). It returns the code string or `None` if generation fails (e.g., LLM error, specific error marker returned by LLM, or output too short).

    3.  **Code Assembly (Implemented in `_assemble_components`)**:
        *   **Input**: The `_assemble_components` method in `CodeService` takes the `parsed_outline` (dictionary) and `component_details` (dictionary mapping component keys to their generated code strings or `None`).
        *   **Process**:
            *   Initializes an empty list to store parts of the final code.
            *   Appends a module-level docstring if defined in `outline.module_docstring`.
            *   Appends all import statements listed in `outline.imports`.
            *   Iterates through each `component` in `outline.components`:
                *   **Functions**: Retrieves the corresponding code from `component_details` using the function's name. If not found or `None`, a placeholder function (with signature and docstring derived from the outline) is generated with a `pass # TODO: Implement` body.
                *   **Classes**:
                    *   Constructs the `class ClassName:` line.
                    *   Appends an indented class docstring if available in the class component's `description`.
                    *   Handles class `attributes` from the outline by inserting them as commented type hints if an `__init__` method (which should handle them) is not detailed.
                    *   Iterates through `methods` defined for the class. For each method:
                        *   Retrieves its code from `component_details` (keyed as `ClassName.MethodName`).
                        *   If code exists, it's indented (all lines) and appended.
                        *   If code is missing, an indented placeholder method is generated.
                    *   If a class definition would be empty (no docstring, attributes, or methods), `pass` is added.
            *   Appends a main execution block if defined in `outline.main_execution_block`.
        *   **Output**: The method joins all collected code parts into a single string, performs a basic cleanup of excessive newlines, and returns the assembled Python code.

    4.  **End-to-End Orchestration via `"HIERARCHICAL_GEN_COMPLETE_TOOL"` Context**:
        The `"HIERARCHICAL_GEN_COMPLETE_TOOL"` context in `CodeService.generate_code` manages the full hierarchical pipeline:
        1.  It first invokes the outline and detail generation logic (currently by calling `generate_code` with `context="EXPERIMENTAL_HIERARCHICAL_FULL_TOOL"` internally). This produces the `parsed_outline` and the `component_details` dictionary.
        2.  If the previous steps are successful (even if some individual component details failed to generate, resulting in `None` for those parts), it then calls `self._assemble_components(parsed_outline, component_details)`.
        3.  The `code_string` field in the final returned dictionary is populated with this assembled code.
        4.  The `status` reflects the outcome of the entire process, such as:
            *   `SUCCESS_HIERARCHICAL_ASSEMBLED`: If the outline, all component details (or placeholders for missing ones), and assembly were successful.
            *   `PARTIAL_HIERARCHICAL_ASSEMBLED`: If outline and assembly were successful, but some component details could not be generated (and are thus placeholders in the assembled code).
            *   `ERROR_ASSEMBLY_FAILED`: If an error occurred during the assembly phase itself.
            *   It also propagates error statuses from the outline/detail generation phases if they fail before assembly.

    5.  **Error Handling**:
        *   If outline generation fails: Return error.
        *   If a sub-component detail generation fails:
            *   Option 1: Halt and report error for that component (current behavior in `EXPERIMENTAL_HIERARCHICAL_FULL_TOOL` and `HIERARCHICAL_GEN_COMPLETE_TOOL` results in `None` for that component and a partial success status).
            *   Option 2: Insert a placeholder (e.g., `pass # TODO: Implement this`) and continue with other components (this is implicitly handled by `_assemble_components` if a detail is `None`).
            *   Option 3: Attempt a retry or a correction prompt for the failed component (future enhancement).
        *   Assembly errors (e.g., generated code is syntactically incorrect when combined) would need to be caught and reported during the assembly phase (current `HIERARCHICAL_GEN_COMPLETE_TOOL` includes a `try-except` for this).

-   **`CodeService.generate_code` Parameters for Hierarchical Strategy**:
    *   `context`: e.g., `HIERARCHICAL_GEN_MODULE`.
    *   `prompt_or_description`: The high-level requirement.
    *   `additional_context`: Could be used to pass in existing parts of an outline if generation is resumed, or to provide overarching project constraints.
    *   A new parameter like `generation_options: Optional[Dict[str, Any]] = None` could control aspects like "max_retries_for_sub_component".

-   **`CodeService.generate_code` Return Value for Hierarchical Strategy**:
    *   `status`: Could indicate "SUCCESS_HIERARCHICAL_ASSEMBLED", "PARTIAL_HIERARCHICAL_ASSEMBLED", "ERROR_ASSEMBLY_FAILED", "ERROR_OUTLINE_FAILED", etc.
    *   `code_string`: The fully assembled code, or `None` if assembly failed or was not reached.
    *   `outline_object` / `parsed_outline`: The parsed outline.
    *   `component_details`: A dictionary of results for each detail generation step.
    *   `logs`, `error`.

-   **Benefits**:
    *   Manages complexity by breaking down large tasks.
    *   Allows LLM to focus on smaller, more constrained generation problems, potentially improving quality and reducing hallucinations.
    *   Can generate more structured and larger outputs than a single LLM call might reliably produce.
    *   Provides better traceability if a specific part of the code is problematic.

-   **Challenges**:
    *   Designing robust prompts for both outline and detail generation.
    *   Reliably parsing the structured outline from the LLM.
    *   Managing context and dependencies between components (e.g., ensuring a function body correctly uses class attributes defined in the outline).
    *   Assembling code with correct syntax and indentation (current `_assemble_components` is a good first pass).
    *   Increased number of LLM calls (cost and latency).

#### Example Use Case: CSV Processing CLI Tool

This section illustrates the hierarchical generation process with a concrete example.

**High-Level Request to `CodeService.generate_code`**:
`prompt_or_description`: "Generate a Python command-line tool using `argparse`. The tool should:
1. Accept a CSV filepath as a mandatory argument (`--filepath`).
2. Accept a column name or index (integer) as a mandatory argument (`--column`).
3. Accept an operation type (e.g., 'sum', 'average', 'count_unique') as an optional argument (`--operation`, default 'sum').
4. Read the CSV, attempt to convert the specified column to numeric values (if applicable for 'sum'/'average').
5. Perform the specified operation on that column.
6. Print the result to the console.
7. Include basic error handling (e.g., file not found, column not found, non-numeric data for numeric ops)."
`context`: `HIERARCHICAL_GEN_COMPLETE_TOOL`

**1. Outline Generation (Conceptual LLM Output 1 - JSON):**

An LLM tasked with generating the outline might produce something like this:

```json
{
  "module_name": "csv_analyzer_tool.py",
  "description": "A CLI tool to perform basic analysis on a CSV column.",
  "imports": ["argparse", "csv", "statistics"],
  "components": [
    {
      "type": "function",
      "name": "parse_arguments",
      "signature": "() -> argparse.Namespace",
      "description": "Parses command-line arguments using argparse.",
      "body_placeholder": "Define arguments for filepath, column, and operation. Return parsed args."
    },
    {
      "type": "function",
      "name": "read_and_process_column",
      "signature": "(filepath: str, column_identifier: str, operation: str) -> any",
      "description": "Reads the CSV, extracts, and processes the specified column.",
      "body_placeholder": "Open CSV. Identify column by name or index. Extract data. Convert to numeric if needed. Perform operation. Handle errors (FileNotFound, ColumnNotFound, ValueError for conversion)."
    },
    {
      "type": "function",
      "name": "main",
      "signature": "() -> None",
      "description": "Main function to orchestrate the tool's logic.",
      "body_placeholder": "Call parse_arguments. Call read_and_process_column with parsed args. Print the result or error messages."
    }
  ],
  "main_execution_block": "if __name__ == '__main__':\n    main()"
}
```

**2. Detail Generation (Conceptual Sub-Prompts & LLM Outputs 2.x):**
(Refer to previous documentation for example sub-prompts and outputs for `parse_arguments` and `read_and_process_column`.)

**3. Code Assembly (Conceptual `CodeService` Action):**
(Refer to previous documentation for the assembled code example.)

This detailed example helps clarify the expected inputs, intermediate LLM outputs (outline and component details), and the final assembled code for the hierarchical generation strategy.

#### Phase 1 Prototype: Outline Generation

As an initial step towards implementing hierarchical code generation, a prototype focusing solely on the "Outline Generation" phase has been developed within `CodeService`.

*   **Prompt Template**: A new prompt template, `LLM_HIERARCHICAL_OUTLINE_PROMPT_TEMPLATE`, has been added to `ai_assistant/code_services/service.py`. This template specifically instructs the LLM to return a JSON object representing the structural outline of the desired code (modules, classes, functions, etc.) based on a high-level description.

*   **`CodeService.generate_code` Context**: The `generate_code` method in `CodeService` now handles a new `context` string: `"EXPERIMENTAL_HIERARCHICAL_OUTLINE"`.

*   **Workflow for `"EXPERIMENTAL_HIERARCHICAL_OUTLINE"`**:
    1.  The method receives a high-level description of the code to be generated (via the `prompt_or_description` parameter).
    2.  It formats the `LLM_HIERARCHICAL_OUTLINE_PROMPT_TEMPLATE` with this description.
    3.  It calls the configured LLM provider (e.g., `self.llm_provider.invoke_ollama_model_async`) with this prompt.
    4.  It attempts to parse the LLM's response string as a JSON object. Basic cleaning (e.g., stripping markdown backticks around the JSON) is performed.
    5.  The method returns a dictionary containing:
        -   `status`: Indicating success (`SUCCESS_OUTLINE_GENERATED`) or failure (e.g., `ERROR_LLM_NO_OUTLINE`, `ERROR_OUTLINE_PARSING`).
        -   `outline_str`: The raw string response from the LLM.
        -   `parsed_outline`: The Python dictionary resulting from successful JSON parsing of the outline.
        -   `code_string`: This is explicitly `None` for this context.
        -   `metadata`: This is also `None` for this context (as `parsed_outline` is separate).
        -   `logs` and `error` messages.

*   **Conceptual Helper Methods**: To further structure the eventual full hierarchical process, conceptual private placeholder methods have been added to `CodeService`. The `_generate_detail_for_component` method has been implemented to generate code for individual components based on the outline and the `LLM_COMPONENT_DETAIL_PROMPT_TEMPLATE`. The `_generate_hierarchical_outline` (whose core logic is currently in the `EXPERIMENTAL_HIERARCHICAL_OUTLINE` context handler) and `_assemble_components` (now implemented) form the key parts of this strategy.

This prototype validates the first crucial step: obtaining a structured plan from the LLM and generating details for its components. The assembly step is also now implemented.

#### Next Steps & Challenges for Full Hierarchical Implementation

With the initial implementation of outline generation, detail generation for individual components, and code assembly, the focus shifts to refining and robustifying the hierarchical strategy:

1.  **Refining Assembly Logic (`_assemble_components`)**:
    *   Improve handling of indentation for complex nested structures if needed.
    *   More sophisticated management of class attributes (e.g., ensuring `__init__` methods correctly initialize them if attributes are defined in the outline, or using `dataclasses`).
    *   Better strategies for placing comments or `pass` statements in empty classes/methods if placeholders were not fully detailed by the LLM.
2.  **Improving Detail Generation Prompts & Context**: Continuously refining `LLM_COMPONENT_DETAIL_PROMPT_TEMPLATE` and the `overall_context_summary` provided to it to ensure high-quality, contextually-aware code generation for components, especially managing inter-component dependencies (e.g., one function calling another defined in the same outline).
3.  **End-to-End Orchestration Refinement**: Review and potentially refactor the orchestration logic within `generate_code` (e.g., the `HIERARCHICAL_GEN_COMPLETE_TOOL` context) to directly use the private helper methods (`_generate_hierarchical_outline`, `_generate_detail_for_component`, `_assemble_components`) rather than chained calls to `generate_code` with different contexts. This would make the flow cleaner.
4.  **Robust Error Handling & Partial Success**: Further enhance how failures are handled at each stage (outline, detail, assembly). For instance, if a critical component fails detail generation, should assembly still proceed? Define clearer criteria for `PARTIAL_HIERARCHICAL_ASSEMBLED` and allow for more granular error reporting.
5.  **Context Management**: For very large projects, develop strategies for efficiently passing relevant contextual information from the overall outline to detail generation prompts, especially for very large outlines, to avoid exceeding LLM token limits. This might involve summarizing or selecting only relevant parts of the `full_outline`.
6.  **Outline Schema Adherence & Iteration**: Continue to ensure LLMs can reliably produce JSON outlines adhering to the expected schema. This might require prompt refinement, few-shot examples, or output validation and correction loops for the outline itself.
7.  **Testing**: Expand the comprehensive test suite for the end-to-end hierarchical generation process, including more complex outline structures and edge cases in assembly and detail generation.
8.  **File Output**: For contexts that generate full modules/projects, integrate saving the assembled `code_string` to a target file path (potentially using `self.self_modification_service` or similar file utilities within `CodeService.generate_code` if `target_path` is provided).

### b. Chained LLM Calls (Iterative Refinement or Extension)
-   **Concept**: Use a sequence of LLM calls, where the output of one call becomes input or context for the next, to iteratively build or refine code.
-   **Process Examples**:
    *   **Generate then Refine**: Generate initial code, then pass it back to the LLM with instructions to "refactor for clarity," "add error handling," "improve performance," or "add comments and docstrings."
    *   **Incremental Build**: Generate a core function, then ask the LLM to "add a helper function for X," then "add unit tests for the core function."
-   **Benefits**: Can improve code quality iteratively, allows for more complex interactions than a single prompt.
-   **Challenges**: Designing effective prompts for each stage, managing context window limitations if the code grows large.

### c. Template-Based Filling with LLM
-   **Concept**: Define robust code templates or skeletons for common structures (e.g., a new tool, a class, a REST API endpoint). The LLM is then tasked with filling in specific variable parts of this template.
-   **Process**:
    1.  Select an appropriate template based on the `context`.
    2.  Identify the "slots" in the template that need LLM-generated code.
    3.  Prompt the LLM to generate only the code for these specific slots.
    4.  Inject the LLM's output into the template.
-   **Benefits**: Enforces structure and boilerplate, reduces the amount of code the LLM needs to generate from scratch, potentially higher reliability for common patterns.
-   **Challenges**: Creating and maintaining good templates, designing prompts that make the LLM fill slots correctly.

### d. Combining Strategies
-   These strategies are not mutually exclusive. For example, hierarchical generation might be used to create an outline of classes and methods, and then template-based filling could be used for the boilerplate of each method, with a final LLM call to generate the core logic within each method. Iterative refinement could then be applied.

### e. Considerations for `CodeService` Interface
-   The `generate_code` and `modify_code` methods might need to accept parameters that control these strategies (e.g., `generation_strategy: str = "single_pass" | "hierarchical"`, `max_iterations: int`).
-   The return value might need to include more structured information about the generation process if multiple steps were involved.
-   The `additional_context` parameter could become more important for passing information between chained calls or providing context for filling templates.

These strategies represent future enhancements to ensure the UCWS can scale to more demanding code generation and modification tasks. Initial implementations will likely focus on `single_pass` generation for simpler contexts.

[end of Self-Evolving-Agent-feat-chat-history-context/docs/code_writing_system_design.md]

[end of Self-Evolving-Agent-feat-chat-history-context/docs/code_writing_system_design.md]
