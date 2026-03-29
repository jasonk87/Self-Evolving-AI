import os
import platform
import subprocess
import webbrowser
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from ai_assistant.tools.tool_system import tool_system_instance

# ----------------------------------------------------------------------------
# Tool: Open URL in Native Browser
# ----------------------------------------------------------------------------
class OpenUrlSchema(BaseModel):
    url: str = Field(description="The full HTTP/HTTPS URL to open in the user's default desktop browser.")

def open_url_in_browser(url: str) -> Dict[str, Any]:
    """
    Opens a specific URL in the user's actual default desktop web browser (Chrome/Edge/Safari).
    This is active OS automation, not background scraping. It physically opens a tab for the user to see.
    """
    try:
        # Ensure it has a protocol
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url

        success = webbrowser.open(url)
        if success:
            return {"success": True, "result": f"Successfully opened {url} in the native desktop browser."}
        else:
            return {"success": False, "error_message": f"webbrowser.open returned False for {url}"}
    except Exception as e:
        return {"success": False, "error_message": f"Failed to open browser: {str(e)}"}


# ----------------------------------------------------------------------------
# Tool: Launch Desktop Application
# ----------------------------------------------------------------------------
class LaunchAppSchema(BaseModel):
    app_name: str = Field(description="The name of the application or executable to launch (e.g., 'notepad', 'code', 'calculator', 'safari').")
    file_path: Optional[str] = Field(default=None, description="An optional absolute path to a file to open with the application.")

def launch_application(app_name: str, file_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Launches a local desktop application or executable on the user's machine.
    Optionally passes a file path for the application to open immediately.
    """
    system = platform.system()
    command = []

    try:
        if system == "Darwin": # macOS
            command = ["open", "-a", app_name]
            if file_path:
                command.append(file_path)

        elif system == "Windows":
            # On Windows, 'start' is a shell builtin, so we use shell=True.
            # If a file is provided, we can try to launch the app against it.
            if file_path:
                command = f"start {app_name} \"{file_path}\""
            else:
                command = f"start {app_name}"

        elif system == "Linux":
            # On Linux, try to run the executable directly in the background
            command = [app_name]
            if file_path:
                command.append(file_path)
        else:
            return {"success": False, "error_message": f"Unsupported OS: {system}"}

        # Execute
        if system == "Windows":
            subprocess.Popen(command, shell=True)
        else:
            # We use Popen so we don't block the agent waiting for the app to close
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        msg = f"Successfully launched '{app_name}'."
        if file_path:
            msg += f" Requested to open file: '{file_path}'"

        return {"success": True, "result": msg}

    except FileNotFoundError:
        return {"success": False, "error_message": f"Could not find application or executable named '{app_name}'. It may not be in the system PATH."}
    except Exception as e:
        return {"success": False, "error_message": f"Failed to launch application: {str(e)}"}

# ----------------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------------
tool_system_instance.register_tool(
    tool_name="open_url_in_browser",
    description="Physically opens a website tab in the user's actual desktop web browser (e.g., Chrome/Safari) so they can see it.",
    module_path=__name__,
    function_name_in_module="open_url_in_browser",
    func_callable=open_url_in_browser,
    pydantic_model=OpenUrlSchema
)

tool_system_instance.register_tool(
    tool_name="launch_application",
    description="Launches a desktop application or executable on the user's local machine, optionally opening a specific file with it.",
    module_path=__name__,
    function_name_in_module="launch_application",
    func_callable=launch_application,
    pydantic_model=LaunchAppSchema
)
