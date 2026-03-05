
from flask import request, jsonify, Response
from . import api_bp
import logging
import os
import sys
import subprocess
import threading
import app_globals
import ai_assistant.config as config
from ai_assistant.core.project_manager import find_project
from ai_assistant.core.background_service import report_user_activity
from ai_assistant.core.shutdown_manager import shutdown_manager
from ai_assistant.voice.tts import generate_speech
from ai_assistant.core.telemetry import telemetry_tracker

logger = logging.getLogger(__name__)

@api_bp.route('/system/telemetry', methods=['GET'])
def get_telemetry_status():
    """Returns the current token usage telemetry."""
    return jsonify({
        "success": True,
        "usage": telemetry_tracker.get_usage(),
        "history": telemetry_tracker.get_history(limit=50) # Return recent history for breakdown
    })

@api_bp.route('/config', methods=['GET'])
def get_config():
    """Returns the current system configuration."""
    return jsonify(app_globals.config_manager.get_all_settings())

@api_bp.route('/config/schema', methods=['GET'])
def get_config_schema():
    """Returns metadata for editable configuration settings."""
    return jsonify({
        "success": True,
        "schema_version": 1,
        "settings": app_globals.config_manager.get_settings_schema(),
    })

@api_bp.route('/config', methods=['POST'])
def update_config():
    """Updates system configuration."""
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "errors": ["Payload must be a JSON object."]}), 400

    success = True
    errors = []
    updated = {}

    for key, raw_value in data.items():
        try:
            value = app_globals.config_manager.coerce_setting_value(key, raw_value)
        except Exception as e:
            success = False
            errors.append(f"{key}: {e}")
            continue

        if not app_globals.config_manager.update_setting(key, value):
            success = False
            errors.append(f"Failed to update {key}")
            continue

        updated[key] = value

    return jsonify({"success": success, "errors": errors, "updated": updated}), (200 if success else 400)

