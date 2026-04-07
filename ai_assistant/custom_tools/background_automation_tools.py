import os
import time
import uuid
import logging
import threading
import subprocess
import shlex
from typing import Dict, Any, List
from pydantic import BaseModel, Field

from ai_assistant.tools.tool_system import tool_system_instance

logger = logging.getLogger(__name__)

# Global registry to keep watcher threads alive and trackable
ACTIVE_WATCHERS: Dict[str, Any] = {}

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
except ImportError:
    Observer = None
    FileSystemEventHandler = object # Dummy class if not installed
    logger.warning("Watchdog not installed. Background automation tools disabled.")

# ----------------------------------------------------------------------------
# Tool: Start Directory Watcher
# ----------------------------------------------------------------------------
class StartWatcherSchema(BaseModel):
    directory_path: str = Field(description="The absolute path of the directory to monitor (e.g., /home/user/Downloads).")
    file_extension: str = Field(description="The file extension to trigger on (e.g., '.pdf', '.png'). Use '.*' for all files.")
    action_command: str = Field(description="The bash or system command to execute when a file matches. Use the variable '{filepath}' which will be replaced with the exact file that triggered the event. Example: 'mv {filepath} /home/user/Documents/Invoices/'")

class CustomFileActionHandler(FileSystemEventHandler):
    def __init__(self, extension: str, command_template: str):
        self.extension = extension.lower()
        self.command_template = command_template
        super().__init__()

    def _execute_action(self, filepath: str):
        # Ignore directories and files that don't match the extension (unless .*)
        if self.extension != ".*" and not filepath.lower().endswith(self.extension):
            return

        try:
            # Safely replace the {filepath} variable using shlex.quote to prevent command injection
            safe_filepath = shlex.quote(filepath)
            final_command = self.command_template.replace("{filepath}", safe_filepath)
            logger.info(f"Watcher triggered on {filepath}. Executing: {final_command}")

            # Execute as a detached background process so it doesn't block the watcher
            subprocess.Popen(final_command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.error(f"Watcher action failed for {filepath}: {e}")

    # Trigger on both created and moved (in case a file is downloaded to a temp name and then renamed to .pdf)
    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self._execute_action(event.src_path)

    def on_moved(self, event: FileSystemEvent):
        if not event.is_directory:
            self._execute_action(event.dest_path)

def start_directory_watcher(directory_path: str, file_extension: str, action_command: str) -> Dict[str, Any]:
    """
    Spawns a headless background sub-agent (a filesystem observer thread) that continuously monitors a specific directory.
    When a file matching the extension is added, it automatically executes your specified bash command.
    """
    if Observer is None:
        return {"success": False, "error_message": "The 'watchdog' Python library is not installed on the system. Run: pip install watchdog"}

    if not os.path.exists(directory_path) or not os.path.isdir(directory_path):
        return {"success": False, "error_message": f"Directory '{directory_path}' does not exist or is not a directory."}

    if "{filepath}" not in action_command:
         return {"success": False, "error_message": "The action_command MUST contain the string '{filepath}' so the background agent knows which file to operate on."}

    try:
        # Create unique ID for this watcher task
        watcher_id = f"watcher_{uuid.uuid4().hex[:8]}"

        # Setup watchdog observer
        event_handler = CustomFileActionHandler(file_extension, action_command)
        observer = Observer()
        observer.schedule(event_handler, directory_path, recursive=False)

        # Start in a background thread attached to the OS process
        observer.start()

        # Register it globally so we can stop it later if requested
        ACTIVE_WATCHERS[watcher_id] = {
            "directory": directory_path,
            "extension": file_extension,
            "command": action_command,
            "observer": observer
        }

        return {
            "success": True,
            "result": f"Successfully spawned headless background agent '{watcher_id}'. It is now silently watching '{directory_path}' for '{file_extension}' files and will automatically run your command."
        }

    except Exception as e:
        return {"success": False, "error_message": f"Failed to start directory watcher: {str(e)}"}


# ----------------------------------------------------------------------------
# Tool: List Background Automation Tasks
# ----------------------------------------------------------------------------
class ListWatchersSchema(BaseModel):
    pass

def list_active_watchers() -> Dict[str, Any]:
    """
    Returns a list of all currently running headless background automation agents (directory watchers)
    spawned by the system during this session.
    """
    if not ACTIVE_WATCHERS:
        return {"success": True, "result": "No background watcher agents are currently running."}

    watchers_info = []
    for wid, w_data in ACTIVE_WATCHERS.items():
        watchers_info.append(f"- ID: {wid} | Directory: {w_data['directory']} | Target: {w_data['extension']} | Action: {w_data['command']}")

    return {"success": True, "result": "Active Background Agents:\n" + "\n".join(watchers_info)}


# ----------------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------------
tool_system_instance.register_tool(
    tool_name="start_directory_watcher",
    description="Deploys a headless background OS agent (daemon) to continuously monitor a folder for new files matching an extension, instantly executing a bash command when triggered. Perfect for 'move this PDF when it downloads' chores.",
    module_path=__name__,
    function_name_in_module="start_directory_watcher",
    func_callable=start_directory_watcher,
    pydantic_model=StartWatcherSchema
)

tool_system_instance.register_tool(
    tool_name="list_active_watchers",
    description="Lists all running background folder watchers and automated daemons deployed on the OS.",
    module_path=__name__,
    function_name_in_module="list_active_watchers",
    func_callable=list_active_watchers,
    pydantic_model=ListWatchersSchema
)
