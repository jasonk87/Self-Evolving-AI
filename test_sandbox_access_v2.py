
from ai_assistant.custom_tools.code_execution_tools import execute_sandboxed_python_script
import sys

script = """
import sys
print(f"Sys Path: {sys.path}")
print(f"Has site-packages: {any('site-packages' in p for p in sys.path)}")
"""

result = execute_sandboxed_python_script(script)
print(f"STDOUT START\n{result.get('stdout')}\nSTDOUT END")
print(f"STDERR START\n{result.get('stderr')}\nSTDERR END")
