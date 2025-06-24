# Unified Code Writing System (UCWS) - Phase 1 Requirements & Scope

## 1. Introduction

This document outlines the Phase 1 requirements and scope for the Unified Code Writing System (UCWS). The UCWS aims to centralize and standardize all code generation and modification tasks performed by the AI assistant, ensuring consistency, quality, and maintainability.

## 2. Goals for Phase 1

*   Establish a foundational architecture for the UCWS.
*   Support a limited set of initial code writing "contexts" or "tiers".
*   Refactor at least one existing code generation/modification use case to utilize the UCWS.
*   Provide clear input/output contracts for interacting with the UCWS.

## 3. Key Functionalities

The UCWS in Phase 1 should support the following key functionalities:

*   **A. Code Generation:**
    *   Accept a detailed prompt or a structured request defining the desired code (e.g., a new Python function).
    *   Interact with a configured Language Model (LLM) to produce code.
    *   Handle LLM responses, including basic cleaning and error checking (e.g., LLM unable to generate).
*   **B. Code Modification (Self-Fixing/Enhancement):**
    *   Accept existing code, a description of the problem or desired change, and relevant context (e.g., module path, function name).
    *   Interact with an LLM to produce a modified version of the code.
    *   (Future, but to keep in mind for interfaces): Support for applying changes using AST manipulation where possible (leveraging `self_modification.py`).
*   **C. LLM Interaction Management:**
    *   Centralized logic for selecting appropriate LLM models for code tasks (potentially based on context).
    *   Standardized prompt templating or construction for different code tasks.
*   **D. Result Handling:**
    *   Return generated/modified code as a primary output.
    *   Provide status indicators (e.g., success, failure, needs_review).
    *   Include error messages or reasons for failure if applicable.
*   **E. Code Analysis/Review (Placeholder):**
    *   The interface should allow for a code review step, but the actual review logic (e.g., linting, static analysis, LLM-based review) will be a placeholder or very basic in Phase 1. The system should be designed to integrate these more fully later.

## 4. Supported Contexts/Tiers (Phase 1)

The UCWS will initially support the following contexts:

*   **Context 1: `NEW_TOOL_CREATION_LLM`**
    *   **Description:** Generating a new Python function (tool) from a natural language description.
    *   **Input:** User's description of the tool's desired functionality.
    *   **Output:** Python code string for the new function, suggested metadata (function name, tool name, description).
    *   *Note:* This context will initially focus on LLM-based generation. AST-based construction or complex scaffolding is out of scope for Phase 1.
*   **Context 2: `EXISTING_TOOL_SELF_FIX_LLM`**
    *   **Description:** Modifying an existing tool's Python function code to fix a bug or implement a minor enhancement, based on a problem description and the original code. This context assumes an LLM is used to generate the *entire* new version of the function.
    *   **Input:** Module path, function name, problem description/goal, original function code.
    *   **Output:** Python code string for the modified function.
*   **Context 3: `EXISTING_TOOL_SELF_FIX_AST` (Refinement of Context 2)**
    *   **Description:** Applying a *specific, suggested code change* (potentially from an earlier LLM generation or a predefined pattern) to an existing tool's function using AST manipulation.
    *   **Input:** Module path, function name, new code string for the entire function.
    *   **Output:** Success/failure status of the modification.
    *   *Note:* This context primarily involves acting as a wrapper or client to `self_modification.edit_function_source_code`.

## 5. Input/Output Contracts (High-Level)

*   **Input (Request to UCWS):**
    *   `context_type`: An enum or string indicating which context (e.g., `NEW_TOOL_CREATION_LLM`, `EXISTING_TOOL_SELF_FIX_LLM`).
    *   `request_payload`: A dictionary containing context-specific data.
        *   For `NEW_TOOL_CREATION_LLM`: `{"description": "str"}`
        *   For `EXISTING_TOOL_SELF_FIX_LLM`: `{"module_path": "str", "function_name": "str", "problem_description": "str", "original_code": "str"}`
        *   For `EXISTING_TOOL_SELF_FIX_AST`: `{"module_path": "str", "function_name": "str", "new_code_string": "str"}`
    *   Optional: `llm_config_overrides` (e.g., specific model, temperature).

*   **Output (Response from UCWS):**
    *   `status`: Enum/string (e.g., `SUCCESS`, `FAILURE_LLM_GENERATION`, `FAILURE_CODE_APPLICATION`, `NEEDS_REVIEW`).
    *   `generated_code`: Optional string (the new or modified code).
    *   `error_message`: Optional string (if status indicates failure).
    *   `metadata`: Optional dictionary for additional context-specific outputs (e.g., suggested function name for new tools).

## 6. Out of Scope for Phase 1

*   Sophisticated project-level code generation (multiple files, build systems).
*   Advanced code review capabilities (deep static analysis, security vulnerability scanning).
*   Automated execution and testing of generated/modified code *within* the UCWS itself (this responsibility may lie with clients of UCWS, like `ActionExecutor`).
*   Direct modification of the AI assistant's core logic through this system.
*   Complex UI/interaction for code review/approval (basic CLI interaction by client is expected).

## 7. Future Considerations (Post-Phase 1)

*   Integration with version control (Git) for changes.
*   More granular code modification (e.g., adding a parameter, refactoring a block) rather than full function replacement for LLM-based modifications.
*   Support for more languages beyond Python.
*   More sophisticated tiering based on risk, complexity, or domain.
