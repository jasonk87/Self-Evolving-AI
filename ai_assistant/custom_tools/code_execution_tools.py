import subprocess
import sys
from typing import Dict, Any
import subprocess
import sys
from typing import Dict, Any
import re
import subprocess
import shlex
from typing import Dict, Any
import os
import subprocess
import tempfile
import json
from typing import Dict, Any, Optional, List
from ai_assistant.core.events import emit_system_event

def execute_sandboxed_python_script(script_content: str, input_files: Optional[Dict[str, str]]=None, output_filenames: Optional[List[str]]=None, timeout_seconds: int=10, python_executable: Optional[str]=None) -> Dict[str, Any]:
    """
    Executes a Python script in a temporary, somewhat isolated environment.
    WARNING: This is a basic PoC sandbox. Security is minimal and relies on OS permissions
    and the inherent sandboxing of running 'python' as a subprocess. It does NOT
    provide strong guarantees against malicious code. Use with extreme caution.

    Args:
        script_content: The Python script content as a string.
        input_files: Optional. A dictionary where keys are filenames and values are their string content.
                     These files will be created in the execution directory.
        output_filenames: Optional. A list of filenames expected to be created by the script,
                          whose content will be read and returned.
        timeout_seconds: Timeout for the script execution.
        python_executable: Optional path to the python interpreter. Defaults to "python".

    Returns:
        A dictionary containing:
            "status": "success", "timeout", or "error"
            "return_code": Integer return code of the script.
            "stdout": Captured standard output.
            "stderr": Captured standard error.
            "output_files": Dictionary of {filename: content} for requested output files.
            "error_message": Optional error message if status is "error".
            "executed_script_path": Path to the temporary script file.
    """
    emit_system_event('tool_status', {'tool': 'PythonExecutor', 'message': 'Preparing sandbox...', 'status': 'RUNNING'})
    if not script_content:
        return {'status': 'error', 'error_message': 'No script content provided.', 'return_code': -1, 'stdout': '', 'stderr': '', 'output_files': {}}
    interpreter = python_executable or 'python'
    if isinstance(input_files, str):
        try:
            input_files = json.loads(input_files)
        except json.JSONDecodeError:
            return {'status': 'error', 'error_message': f'Invalid input_files argument: Expected dict or JSON string, got invalid JSON: {input_files}', 'return_code': -1, 'stdout': '', 'stderr': '', 'output_files': {}}
    if isinstance(output_filenames, str):
        try:
            output_filenames = json.loads(output_filenames)
        except json.JSONDecodeError:
            return {'status': 'error', 'error_message': f'Invalid output_filenames argument: Expected list or JSON string, got invalid JSON: {output_filenames}', 'return_code': -1, 'stdout': '', 'stderr': '', 'output_files': {}}
    if input_files is not None and (not isinstance(input_files, dict)):
        return {'status': 'error', 'error_message': f'Invalid input_files argument: Expected dict, got {type(input_files).__name__}', 'return_code': -1, 'stdout': '', 'stderr': '', 'output_files': {}}
    if output_filenames is not None and (not isinstance(output_filenames, list)):
        return {'status': 'error', 'error_message': f'Invalid output_filenames argument: Expected list, got {type(output_filenames).__name__}', 'return_code': -1, 'stdout': '', 'stderr': '', 'output_files': {}}
    emit_system_event('tool_status', {'tool': 'PythonExecutor', 'message': 'Running script in isolation...', 'status': 'RUNNING'})
    with tempfile.TemporaryDirectory() as temp_dir_path:
        script_filename = 'main_script.py'
        script_file_path = os.path.join(temp_dir_path, script_filename)
        returned_executed_script_path = script_file_path
        try:
            with open(script_file_path, 'w', encoding='utf-8') as f:
                safety_wrapper = '\nimport builtins\nimport sys\n\ndef _safe_input(prompt=None):\n    raise RuntimeError("Interactive input is not supported in this environment. The script is attempting to read from stdin (e.g., using input()).")\n\nbuiltins.input = _safe_input\n'
                f.write(safety_wrapper + '\n' + script_content)
        except IOError as e:
            return {'status': 'error', 'error_message': f'Failed to write script to temp file: {e}', 'return_code': -1, 'stdout': '', 'stderr': '', 'output_files': {}}
        if input_files:
            for filename, content in input_files.items():
                if os.path.sep in filename or '..' in filename:
                    return {'status': 'error', 'error_message': f'Invalid input filename (contains path separators): {filename}', 'return_code': -1, 'stdout': '', 'stderr': '', 'output_files': {}}
                try:
                    input_file_path = os.path.join(temp_dir_path, filename)
                    with open(input_file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                except IOError as e:
                    return {'status': 'error', 'error_message': f"Failed to write input file '{filename}': {e}", 'return_code': -1, 'stdout': '', 'stderr': '', 'output_files': {}}
        stdout_val = ''
        stderr_val = ''
        error_msg_val = None
        try:
            process_result = subprocess.run([interpreter, '-I', '-s', '-S', script_filename], capture_output=True, text=True, timeout=timeout_seconds, cwd=temp_dir_path, check=False)
            emit_system_event('tool_status', {'tool': 'PythonExecutor', 'message': 'Execution finished.', 'status': 'COMPLETED'})
            stdout_val = process_result.stdout
            stderr_val = process_result.stderr
            return_code = process_result.returncode
            status = 'success' if return_code == 0 else 'error'
            if status == 'error' and (not stderr_val):
                error_msg_val = f'Script exited with code {return_code} but no stderr.'
            elif stderr_val:
                error_msg_val = stderr_val
        except subprocess.TimeoutExpired:
            status = 'timeout'
            return_code = -1
            stderr_val = f'Script execution timed out after {timeout_seconds} seconds.'
            error_msg_val = stderr_val
        except FileNotFoundError:
            status = 'error'
            return_code = -1
            stderr_val = f"Python interpreter '{interpreter}' not found. Please ensure it's in PATH or specify full path."
            error_msg_val = stderr_val
        except Exception as e:
            status = 'error'
            return_code = -1
            stderr_val = f'An unexpected error occurred during script execution: {str(e)}'
            error_msg_val = stderr_val
        collected_output_files = {}
        if output_filenames:
            for out_fname in output_filenames:
                if os.path.sep in out_fname or '..' in out_fname:
                    if stderr_val:
                        stderr_val += '\n'
                    stderr_val += f'Warning: Invalid output filename requested (contains path separators), skipped: {out_fname}'
                    continue
                out_fpath = os.path.join(temp_dir_path, out_fname)
                if os.path.exists(out_fpath) and os.path.isfile(out_fpath):
                    try:
                        with open(out_fpath, 'r', encoding='utf-8') as f_out:
                            collected_output_files[out_fname] = f_out.read()
                    except Exception as e_read_out:
                        if stderr_val:
                            stderr_val += '\n'
                        stderr_val += f"Warning: Could not read output file '{out_fname}': {e_read_out}"
                else:
                    if stderr_val:
                        stderr_val += '\n'
                    stderr_val += f"Warning: Requested output file '{out_fname}' not found in execution directory."
        return {'status': status, 'return_code': return_code, 'stdout': stdout_val.strip(), 'stderr': stderr_val.strip(), 'output_files': collected_output_files, 'error_message': error_msg_val.strip() if error_msg_val else None, 'executed_script_path': returned_executed_script_path}

def execute_safe_terminal_command(command: str) -> Dict[str, Any]:
    """
    Executes a terminal command if it is in the allowed whitelist.
    
    Args:
        command: The command string to execute.
        
    Returns:
        A dictionary containing "status", "stdout", "stderr", "return_code".
    """
    ALLOWED_PREFIXES = ['pip install', 'python -m pip install', 'python3 -m pip install', 'python3.11 -m pip install', 'ls', 'dir', 'echo', 'whoami']
    is_allowed = False
    clean_command = command.strip()
    for prefix in ALLOWED_PREFIXES:
        if clean_command.startswith(prefix):
            is_allowed = True
            break
    if not is_allowed:
        return {'status': 'error', 'error_message': f"Command not allowed: '{clean_command}'. Allowed prefixes: {ALLOWED_PREFIXES}", 'return_code': -1, 'stdout': '', 'stderr': ''}
    try:
        command_list = shlex.split(clean_command)
        process_result = subprocess.run(command_list, capture_output=True, text=True, timeout=120, shell=False)
        return {'status': 'success' if process_result.returncode == 0 else 'error', 'return_code': process_result.returncode, 'stdout': process_result.stdout.strip(), 'stderr': process_result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'error_message': 'Command execution timed out.', 'return_code': -1, 'stdout': '', 'stderr': ''}
    except FileNotFoundError as e:
        return {'status': 'error', 'error_message': f'Command not found: {str(e)}', 'return_code': -1, 'stdout': '', 'stderr': ''}
    except Exception as e:
        return {'status': 'error', 'error_message': f'Unexpected error: {str(e)}', 'return_code': -1, 'stdout': '', 'stderr': ''}
EXECUTE_SAFE_TERMINAL_COMMAND_SCHEMA = {'name': 'execute_safe_terminal_command', 'description': 'Executes a terminal command if it matches a strict allowlist (e.g., pip install). secure alternative to full shell access.', 'parameters': [{'name': 'command', 'type': 'str', 'description': 'The command string to execute.'}], 'returns': {'type': 'dict', 'description': "A dict with 'status', 'return_code', 'stdout', 'stderr', 'error_message'."}}

def install_python_package(package_name: str) -> Dict[str, Any]:
    """
    Installs a Python package using the current interpreter's pip module.
    
    Args:
        package_name: The name of the package to install.
        
    Returns:
        A dictionary containing "status", "stdout", "stderr", "return_code".
    """
    import sys
    if not package_name or not package_name.strip():
        return {'status': 'error', 'error_message': 'Package name cannot be empty.', 'return_code': -1, 'stdout': '', 'stderr': ''}
    clean_package_name = package_name.strip()
    import re
    if not re.match('^[a-zA-Z0-9_\\-\\.\\[\\]<>=!]+$', clean_package_name):
        return {'status': 'error', 'error_message': f"Invalid package name format: '{clean_package_name}'.", 'return_code': -1, 'stdout': '', 'stderr': ''}
    cmd = [sys.executable, '-m', 'pip', 'install', clean_package_name]
    try:
        process_result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return {'status': 'success' if process_result.returncode == 0 else 'error', 'return_code': process_result.returncode, 'stdout': process_result.stdout.strip(), 'stderr': process_result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'error_message': 'Package installation timed out.', 'return_code': -1, 'stdout': '', 'stderr': ''}
    except Exception as e:
        return {'status': 'error', 'error_message': f'Unexpected error during installation: {str(e)}', 'return_code': -1, 'stdout': '', 'stderr': ''}
INSTALL_PYTHON_PACKAGE_SCHEMA = {'name': 'install_python_package', 'description': "Installs a Python package using the current environment's pip.", 'parameters': [{'name': 'package_name', 'type': 'str', 'description': 'The name of the package to install.'}], 'returns': {'type': 'dict', 'description': "A dict with 'status', 'return_code', 'stdout', 'stderr'."}}
EXECUTE_SANDBOXED_PYTHON_SCRIPT_SCHEMA = {'name': 'execute_sandboxed_python_script', 'description': 'Executes a given Python script string in a temporary, somewhat isolated environment. WARNING: Basic PoC sandbox with minimal security. Use with extreme caution.', 'parameters': [{'name': 'script_content', 'type': 'str', 'description': 'The Python script content as a string.'}, {'name': 'input_files', 'type': 'dict', 'description': 'Optional. Filename:content map for files to create in the execution dir.'}, {'name': 'output_filenames', 'type': 'list', 'description': 'Optional. List of filenames expected to be created by the script, whose content will be returned.'}, {'name': 'timeout_seconds', 'type': 'int', 'description': 'Optional. Timeout for script execution (default 10s).'}, {'name': 'python_executable', 'type': 'str', 'description': "Optional. Path to python interpreter (e.g., 'python' or '/usr/bin/python3'). Defaults to 'python'."}], 'returns': {'type': 'dict', 'description': "A dict with 'status' ('success', 'timeout', 'error'), 'return_code', 'stdout', 'stderr', 'output_files' (dict), 'error_message'."}}

def run_all_tests() -> Dict[str, Any]:
    """
    Runs all tests available in the project by discovering and executing them.
    Typically runs 'pytest'.

    Returns:
        A dictionary containing the test results.
    """
    import sys
    try:
        process_result = subprocess.run([sys.executable, '-m', 'pytest'], capture_output=True, text=True, timeout=300)
        return {'status': 'success' if process_result.returncode == 0 else 'failure', 'return_code': process_result.returncode, 'stdout': process_result.stdout.strip(), 'stderr': process_result.stderr.strip()}
    except FileNotFoundError:
        return {'status': 'error', 'error_message': 'pytest not found. Please ensure it is installed.', 'return_code': -1, 'stdout': '', 'stderr': ''}
    except Exception as e:
        return {'status': 'error', 'error_message': f'Unexpected error running tests: {str(e)}', 'return_code': -1, 'stdout': '', 'stderr': ''}
RUN_ALL_TESTS_SCHEMA = {'name': 'run_all_tests', 'description': 'Runs all tests in the current environment using pytest.', 'parameters': [], 'returns': {'type': 'dict', 'description': 'Results of the test run including stdout/stderr.'}}
if __name__ == '__main__':
    print('--- Testing code_execution_tools.py ---')
    print('\n--- Testing execute_sandboxed_python_script ---')
    script1 = "print('Hello from sandbox')"
    res1 = execute_sandboxed_python_script(script1)
    print(f'Test 1 Output: {res1}')
    assert res1['status'] == 'success' and 'Hello from sandbox' in res1['stdout']
    script2 = "import sys; sys.stderr.write('Error message\\n'); sys.exit(1)"
    res2 = execute_sandboxed_python_script(script2)
    print(f'Test 2 Output: {res2}')
    assert res2['status'] == 'error' and 'Error message' in res2['stderr'] and (res2['return_code'] == 1)
    assert 'Error message' in res2['error_message']
    script3 = 'import time; time.sleep(3)'
    res3 = execute_sandboxed_python_script(script3, timeout_seconds=1)
    print(f'Test 3 Output: {res3}')
    assert res3['status'] == 'timeout'
    assert 'timed out' in res3['error_message']
    script4 = '\ntry:\n    with open(\'input.txt\', \'r\') as f_in:\n        content = f_in.read()\n    with open(\'output.txt\', \'w\') as f_out:\n        f_out.write(f"Read: {{content.strip()}}")\n    print("Script processed files.")\nexcept Exception as e_script:\n    print(f"Error in script4: {{e_script}}")\n'
    input_data = {'input.txt': 'Hello from input file!'}
    output_request = ['output.txt', 'non_existent_output.txt']
    res4 = execute_sandboxed_python_script(script4, input_files=input_data, output_filenames=output_request, timeout_seconds=2)
    print(f'Test 4 Output: {res4}')
    assert res4['status'] == 'success'
    assert 'Script processed files' in res4['stdout']
    assert 'output.txt' in res4['output_files']
    assert res4['output_files']['output.txt'] == 'Read: Hello from input file!'
    assert "Requested output file 'non_existent_output.txt' not found" in res4['stderr']
    res5 = execute_sandboxed_python_script('')
    print(f'Test 5 Output: {res5}')
    assert res5['status'] == 'error' and 'No script content' in res5['error_message']
    res6 = execute_sandboxed_python_script("print('test')", input_files={'../oops.txt': 'bad'})
    print(f'Test 6 Output: {res6}')
    assert res6['status'] == 'error' and 'Invalid input filename' in res6['error_message']
    script7 = '1/0'
    res7 = execute_sandboxed_python_script(script7)
    print(f'Test 7 Output: {res7}')
    assert res7['status'] == 'error'
    assert res7['return_code'] != 0
    assert 'ZeroDivisionError' in res7['stderr']
    assert 'ZeroDivisionError' in res7['error_message']
    print('--- Code Execution Tools Tests Finished ---')