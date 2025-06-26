import os
import subprocess
import tempfile
# import shutil # Not strictly needed if TemporaryDirectory handles all cleanup
from typing import Dict, Any, Optional, List, Tuple


def execute_sandboxed_python_script(
    script_content: str,
    input_files: Optional[Dict[str, str]] = None,
    output_filenames: Optional[List[str]] = None, # List of filenames expected as output
    timeout_seconds: int = 10,
    python_executable: Optional[str] = None # e.g., "python" or "/usr/bin/python3"
) -> Dict[str, Any]:
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
    if not script_content:
        return {"status": "error", "error_message": "No script content provided.", "return_code": -1, "stdout": "", "stderr": "", "output_files": {}}

    interpreter = python_executable or "python"

    with tempfile.TemporaryDirectory() as temp_dir_path:
        script_filename = "main_script.py"
        script_file_path = os.path.join(temp_dir_path, script_filename)

        returned_executed_script_path = script_file_path

        try:
            with open(script_file_path, 'w', encoding='utf-8') as f:
                f.write(script_content)
        except IOError as e: # pragma: no cover
            return {"status": "error", "error_message": f"Failed to write script to temp file: {e}", "return_code": -1, "stdout": "", "stderr": "", "output_files": {}}

        if input_files:
            for filename, content in input_files.items():
                if os.path.sep in filename or '..' in filename:
                    return {"status": "error", "error_message": f"Invalid input filename (contains path separators): {filename}", "return_code": -1, "stdout": "", "stderr": "", "output_files": {}}
                try:
                    input_file_path = os.path.join(temp_dir_path, filename)
                    with open(input_file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                except IOError as e: # pragma: no cover
                    return {"status": "error", "error_message": f"Failed to write input file '{filename}': {e}", "return_code": -1, "stdout": "", "stderr": "", "output_files": {}}

        stdout_val = ""
        stderr_val = ""
        error_msg_val = None

        try:
            process_result = subprocess.run(
                [interpreter, "-I", "-s", "-S", script_filename],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=temp_dir_path,
                check=False
            )
            stdout_val = process_result.stdout
            stderr_val = process_result.stderr
            return_code = process_result.returncode
            status = "success" if return_code == 0 else "error"
            if status == "error" and not stderr_val: # Some errors might not produce stderr but still have non-zero exit
                 error_msg_val = f"Script exited with code {return_code} but no stderr."
            elif stderr_val: # If there's stderr, it's likely the error message or part of it
                 error_msg_val = stderr_val


        except subprocess.TimeoutExpired: # pragma: no cover
            status = "timeout"
            return_code = -1
            stderr_val = f"Script execution timed out after {timeout_seconds} seconds."
            error_msg_val = stderr_val
        except FileNotFoundError: # pragma: no cover
            status = "error"
            return_code = -1
            stderr_val = f"Python interpreter '{interpreter}' not found. Please ensure it's in PATH or specify full path."
            error_msg_val = stderr_val
        except Exception as e: # pragma: no cover
            status = "error"
            return_code = -1
            stderr_val = f"An unexpected error occurred during script execution: {str(e)}"
            error_msg_val = stderr_val

        collected_output_files = {}
        if output_filenames:
            for out_fname in output_filenames:
                if os.path.sep in out_fname or '..' in out_fname:
                    if stderr_val: stderr_val += "\n"
                    stderr_val += f"Warning: Invalid output filename requested (contains path separators), skipped: {out_fname}"
                    continue

                out_fpath = os.path.join(temp_dir_path, out_fname)
                if os.path.exists(out_fpath) and os.path.isfile(out_fpath):
                    try:
                        with open(out_fpath, 'r', encoding='utf-8') as f_out:
                            collected_output_files[out_fname] = f_out.read()
                    except Exception as e_read_out: # pragma: no cover
                        if stderr_val: stderr_val += "\n"
                        stderr_val += f"Warning: Could not read output file '{out_fname}': {e_read_out}"
                else:
                    if stderr_val: stderr_val += "\n" # Add to stderr if file not found
                    stderr_val += f"Warning: Requested output file '{out_fname}' not found in execution directory."

        return {
            "status": status,
            "return_code": return_code,
            "stdout": stdout_val.strip(),
            "stderr": stderr_val.strip(),
            "output_files": collected_output_files,
            "error_message": error_msg_val.strip() if error_msg_val else None,
            "executed_script_path": returned_executed_script_path
        }

EXECUTE_SANDBOXED_PYTHON_SCRIPT_SCHEMA = {
   "name": "execute_sandboxed_python_script",
   "description": "Executes a given Python script string in a temporary, somewhat isolated environment. WARNING: Basic PoC sandbox with minimal security. Use with extreme caution.",
   "parameters": [
       {"name": "script_content", "type": "str", "description": "The Python script content as a string."},
       {"name": "input_files", "type": "dict", "description": "Optional. Filename:content map for files to create in the execution dir."},
       {"name": "output_filenames", "type": "list", "description": "Optional. List of filenames expected to be created by the script, whose content will be returned."},
       {"name": "timeout_seconds", "type": "int", "description": "Optional. Timeout for script execution (default 10s)."},
       {"name": "python_executable", "type": "str", "description": "Optional. Path to python interpreter (e.g., 'python' or '/usr/bin/python3'). Defaults to 'python'."}
   ],
   "returns": {
       "type": "dict",
       "description": "A dict with 'status' ('success', 'timeout', 'error'), 'return_code', 'stdout', 'stderr', 'output_files' (dict), 'error_message'."
   }
}

if __name__ == '__main__': # pragma: no cover
    print("--- Testing code_execution_tools.py ---")

    print("\n--- Testing execute_sandboxed_python_script ---")
    
    # Test 1: Simple print
    script1 = "print('Hello from sandbox')"
    res1 = execute_sandboxed_python_script(script1)
    print(f"Test 1 Output: {res1}")
    assert res1["status"] == "success" and "Hello from sandbox" in res1["stdout"]

    # Test 2: Stderr and non-zero exit
    script2 = "import sys; sys.stderr.write('Error message\\n'); sys.exit(1)"
    res2 = execute_sandboxed_python_script(script2)
    print(f"Test 2 Output: {res2}")
    assert res2["status"] == "error" and "Error message" in res2["stderr"] and res2["return_code"] == 1
    assert "Error message" in res2["error_message"]

    # Test 3: Timeout
    script3 = "import time; time.sleep(3)"
    res3 = execute_sandboxed_python_script(script3, timeout_seconds=1)
    print(f"Test 3 Output: {res3}")
    assert res3["status"] == "timeout"
    assert "timed out" in res3["error_message"]

    # Test 4: Input and Output files
    script4 = """
try:
    with open('input.txt', 'r') as f_in:
        content = f_in.read()
    with open('output.txt', 'w') as f_out:
        f_out.write(f"Read: {{content.strip()}}")
    print("Script processed files.")
except Exception as e_script:
    print(f"Error in script4: {{e_script}}")
"""
    input_data = {"input.txt": "Hello from input file!"}
    output_request = ["output.txt", "non_existent_output.txt"]
    res4 = execute_sandboxed_python_script(script4, input_files=input_data, output_filenames=output_request, timeout_seconds=2)
    print(f"Test 4 Output: {res4}")
    assert res4["status"] == "success"
    assert "Script processed files" in res4["stdout"]
    assert "output.txt" in res4["output_files"]
    assert res4["output_files"]["output.txt"] == "Read: Hello from input file!"
    assert "Requested output file 'non_existent_output.txt' not found" in res4["stderr"]

    # Test 5: No script content
    res5 = execute_sandboxed_python_script("")
    print(f"Test 5 Output: {res5}")
    assert res5["status"] == "error" and "No script content" in res5["error_message"]

    # Test 6: Invalid input filename
    res6 = execute_sandboxed_python_script("print('test')", input_files={"../oops.txt": "bad"})
    print(f"Test 6 Output: {res6}")
    assert res6["status"] == "error" and "Invalid input filename" in res6["error_message"]
    
    # Test 7: Script error without explicit stderr message but non-zero exit
    script7 = "1/0" # Raises ZeroDivisionError
    res7 = execute_sandboxed_python_script(script7)
    print(f"Test 7 Output: {res7}")
    assert res7["status"] == "error"
    assert res7["return_code"] != 0
    assert "ZeroDivisionError" in res7["stderr"] # Python's default traceback goes to stderr
    assert "ZeroDivisionError" in res7["error_message"]

    print("--- Code Execution Tools Tests Finished ---")
