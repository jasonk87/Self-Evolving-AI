import os
import sys
import tempfile
import subprocess
import shutil
import logging
from typing import Tuple, Dict, Any

logger = logging.getLogger(__name__)

class SandboxManager:
    """
    Manages isolated execution environments for validating AI-generated code.
    """
    
    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)
        
    def execute_test(self, original_module_path: str, modified_file_content: str, test_script_content: str, timeout: int = 30) -> Tuple[bool, str, str]:
        """
        Executes a test script against a modified version of a module in an isolated environment.
        
        Args:
            original_module_path: The python module path (e.g. 'ai_assistant.custom_tools.agent_tools')
            modified_file_content: The full source code of the module containing the new/fixed function.
            test_script_content: The pytest/unittest script to validate the changes.
            timeout: Maximum execution time in seconds.
            
        Returns:
            Tuple of (success_boolean, stdout_string, stderr_string)
        """
        temp_dir = tempfile.mkdtemp(prefix="weebo_sandbox_")
        
        try:
            # 1. Recreate the module structure inside the temp directory
            # E.g., tempdir/ai_assistant/custom_tools/agent_tools.py
            module_parts = original_module_path.split('.')
            module_dir = os.path.join(temp_dir, *module_parts[:-1])
            os.makedirs(module_dir, exist_ok=True)
            
            # Create __init__.py files along the path to make it a valid package
            current_path = temp_dir
            for part in module_parts[:-1]:
                current_path = os.path.join(current_path, part)
                init_file = os.path.join(current_path, '__init__.py')
                if not os.path.exists(init_file):
                    with open(init_file, 'w') as f:
                        f.write("")
            
            # Write the modified module
            mock_module_file = os.path.join(temp_dir, *module_parts) + ".py"
            with open(mock_module_file, 'w', encoding='utf-8') as f:
                f.write(modified_file_content)
                
            # 2. Write the test script into the temp directory root
            test_file = os.path.join(temp_dir, "test_sandbox.py")
            with open(test_file, 'w', encoding='utf-8') as f:
                f.write(test_script_content)
                
            # 3. Setup environment variables
            # Prioritize the temp_dir in PYTHONPATH so the modified module is loaded instead of the real one.
            env = os.environ.copy()
            existing_pythonpath = env.get("PYTHONPATH", "")
            # temp_dir first, then project_root (for other real dependencies), then existing
            new_pythonpath = f"{temp_dir}{os.pathsep}{self.project_root}"
            if existing_pythonpath:
                new_pythonpath += f"{os.pathsep}{existing_pythonpath}"
            env["PYTHONPATH"] = new_pythonpath
            
            # 4. Execute the test
            cmd = [sys.executable, "-m", "pytest", test_file, "-v", "--tb=short"]
            
            logger.info(f"Sandbox: Executing tests in isolated environment (module: {original_module_path})")
            
            process = subprocess.run(
                cmd,
                cwd=temp_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            success = process.returncode == 0
            return success, process.stdout, process.stderr
            
        except subprocess.TimeoutExpired as e:
            err_msg = f"Sandbox execution timed out after {timeout} seconds."
            logger.error(err_msg)
            return False, "", err_msg
        except Exception as e:
            err_msg = f"Sandbox framework error: {e}"
            logger.error(err_msg, exc_info=True)
            return False, "", err_msg
        finally:
            # Cleanup temp directory
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup sandbox directory {temp_dir}: {e}")
