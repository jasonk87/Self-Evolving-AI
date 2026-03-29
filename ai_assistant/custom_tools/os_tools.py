import os
import platform
import base64
from io import BytesIO
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

class CaptureDesktopSchema(BaseModel):
    pass # No parameters needed

def capture_desktop_screenshot() -> Dict[str, Any]:
    """
    Captures a screenshot of the user's entire primary desktop monitor.
    Use this tool whenever the user asks you to 'look at my screen', 'read this error',
    or when you need visual context of their active workspace (e.g., their code editor).
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return {
            "success": False,
            "error_message": "Pillow (PIL) module is not installed. To use screen capture, run: pip install Pillow"
        }

    try:
        # Take the screenshot
        screenshot = ImageGrab.grab(all_screens=False) # Only grab primary to save tokens/bandwidth

        # Save it temporarily
        os.makedirs("ai_assistant/core/data/screenshots", exist_ok=True)
        import time
        filename = f"ai_assistant/core/data/screenshots/desktop_capture_{int(time.time())}.png"

        # Resize if massive (e.g., 4k monitor) to save LLM vision tokens
        max_size = (1920, 1080)
        screenshot.thumbnail(max_size)
        screenshot.save(filename, format="PNG")

        # Encode to base64 for LLM and PiP streaming
        buffered = BytesIO()
        screenshot.save(buffered, format="PNG")
        encoded_string = base64.b64encode(buffered.getvalue()).decode('utf-8')

        return {
            "success": True,
            "result": f"Successfully captured desktop screenshot and saved to {filename}. Analyze the visual contents.",
            "base64_image": encoded_string,
            "filename": filename
        }

    except Exception as e:
         return {"success": False, "error_message": f"Failed to capture desktop screen: {str(e)}"}

tool_system_instance.register_tool(
    tool_name="capture_desktop_screenshot",
    description="Takes a live screenshot of the user's actual desktop monitor. Essential for seeing what the user is looking at (like code errors or active apps).",
    module_path=__name__,
    function_name_in_module="capture_desktop_screenshot",
    func_callable=capture_desktop_screenshot,
    pydantic_model=CaptureDesktopSchema
)

class RequestFileSelectionSchema(BaseModel):
    title: Optional[str] = Field(default="Select a file", description="The prompt or title shown at the top of the OS file dialog window.")

def request_user_file_selection(title: str = "Select a file") -> Dict[str, Any]:
    """
    Pauses execution and opens a native OS graphical file browser (like Finder or Windows Explorer).
    The user can physically click and select a file. The absolute path to that file is then returned to you.
    Use this whenever the user says 'this file' or asks you to edit something but doesn't give a clear path.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return {
            "success": False,
            "error_message": "Tkinter module is not installed. The OS file dialog cannot be opened."
        }

    try:
        # Create a headless Tkinter root
        root = tk.Tk()
        root.withdraw()

        # Keep window on top so the user sees it
        root.attributes('-topmost', True)

        # Open the native OS file dialog
        file_path = filedialog.askopenfilename(title=title, parent=root)

        # Clean up
        root.destroy()

        if not file_path:
            return {"success": False, "error_message": "User canceled the file selection dialog."}

        return {
            "success": True,
            "result": file_path
        }

    except Exception as e:
         return {"success": False, "error_message": f"Failed to open native OS file dialog: {str(e)}"}

tool_system_instance.register_tool(
    tool_name="request_user_file_selection",
    description="Opens a physical OS file explorer window (Finder/Windows Explorer) forcing the user to select a file visually. Returns the absolute path of their selection.",
    module_path=__name__,
    function_name_in_module="request_user_file_selection",
    func_callable=request_user_file_selection,
    pydantic_model=RequestFileSelectionSchema
)