@api_bp.route('/run', methods=['POST'])
def run_script():
    """Executes a Python script."""
    report_user_activity() # Signal user activity
    data = request.json
    path = data.get('path')

    if not path or not path.startswith('projects/'):
        return jsonify({"error": "Invalid path format. Must start with 'projects/'", "success": False}), 400

    # Parse project name and relative path
    # Expected format: "projects/<project_name>/<relative_path>"
    try:
        parts = path.split('/', 2)
        if len(parts) < 3:
             return jsonify({"error": "Invalid path format. Missing project name or file path.", "success": False}), 400
        
        project_name = parts[1]
        file_relative_path = parts[2]
    except Exception as e:
        return jsonify({"error": f"Failed to parse path: {e}", "success": False}), 400

    try:
        # Use find_project to get the true root path
        # from ai_assistant.core.project_manager import find_project
        project = find_project(project_name)

        if not project:
            return jsonify({"error": f"Project '{project_name}' not found.", "success": False}), 404
        
        root_path = project.get('root_path')
        if not root_path or not os.path.exists(root_path):
            return jsonify({"error": f"Project root path invalid for '{project_name}'.", "success": False}), 500

        # Construct full path
        full_path = os.path.abspath(os.path.join(root_path, file_relative_path))

        # Security check: ensure path is within root_path
        if not full_path.startswith(os.path.abspath(root_path)):
             return jsonify({"error": "Access denied: Path traversal detected.", "success": False}), 403

        if not os.path.exists(full_path):
            return jsonify({"error": f"File not found: {full_path}", "success": False}), 404

        # determine cwd (script's directory)
        cwd = os.path.dirname(full_path)

        # Execute
        result = subprocess.run(
            [sys.executable, full_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr

        if not output:
             output = "Script finished with no output."

        return jsonify({"output": output, "success": True})

    except subprocess.TimeoutExpired:
        return jsonify({"output": "Error: Execution timed out (limit: 60s)", "success": False}), 200
    except Exception as e:
        logger.error(f"Error executing script {path}: {e}")
        return jsonify({"output": f"Error: {str(e)}", "success": False}), 500

@api_bp.route('/terminal/exec', methods=['POST'])
def exec_terminal_command():
    """Executes a shell command directly."""
    report_user_activity() # Signal user activity
    
    # Security Check: SAFE_MODE
    if config.SAFE_MODE:
        return jsonify({"error": "Safe Mode is ENABLED. Arbitrary command execution is blocked.", "success": False}), 403

    data = request.json
    command = data.get('command')
    project_name = data.get('project_name')
    
    if not command:
        return jsonify({"error": "Command is required", "success": False}), 400

    # Default to project root equivalent logic (cwd)
    # We can't easily get 'project_root' of the main app here without importing from config or something
    # But usually this is for projects.
    cwd = os.getcwd() # Default
    
    if project_name:
         try:
             project = find_project(project_name)
             if project and project.get('root_path'):
                 cwd = project.get('root_path')
         except Exception as e:
             logger.warning(f"Could not resolve project path for {project_name}: {e}")

    try:
        # Use shell=True to allow complex commands (pipes, etc.) - Security Risk if public, but this is local user app.
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd
        )
        
        return jsonify({
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "cwd": cwd,
            "success": True
        })

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Execution timed out", "success": False}), 408
    except Exception as e:
        logger.error(f"Error executing command '{command}': {e}")
        return jsonify({"error": str(e), "success": False}), 500

@api_bp.route('/speak', methods=['POST'])
def api_speak():
    data = request.json
    text = data.get('text')
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    audio_data = generate_speech(text)
    if audio_data:
        return Response(audio_data, mimetype="audio/mpeg")
    else:
        return jsonify({"error": "TTS generation failed"}), 500

@api_bp.route('/system/quarantine', methods=['GET'])
def get_quarantine_status():
    """Returns the current list of blocked tools."""
    if not app_globals.orchestrator:
        return jsonify({"error": "Orchestrator not initialized", "success": False}), 500

    blocked_tools = app_globals.orchestrator.get_blocked_tools()
    return jsonify({"success": True, "blocked_tools": blocked_tools})

@api_bp.route('/system/quarantine/unblock', methods=['POST'])
def unblock_quarantined_tool():
    """Manually unblocks a specific quarantined tool."""
    if not app_globals.orchestrator:
        return jsonify({"error": "Orchestrator not initialized", "success": False}), 500

    data = request.json or {}
    tool_name = data.get('tool_name')

    if not tool_name:
        return jsonify({"error": "tool_name is required", "success": False}), 400

    success = app_globals.orchestrator.unblock_tool(tool_name)
    if success:
        return jsonify({"success": True, "message": f"Tool '{tool_name}' unblocked."})
    else:
        return jsonify({"success": False, "error": f"Tool '{tool_name}' not found in quarantine."}), 404

@api_bp.route('/system/shutdown', methods=['POST'])
def system_shutdown():
    """Shuts down the server gracefully."""
    logger.info("Shutdown requested via API.")
    
    def shutdown_server():
        # Delay slightly to allow response to be sent
        import time
        time.sleep(1)
        
        # Signal shutdown to stop new tasks
        shutdown_manager.request_shutdown(timeout_seconds=240)
        
        # Graceful wait loop (Max 4 minutes)
        timeout = 240
        start_time = time.time()
        logger.info(f"Shutdown: Waiting up to {timeout}s for active tasks to complete...")

        while time.time() - start_time < timeout:
            try:
                # Check for active tasks
                active_tasks = app_globals.task_manager.list_active_tasks()
                if not active_tasks:
                    logger.info("Shutdown: No active tasks remaining. Proceeding to exit.")
                    break
                
                if (int(time.time()) % 5) == 0: # Log every 5 seconds
                    logger.info(f"Shutdown: Waiting for {len(active_tasks)} task(s) to finish...")
                
                time.sleep(1)
            except Exception as e:
                logger.error(f"Shutdown: Error checking tasks: {e}")
                # If we cant check tasks, we should probably just exit to avoid hanging forever
                break
        
        if time.time() - start_time >= timeout:
            logger.warning("Shutdown: Timed out waiting for active tasks. Forcing exit.")

        # Force exit
        os._exit(0)

    # Run shutdown in a separate thread so this request can return 200 OK
    threading.Thread(target=shutdown_server).start()
    return jsonify({"success": True, "message": "System shutting down gracefully..."})
