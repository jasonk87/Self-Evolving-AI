
from ai_assistant.custom_tools.code_execution_tools import execute_sandboxed_python_script

script = """
import sys
import site
print(f"Sys Path includes site-packages: {any('site-packages' in p for p in sys.path)}")
try:
    import json
    print("Standard lib json imported")
except ImportError:
    print("Standard lib import failed")
"""

result = execute_sandboxed_python_script(script)
print("Result:")
print(result)
