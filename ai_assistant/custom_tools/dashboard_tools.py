# ai_assistant/custom_tools/dashboard_tools.py

from ai_assistant.tools.tool_system import tool_system_instance
from ai_assistant.core.reflection import global_reflection_log

def get_system_status() -> dict:
    """
    Retrieves the current status of the AI system, including the number of loaded tools and reflection log entries.

    Returns:
        dict: A dictionary containing system status information.
    """
    try:
        tool_count = len(tool_system_instance.list_tools())
    except Exception:
        tool_count = -1 # Indicate error

    try:
        log_entry_count = len(global_reflection_log.get_entries())
    except Exception:
        log_entry_count = -1 # Indicate error

    return {
        "status": "OK",
        "tool_count": tool_count,
        "reflection_log_entries": log_entry_count
    }

def format_status_as_html(status_dict: dict) -> str:
    """
    Formats the system status dictionary into an HTML string.

    Args:
        status_dict (dict): The dictionary returned by get_system_status.

    Returns:
        str: An HTML string representing the system status.
    """
    if not isinstance(status_dict, dict):
        return "<p>Error: Invalid status data provided.</p>"

    tool_count = status_dict.get('tool_count', 'N/A')
    log_entries = status_dict.get('reflection_log_entries', 'N/A')

    html = f"""
    <p><strong>Total Tools Loaded:</strong> {tool_count}</p>
    <p><strong>Reflection Log Entries:</strong> {log_entries}</p>
    <p><small>Last updated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</small></p>
    """
    return html.strip()

def get_dashboard_html() -> str:
    """
    Returns the HTML and JavaScript for the interactive System Status Dashboard.

    Returns:
        str: A string containing the full HTML for the dashboard.
    """
    html_content = """
<div style="font-family: sans-serif; padding: 15px; border: 1px solid #ccc; border-radius: 8px; background-color: #f9f9f9;">
    <h2>System Status Dashboard</h2>
    <p>Click the button to get the latest system status from the AI.</p>
    <button id="refresh-button" onclick="refreshStatus()">Refresh Status</button>
    <hr style="margin: 15px 0;">
    <h4>Live Status:</h4>
    <div id="status-content" style="padding: 10px; border: 1px dashed #ddd; min-height: 50px; background-color: #fff;">
        <p><i>Click "Refresh Status" to begin...</i></p>
    </div>
</div>

<script>
    function refreshStatus() {
        const button = document.getElementById('refresh-button');
        const statusDiv = document.getElementById('status-content');

        console.log("Dashboard: Refresh button clicked.");
        // We no longer need to set a placeholder here, because the AI will replace the whole div content.
        // However, showing some feedback is good UX.
        statusDiv.innerHTML = "<p><i>Requesting status from AI...</i></p>";
        button.disabled = true;

        const messagePayload = {
            type: 'chat_prompt_request',
            prompt: 'Update system status dashboard'
        };

        // Send the message to the parent window where the main app's script.js is listening
        window.parent.postMessage(messagePayload, '*');

        // Re-enable the button after a timeout. A more robust solution would
        // wait for a confirmation message back from the parent window.
        setTimeout(() => {
            button.disabled = false;
        }, 4000);
    }
</script>
"""
    return html_content
