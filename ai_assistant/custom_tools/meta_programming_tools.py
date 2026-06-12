import os
import sys
import re
import logging
import time
import ast
from typing import TYPE_CHECKING, Optional
import asyncio
from ai_assistant.config import get_model_for_task
if TYPE_CHECKING:
    from ai_assistant.core.action_executor import ActionExecutor
logger = logging.getLogger(__name__)
GENERATED_TOOLS_DIR_NAME = 'generated'
GENERATED_TOOLS_MODULE_PATH_PREFIX = f'ai_assistant.custom_tools.{GENERATED_TOOLS_DIR_NAME}'

def get_generated_tools_path() -> str:
    """Returns the absolute path to the directory where generated tools are stored."""
    custom_tools_dir = os.path.dirname(__file__)
    path = os.path.join(custom_tools_dir, GENERATED_TOOLS_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    if path not in sys.path:
        sys.path.append(path)
    return path

async def generate_new_tool_from_description(tool_description: str, suggested_tool_function_name: Optional[str]=None, suggested_filename: Optional[str]=None, action_executor: Optional['ActionExecutor']=None) -> str:
    """
    Generates Python code for a new tool based on a description and saves it
    to 'ai_assistant/custom_tools/generated/'. The LLM infers the tool's
    name, arguments, and implementation. A restart or tool refresh mechanism
    is typically required to activate the new tool.

    Args:
        action_executor: Provides access to the LLM interface.
        tool_description: Natural language description of the tool to create.
        suggested_tool_function_name: Optional specific function name for the new tool.
        suggested_filename: Optional specific filename (e.g., my_tool.py).

    Returns:
        A string indicating the result of the operation.
    """
    has_llm = action_executor and hasattr(action_executor, 'llm_interface') or (action_executor and hasattr(action_executor, 'code_service') and hasattr(action_executor.code_service, 'llm_provider'))
    if not has_llm:
        return 'Error: ActionExecutor with LLM interface (or CodeService) is required for tool generation.'
    function_name_guidance = f'The primary tool function should be named: `{suggested_tool_function_name}`.' if suggested_tool_function_name else ''
    if suggested_filename:
        processed_sugg_filename = os.path.basename(suggested_filename)
        if not processed_sugg_filename.endswith('.py'):
            processed_sugg_filename += '.py'
        filename_guidance = f'Save the tool in a file named: `{processed_sugg_filename}`.'
    else:
        filename_guidance = 'Suggest a suitable, PEP8-compliant Python filename for this tool (e.g., `utility_helpers.py` or `data_processor_tool.py`).'
    base_prompt = f'\nYou are an expert Python programmer assisting an AI agent by creating new tools.\nYour task is to generate the complete Python code for a new tool based on the following description.\n\nTool Description:\n"{tool_description}"\n\n{function_name_guidance}\n{filename_guidance}\n\nYour output MUST strictly follow this format:\n1.  The Python code block for the tool, enclosed in triple backticks (```python ... ```).\n2.  On a new line, after the code block, the suggested filename using the prefix "Suggested Filename: ".\n\nThe Python code should:\n- Be a single, self-contained Python script/module.\n- Include a clear function definition for the tool.\n- Use type hints for all arguments and return types.\n- Have a comprehensive docstring for the main tool function, explaining what it does, its arguments (name, type, description), and what it returns (type, description). This docstring will be used by the AI assistant.\n- Include necessary import statements at the top of the script.\n- Implement the core logic to fulfill the described functionality.\n- Handle potential errors gracefully (e.g., using try-except blocks).\n- If the tool needs `action_executor` (e.g., to call other tools), it should accept `action_executor: ActionExecutor` as its first argument.\n\nExample Tool Structure:\n```python\nimport os\nfrom typing import TYPE_CHECKING, List\n\nif TYPE_CHECKING:\n    from ai_assistant.core.action_executor import ActionExecutor\n\nasync def example_tool_function(action_executor: "ActionExecutor", items: List[str]) -> str:\n    """\n    This is an example docstring. It processes items.\n    Args:\n        action_executor: The action executor.\n        items (List[str]): A list of strings to process.\n    Returns:\n        str: A summary of the processing.\n    """\n    try:\n        # Tool logic here\n        return f"Processed {{len(items)}} items."\n    except Exception as e:\n        # import logging; logger = logging.getLogger(__name__); logger.error(f"Error: {{e}}")\n        return f"Error: {{e}}"\n```\nNow, generate the Python code and the suggested filename for the described tool.\n'
    try:
        model_name = get_model_for_task('tool_creation')
        llm = None
        if hasattr(action_executor, 'llm_interface'):
            llm = action_executor.llm_interface
        elif hasattr(action_executor, 'code_service') and hasattr(action_executor.code_service, 'llm_provider'):
            llm = action_executor.code_service.llm_provider
        if not llm:
            return 'Error: Could not find LLM provider in ActionExecutor.'

        # --- Redundancy Guardrail ---
        try:
            from ai_assistant.tools.tool_system import tool_system_instance
            existing_tools = await asyncio.to_thread(tool_system_instance.list_tools)
            
            # Filter tools to check (skip system tools if desired, but redundant custom tools are the main issue)
            # We'll check all of them.
            tool_summaries = []
            for name, details in existing_tools.items():
                desc = details.get('description', 'No description')
                tool_summaries.append(f"- {name}: {desc[:150]}...")
            
            tool_list_str = "\n".join(tool_summaries)
            
            check_prompt = f"""
You are an AI governance system.
A user wants to create a new tool.
Description: "{tool_description}"

Here is a list of existing tools:
{tool_list_str}

Is the requested tool redundant with any existing tool? 
If there is a tool that ALREADY performs the requested functionality (e.g. creating a notification, setting a reminder), output "YES: <Tool Name>". 
If the requested tool is sufficiently unique or specialized, output "NO".

Answer:
"""
            # Use quick check with LLM
            # We can use the same llm instance.
            check_response = ""
            if hasattr(llm, 'send_request'):
                check_response = await llm.send_request(prompt=check_prompt, model_name=get_model_for_task('fast_task'), temperature=0.0)
            elif hasattr(llm, 'invoke_ollama_model_async'):
                check_response = await llm.invoke_ollama_model_async(check_prompt, model_name=get_model_for_task('fast_task'), temperature=0.0)
            
            if "YES:" in check_response:
                match = re.search(r"YES:\s*([\w_]+)", check_response)
                existing_tool_name = match.group(1) if match else "an existing tool"
                logger.warning(f"Redundancy Guardrail blocked tool creation. Existing: {existing_tool_name}")
                return f"ABORTED: A similar tool already exists: '{existing_tool_name}'. Please use that tool instead of creating a duplicate."
                
        except Exception as e:
            logger.warning(f"Redundancy Guardrail check failed (proceeding with creation): {e}")
        # ----------------------------

        generated_code = ''
        final_filename = ''
        last_error = ''
        
        # --- Council Review Loop ---
        try:
            from ai_assistant.core.reviewer import ReviewerAgent
            from ai_assistant.core.critical_reviewer import CriticalReviewCoordinator
        except ImportError:
            logger.warning("Could not import ReviewerAgent or CriticalReviewCoordinator. Skipping Council Review.")
            ReviewerAgent = None
            CriticalReviewCoordinator = None

        council_feedback = ""
        
        max_retries = 3
        for attempt in range(max_retries):
            current_prompt = base_prompt
            
            # Append previous error or Council feedback to the prompt
            if attempt > 0:
                failure_context = ""
                if last_error:
                    failure_context += f"Previous attempt failed verification/parsing: {last_error}\n"
                if council_feedback:
                    failure_context += f"The Council rejected the previous code with this reasoning:\n{council_feedback}\n"
                
                current_prompt += f'\n\nIMPORTANT: Your previous attempt failed. Please fix the code based on this feedback:\n{failure_context}'

            logger.info(f'Tool generation attempt {attempt + 1}/{max_retries}')
            
            if hasattr(llm, 'send_request'):
                llm_response = await llm.send_request(prompt=current_prompt, model_name=model_name, temperature=0.2)
            elif hasattr(llm, 'invoke_ollama_model_async'):
                llm_response = await llm.invoke_ollama_model_async(current_prompt, model_name=model_name, temperature=0.2)
            else:
                return f"Error: LLM provider {llm} has neither 'send_request' nor 'invoke_ollama_model_async'."
            
            if not isinstance(llm_response, str) or not llm_response.strip():
                last_error = 'LLM returned empty response.'
                continue
                
            code_match = re.search('```(?:python)?\\s*\\n(.*?)\\n```', llm_response, re.DOTALL | re.IGNORECASE)
            filename_match = re.search('Suggested Filename:\\s*([\\w_.-]+\\.py)', llm_response)
            
            if not code_match:
                last_error = 'LLM did not provide a Python code block.'
                continue
                
            candidate_code = code_match.group(1).strip()
            
            # Filename extraction logic
            if 'Suggested Filename:' in candidate_code:
                internal_filename_match = re.search('Suggested Filename:\\s*([\\w_.-]+\\.py)', candidate_code)
                if internal_filename_match and (not suggested_filename) and (not filename_match):
                    filename_match = internal_filename_match
                candidate_code = re.sub('^Suggested Filename:.*$', '', candidate_code, flags=re.MULTILINE).strip()
            
            # Syntax Check
            try:
                ast.parse(candidate_code)
            except SyntaxError as e:
                lines = candidate_code.splitlines()
                if e.lineno and 0 <= e.lineno - 1 < len(lines):
                    failing_line = lines[e.lineno - 1]
                    last_error = f"SyntaxError on line {e.lineno}: {e.msg}\nFailing Line: '{failing_line}'"
                else:
                    last_error = f'SyntaxError: {e}'
                logger.warning(f'Generated code failed syntax check on attempt {attempt + 1}: {e}')
                continue
                
            # Deduplicate Imports (Deterministic pass before review)
            def deduplicate_imports(code_str: str) -> str:
                try:
                    ast.parse(code_str) # Re-verify syntax just in case
                    lines = code_str.splitlines()
                    seen_imports = set()
                    new_lines = []
                    for line in lines:
                        stripped = line.strip()
                        if stripped.startswith("import ") or stripped.startswith("from "):
                            if stripped in seen_imports: continue
                            seen_imports.add(stripped)
                        new_lines.append(line)
                    return "\n".join(new_lines)
                except Exception: return code_str

            candidate_code = deduplicate_imports(candidate_code)
            
            # --- Council Review Step ---
            if CriticalReviewCoordinator and ReviewerAgent:
                try:
                    # Dynamically instantiate reviewers for this session
                    skeptic = ReviewerAgent("council_skeptic")
                    judge = ReviewerAgent("council_judge")
                    coordinator = CriticalReviewCoordinator(skeptic, judge)
                    
                    logger.info(f"Convening The Council for new tool review (Attempt {attempt + 1})...")
                    is_approved, reasoning = await coordinator.execute_council_debate(
                        proposed_code=candidate_code,
                        proposal_description=f"New Tool Creation: {tool_description}",
                        original_code="# New File Creation", 
                        module_path="new_tool.py", # Placeholder for context
                        llm_provider=llm
                    )
                    
                    if not is_approved:
                        logger.warning(f"Council REJECTED the new tool code (Attempt {attempt+1}). Reasoning: {reasoning}")
                        council_feedback = reasoning
                        last_error = "" # Clear syntax error as this is a review rejection
                        continue # Retry loop
                    
                    logger.info(f"Council APPROVED the new tool code. Reasoning: {reasoning}")
                    
                except Exception as e_review:
                    logger.error(f"Error during Council Review: {e_review}. Proceeding with caution (Fail-Open or logging).")
                    pass
            # ---------------------------

            # If we got here, it passed syntax and (if applicable) Council review
            generated_code = candidate_code
            
            if suggested_filename:
                final_filename = os.path.basename(suggested_filename)
            elif filename_match:
                final_filename = filename_match.group(1).strip()
                
            break # Success!

        if not generated_code:
            final_reason = f"Last Syntax Error: {last_error}" if last_error else f"Council Rejection: {council_feedback}"
            return f'Error: Failed to generate valid tool code after {max_retries} attempts. {final_reason}'

        if not final_filename:
            func_name_match = re.search('def\\s+([\\w_]+)\\s*\\(', generated_code)
            base_name = func_name_match.group(1) if func_name_match else f'generated_tool_{int(time.time())}'
            final_filename = f'{base_name}.py'
            logger.warning(f'No filename suggested by LLM or user. Using fallback: {final_filename}')
        
        final_filename = re.sub('[^\\w_.-]', '', final_filename)
        if not final_filename or not final_filename.endswith('.py'):
            final_filename = f'tool_{int(time.time())}.py'
        


        generated_tools_dir = get_generated_tools_path()
        file_path = os.path.join(generated_tools_dir, final_filename)
        init_py_path = os.path.join(generated_tools_dir, '__init__.py')
        if not os.path.exists(init_py_path):
            with open(init_py_path, 'w', encoding='utf-8') as f_init:
                f_init.write("# This file makes Python treat the 'generated' directory as a package.\n")
            logger.info(f'Created __init__.py in {generated_tools_dir}')
        with open(file_path, 'w', encoding='utf-8') as f_tool:
            f_tool.write(generated_code)
        tool_function_name = None
        func_match = re.search('def\\s+([\\w_]+)\\s*\\(', generated_code)
        if func_match:
            tool_function_name = func_match.group(1)
        if tool_function_name:
            module_name = final_filename.replace('.py', '')
            import_statement = f'from .{module_name} import {tool_function_name}\n'
            current_init_content = ''
            if os.path.exists(init_py_path):
                with open(init_py_path, 'r', encoding='utf-8') as f_read_init:
                    current_init_content = f_read_init.read()
            if import_statement.strip() not in current_init_content:
                with open(init_py_path, 'a', encoding='utf-8') as f_append_init:
                    if current_init_content and (not current_init_content.endswith('\n')):
                        f_append_init.write('\n')
                    f_append_init.write(import_statement)
                logger.info(f"Appended '{import_statement.strip()}' to {init_py_path}")
        try:
            from ai_assistant.tools.tool_system import tool_system_instance
            reload_result = tool_system_instance.refresh_custom_tools()
            logger.info(f'Auto-reloaded tools after generation: {reload_result}')
            reload_msg = 'Tool generated and reloaded. You can use it immediately.'
        except Exception as e_reload:
            logger.error(f'Failed to auto-reload tools: {e_reload}')
            reload_msg = 'Tool generated, but auto-reload failed. Please restart or use /refresh_tools.'
        test_generation_msg = ''
        try:
            test_gen_result = await _generate_test_for_tool(tool_name=tool_function_name, tool_filename=final_filename, tool_code=generated_code, action_executor=action_executor)
            test_generation_msg = f'\nTest Generation: {test_gen_result}'
        except Exception as e_test:
            logger.error(f'Failed to generate test for tool {tool_function_name}: {e_test}')
            test_generation_msg = f'\nTest Generation Failed: {e_test}'
        relative_file_path = os.path.join('custom_tools', GENERATED_TOOLS_DIR_NAME, final_filename).replace('\\', '/')
        return f"Successfully generated tool code and applied syntax verification.\nSaved to: 'ai_assistant/{relative_file_path}'\nFunction: '{tool_function_name}'\n{reload_msg}{test_generation_msg}"
    except Exception as e:
        logger.error(f'Error in generate_new_tool_from_description: {e}', exc_info=True)
        return f'An unexpected error occurred during tool generation: {e}'

async def _generate_test_for_tool(tool_name: str, tool_filename: str, tool_code: str, action_executor: Optional['ActionExecutor']) -> str:
    """
    Generates a pytest file for the newly created tool.
    """
    if not tool_name:
        return 'Skipped (no tool name identified).'
    if not action_executor:
        return 'Skipped (no action_executor provided).'
    logger.info(f'Generating test for tool: {tool_name}')
    llm = None
    if hasattr(action_executor, 'llm_interface'):
        llm = action_executor.llm_interface
    elif hasattr(action_executor, 'code_service') and hasattr(action_executor.code_service, 'llm_provider'):
        llm = action_executor.code_service.llm_provider
    if not llm:
        return 'Skipped (LLM not available for test generation).'
    model_name = get_model_for_task('code_generation')
    module_name = tool_filename.replace('.py', '')
    prompt = f'\nYou are an expert QA engineer.\nYour task is to write a comprehensive `pytest` test suite for the following Python tool.\n\nTool Name: `{tool_name}`\nModule Name: `{module_name}`\nTool Source Code:\n```python\n{tool_code}\n```\n\nRequirements:\n1. Use `pytest`.\n2. The test file should import the tool from `ai_assistant.custom_tools.generated.{module_name}`.\n3. Include tests for:\n    - Normal operation (happy path).\n    - Edge cases (empty inputs, invalid types).\n    - Error handling (if the tool raises exceptions).\n4. Do NOT mock `action_executor` unless absolutely necessary (try to pass None or a simple MagicMock if needed).\n5. Output ONLY the Python code for the test file, enclosed in triple backticks.\n\nExample Import:\n`from ai_assistant.custom_tools.generated.{module_name} import {tool_name}`\n\nGenerate the test code now.\n'
    try:
        if hasattr(llm, 'send_request'):
            llm_response = await llm.send_request(prompt=prompt, model_name=model_name, temperature=0.2)
        elif hasattr(llm, 'invoke_ollama_model_async'):
            llm_response = await llm.invoke_ollama_model_async(prompt, model_name=model_name, temperature=0.2)
        else:
            return 'Failed (LLM method not found).'
        if not llm_response:
            return 'Failed (Empty LLM response).'
        code_match = re.search('```(?:python)?\\s*\\n(.*?)\\n```', llm_response, re.DOTALL | re.IGNORECASE)
        if not code_match:
            return 'Failed (No code block found in LLM response).'
        test_code = code_match.group(1).strip()
        test_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'tests', 'custom_tools')
        test_dir = os.path.abspath(test_dir)
        os.makedirs(test_dir, exist_ok=True)
        test_filename = f'test_{module_name}.py'
        test_filepath = os.path.join(test_dir, test_filename)
        with open(test_filepath, 'w', encoding='utf-8') as f:
            f.write(test_code)
        return f'Success! Saved to {test_filename}'
    except Exception as e:
        logger.error(f'Error generating test: {e}')
        return f'Error: {e}'
