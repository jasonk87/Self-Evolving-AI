import os
import platform
import subprocess
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from ai_assistant.tools.tool_system import tool_system_instance

class GetSystemInfoSchema(BaseModel):
    pass # No parameters needed

def get_system_info() -> Dict[str, Any]:
    """
    Retrieves the operating system's core technical information, including
    OS version, node name, architecture, and current Python environment details.
    """
    try:
        sys_info = {
            "system": platform.system(),
            "node_name": platform.node(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version()
        }

        # Add basic memory stats if on Linux or MacOS
        if platform.system() == "Linux":
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
                sys_info["memory_total"] = lines[0].strip().split()[1] + " kB"
                sys_info["memory_free"] = lines[1].strip().split()[1] + " kB"

        return {"success": True, "result": sys_info}
    except Exception as e:
        return {"success": False, "error_message": str(e)}


class AnalyzeClipboardSchema(BaseModel):
    pass # No parameters needed

def read_clipboard() -> Dict[str, Any]:
    """
    Reads the current text contents of the system clipboard.
    This allows the agent to process data the user recently copied (e.g. error logs, URLs).
    Requires xclip or xsel on Linux, pbpaste on macOS.
    """
    try:
        import pyperclip
    except ImportError:
        return {
            "success": False,
            "error_message": "pyperclip module is not installed. To use OS clipboard tools, run: pip install pyperclip"
        }

    try:
        text = pyperclip.paste()
        if not text:
             return {"success": True, "result": "Clipboard is empty or contains non-text data."}
        return {"success": True, "result": text[:5000]} # Limit size to prevent token explosion
    except Exception as e:
         return {"success": False, "error_message": f"Failed to read clipboard: {str(e)}"}

tool_system_instance.register_tool(
    tool_name="get_system_info",
    description="Reads detailed OS environment and hardware specs (Linux/Mac/Win). Use to determine how to run commands.",
    module_path=__name__,
    function_name_in_module="get_system_info",
    func_callable=get_system_info,
    pydantic_model=GetSystemInfoSchema
)

tool_system_instance.register_tool(
    tool_name="read_clipboard",
    description="Reads the user's active system clipboard contents (Ctrl+C text). Excellent for debugging copied errors.",
    module_path=__name__,
    function_name_in_module="read_clipboard",
    func_callable=read_clipboard,
    pydantic_model=AnalyzeClipboardSchema
)