import importlib.util
import inspect
from typing import Optional, Dict, Any
try:
    from ai_assistant.core.tool_creator import get_generated_tools_dir
except ImportError:

    def get_generated_tools_dir():
        return get_generated_tools_path()
CUSTOM_TOOLS_DIR_PATH = os.path.dirname(__file__)
GENERATED_TOOLS_DIR_FOR_FINDER = None
try:
    GENERATED_TOOLS_DIR_FOR_FINDER = get_generated_tools_dir()
except Exception as e_get_dir:
    logger.warning(f'Warning: Could not dynamically get generated_tools_dir for find_agent_tool_source: {e_get_dir}. Fallback may be incorrect if paths changed.')
KNOWN_TOOL_DIRECTORIES = [CUSTOM_TOOLS_DIR_PATH, GENERATED_TOOLS_DIR_FOR_FINDER, get_generated_tools_path()]
KNOWN_TOOL_DIRECTORIES = [d for d in KNOWN_TOOL_DIRECTORIES if d and os.path.isdir(d)]
if not KNOWN_TOOL_DIRECTORIES:
    logger.error('Critical: No valid KNOWN_TOOL_DIRECTORIES could be determined for find_agent_tool_source.')

def find_agent_tool_source(tool_name: str) -> Optional[Dict[str, str]]:
    """
    Finds an agent tool's module path, function name, and its source code.
    Searches in known agent tool directories.

    Args:
        tool_name: The name of the tool (expected to match the .py file name and function name).

    Returns:
        A dictionary with "module_path", "function_name", "file_path", and "source_code",
        or None if the tool is not found or source cannot be retrieved.
    """
    if not tool_name.isidentifier():
        logger.warning(f"find_agent_tool_source: '{tool_name}' is not a valid Python identifier. Cannot be a tool name.")
        return None
    potential_filename = f'{tool_name}.py'
    for tool_dir_abs_path in KNOWN_TOOL_DIRECTORIES:
        if not tool_dir_abs_path:
            continue
        prospective_file_path = os.path.join(tool_dir_abs_path, potential_filename)
        if os.path.exists(prospective_file_path) and os.path.isfile(prospective_file_path):
            try:
                module_path_parts = []
                current_path = os.path.normpath(tool_dir_abs_path)
                path_parts = current_path.split(os.sep)
                try:
                    ai_assistant_index = path_parts.index('ai_assistant')
                    module_path_parts = path_parts[ai_assistant_index:]
                except ValueError:
                    logger.warning(f"Could not determine module path relative to 'ai_assistant' for {tool_dir_abs_path}. Using directory name.")
                    module_path_parts = [os.path.basename(tool_dir_abs_path)]
                full_module_name_for_spec = '.'.join(module_path_parts + [tool_name])
                module_spec = importlib.util.spec_from_file_location(full_module_name_for_spec, prospective_file_path)
                if module_spec and module_spec.loader:
                    module_obj = importlib.util.module_from_spec(module_spec)
                    module_spec.loader.exec_module(module_obj)
                    if hasattr(module_obj, tool_name):
                        function_obj = getattr(module_obj, tool_name)
                        source_code = inspect.getsource(function_obj)
                        return {'module_path': full_module_name_for_spec, 'function_name': tool_name, 'file_path': prospective_file_path, 'source_code': source_code.strip()}
                    else:
                        logger.warning(f"Tool function '{tool_name}' not found in module '{module_obj.__name__}' at '{prospective_file_path}'.")
            except Exception as e:
                logger.error(f"Could not load or inspect tool '{tool_name}' from '{prospective_file_path}': {e}", exc_info=True)
                pass
                pass
    logger.info(f"Direct file match failed for tool '{tool_name}'. Scanning known directories for definition...")
    for tool_dir_abs_path in KNOWN_TOOL_DIRECTORIES:
        if not tool_dir_abs_path or not os.path.exists(tool_dir_abs_path):
            continue
        try:
            for filename in os.listdir(tool_dir_abs_path):
                if filename.endswith('.py'):
                    file_path = os.path.join(tool_dir_abs_path, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        if f'def {tool_name}(' in content:
                            module_path_parts = []
                            current_path = os.path.normpath(tool_dir_abs_path)
                            path_parts = current_path.split(os.sep)
                            try:
                                ai_assistant_index = path_parts.index('ai_assistant')
                                module_path_parts = path_parts[ai_assistant_index:]
                            except ValueError:
                                module_path_parts = [os.path.basename(tool_dir_abs_path)]
                            full_module_name_for_spec = '.'.join(module_path_parts + [filename.replace('.py', '')])
                            source_match = re.search('def\\s+' + tool_name + '\\s*\\(.*?(?=\\n\\S|\\Z)', content, re.DOTALL)
                            try:
                                module_spec = importlib.util.spec_from_file_location(full_module_name_for_spec, file_path)
                                if module_spec and module_spec.loader:
                                    module_obj = importlib.util.module_from_spec(module_spec)
                                    module_spec.loader.exec_module(module_obj)
                                    if hasattr(module_obj, tool_name):
                                        function_obj = getattr(module_obj, tool_name)
                                        source_code = inspect.getsource(function_obj)
                                        return {'module_path': full_module_name_for_spec, 'function_name': tool_name, 'file_path': file_path, 'source_code': source_code.strip()}
                            except Exception as e_inner:
                                logger.warning(f'Found match in {filename} but failed to load: {e_inner}')
                                continue
                    except Exception as e_file:
                        logger.warning(f'Error reading {file_path}: {e_file}')
                        continue
        except Exception as e_dir:
            logger.error(f'Error scanning directory {tool_dir_abs_path}: {e_dir}')
            continue
    return None
FIND_AGENT_TOOL_SOURCE_SCHEMA = {'name': 'find_agent_tool_source', 'description': "Finds an existing agent tool's source code, module path, and file path. Searches in standard agent tool directories.", 'parameters': [tuple(sorted({'name': 'tool_name', 'type': 'str', 'description': "The name of the agent tool to find (e.g., 'my_calculator')."}.items()))], 'returns': tuple(sorted({'type': 'string', 'description': "A JSON string representing a dictionary with keys 'module_path', 'function_name', 'file_path', 'source_code', or null if not found."}.items()))}

def stage_agent_tool_modification(module_path: str, function_name: str, modified_code_string: str, change_description: str, original_reflection_entry_id: Optional[str]=None, tool_name_for_action: Optional[str]=None, modification_strategy: str='full_replace', target_node_pattern: Optional[str]=None) -> Dict[str, Any]:
    """
    Prepares a structured dictionary for proposing a modification to an existing agent tool.
    This dictionary is intended to be used by ActionExecutor with the 'PROPOSE_TOOL_MODIFICATION' action type.

    Args:
        module_path: The module path of the tool to be modified (e.g., "ai_assistant.custom_tools.my_tool").
        function_name: The name of the function within the module to be modified.
        modified_code_string: The complete new source code for the function, OR the replacement code snippet if strategy is 'surgical'.
        change_description: A description of why the change is being made or the user's request.
        original_reflection_entry_id: Optional. If the modification stems from a reflection log.
        tool_name_for_action: Optional. The 'tool_name' as known by the tool system (e.g. for display or logging).
                              If None, defaults to function_name.
        modification_strategy: "full_replace" (default) or "surgical_replace_node".
        target_node_pattern: Required if strategy is 'surgical_replace_node'. The code string to find and match for replacement.

    Returns:
        A dictionary structured for ActionExecutor's PROPOSE_TOOL_MODIFICATION action.
    """
    actual_tool_name = tool_name_for_action if tool_name_for_action else function_name
    action_details = {'module_path': module_path, 'function_name': function_name, 'tool_name': actual_tool_name, 'suggested_code_change': modified_code_string, 'suggested_change_description': change_description, 'modification_strategy': modification_strategy}
    if target_node_pattern:
        action_details['target_node_pattern'] = target_node_pattern
    if original_reflection_entry_id:
        action_details['original_reflection_entry_id'] = original_reflection_entry_id
    return {'action_type_for_executor': 'PROPOSE_TOOL_MODIFICATION', 'action_details_for_executor': action_details}
STAGE_AGENT_TOOL_MODIFICATION_SCHEMA = {'name': 'stage_agent_tool_modification', 'description': 'Stages the parameters needed to propose a modification to an existing agent tool. This prepares the information for the self-modification review and application process, typically for ActionExecutor. Supports both full function replacement and surgical node replacement.', 'parameters': [tuple(sorted({'name': 'module_path', 'type': 'str', 'description': "The module path of the tool (e.g., 'ai_assistant.custom_tools.my_tool')."}.items())), tuple(sorted({'name': 'function_name', 'type': 'str', 'description': 'The function name of the tool to modify.'}.items())), tuple(sorted({'name': 'modified_code_string', 'type': 'str', 'description': 'The complete new source code for the modified function, OR the replacement snippet for surgical edits.'}.items())), tuple(sorted({'name': 'change_description', 'type': 'str', 'description': 'Detailed description of the changes made or the reason for modification.'}.items())), tuple(sorted({'name': 'original_reflection_entry_id', 'type': 'str', 'description': 'Optional. The ID of the reflection entry that suggested this modification.'}.items())), tuple(sorted({'name': 'tool_name_for_action', 'type': 'str', 'description': "Optional. The 'tool_name' for logging/display in ActionExecutor, defaults to function_name."}.items())), tuple(sorted({'name': 'modification_strategy', 'type': 'str', 'description': "Optional. 'full_replace' (default) or 'surgical_replace_node'."}.items())), tuple(sorted({'name': 'target_node_pattern', 'type': 'str', 'description': 'Optional (Required for surgical). The existing code snippet to match and replace.'}.items()))], 'returns': tuple(sorted({'type': 'string', 'description': "A JSON string representing a dictionary containing 'action_type_for_executor': 'PROPOSE_TOOL_MODIFICATION' and 'action_details_for_executor': {details_dict}."}.items()))}
if __name__ == '__main__':
    print('--- Testing find_agent_tool_source ---')
    dummy_custom_tool_name = '_test_dummy_custom_tool_for_find'
    dummy_custom_tool_path = os.path.join(CUSTOM_TOOLS_DIR_PATH, f'{dummy_custom_tool_name}.py')
    with open(dummy_custom_tool_path, 'w') as f:
        f.write(f"def {dummy_custom_tool_name}(param1: str):\n    return f'Custom tool received: {{param1}}'")
    dummy_generated_tool_name = '_test_dummy_generated_tool_for_find'
    generated_dir_for_test = None
    if GENERATED_TOOLS_DIR_FOR_FINDER and os.path.isdir(GENERATED_TOOLS_DIR_FOR_FINDER):
        generated_dir_for_test = GENERATED_TOOLS_DIR_FOR_FINDER
    dummy_generated_tool_path = None
    if generated_dir_for_test:
        os.makedirs(generated_dir_for_test, exist_ok=True)
        dummy_generated_tool_path = os.path.join(generated_dir_for_test, f'{dummy_generated_tool_name}.py')
        with open(dummy_generated_tool_path, 'w') as f:
            f.write(f"def {dummy_generated_tool_name}():\n    return 'Generated tool reporting'")
    print(f'Searching for custom tool: {dummy_custom_tool_name}')
    found_custom = find_agent_tool_source(dummy_custom_tool_name)
    if found_custom:
        print(f"Found custom: {found_custom['file_path']}, Module: {found_custom['module_path']}")
        assert dummy_custom_tool_name in found_custom['source_code']
    else:
        print(f"Custom tool '{dummy_custom_tool_name}' not found. KNOWN_TOOL_DIRECTORIES: {KNOWN_TOOL_DIRECTORIES}")
        assert False, f'Failed to find {dummy_custom_tool_name}'
    if dummy_generated_tool_path:
        print(f'Searching for generated tool: {dummy_generated_tool_name}')
        found_generated = find_agent_tool_source(dummy_generated_tool_name)
        if found_generated:
            print(f"Found generated: {found_generated['file_path']}, Module: {found_generated['module_path']}")
            assert dummy_generated_tool_name in found_generated['source_code']
        else:
            print(f"Generated tool '{dummy_generated_tool_name}' not found. KNOWN_TOOL_DIRECTORIES: {KNOWN_TOOL_DIRECTORIES}")
            print('INFO: Generated tool find test might be sensitive to execution environment for get_generated_tools_dir().')
    else:
        print(f"Skipped generated tool test as directory '{GENERATED_TOOLS_DIR_FOR_FINDER}' was not valid or accessible.")
    print('Searching for non_existent_tool:')
    not_found = find_agent_tool_source('non_existent_tool_xyz_for_find')
    if not_found is None:
        print('Correctly did not find non_existent_tool_xyz_for_find.')
        assert True
    else:
        print(f'Incorrectly found non_existent_tool_xyz_for_find: {not_found}')
        assert False, 'Found a tool that should not exist.'
    if os.path.exists(dummy_custom_tool_path):
        os.remove(dummy_custom_tool_path)
    if dummy_generated_tool_path and os.path.exists(dummy_generated_tool_path):
        os.remove(dummy_generated_tool_path)
    print('--- Finished testing find_agent_tool_source ---')
    print('\n--- Testing stage_agent_tool_modification ---')
    staged_info = stage_agent_tool_modification(module_path='ai_assistant.custom_tools.calculator', function_name='add', modified_code_string='def add(a,b): return a+b+1 # new version', change_description='User requested to make add function increment by one more.', original_reflection_entry_id='reflect_123', tool_name_for_action='calculator_add_v2')
    print(f'Staged info: {staged_info}')
    assert staged_info['action_type_for_executor'] == 'PROPOSE_TOOL_MODIFICATION'
    assert staged_info['action_details_for_executor']['module_path'] == 'ai_assistant.custom_tools.calculator'
    assert staged_info['action_details_for_executor']['suggested_code_change'].endswith('# new version')
    assert staged_info['action_details_for_executor']['tool_name'] == 'calculator_add_v2'
    staged_info_no_optional = stage_agent_tool_modification(module_path='ai_assistant.custom_tools.helper', function_name='format_text', modified_code_string='def format_text(s): return s.strip()', change_description='Ensure stripping')
    print(f'Staged info (no optional): {staged_info_no_optional}')
    assert staged_info_no_optional['action_details_for_executor']['tool_name'] == 'format_text'
    assert 'original_reflection_entry_id' not in staged_info_no_optional['action_details_for_executor']
    print('--- Finished testing stage_agent_tool_modification ---')