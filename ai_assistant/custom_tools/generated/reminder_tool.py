import datetime
import platform
import subprocess
from typing import Optional


def set_reminder(reminder_text: str, reminder_time: str) -> str:
    """
    Sets a reminder using the operating system's built-in functionality.

    Args:
        reminder_text (str): The text of the reminder.
        reminder_time (str): The time for the reminder, in a format that can be parsed by datetime.fromisoformat (e.g., "2023-12-25T10:00:00").

    Returns:
        str: A message indicating whether the reminder was set successfully or if an error occurred.
    """
    try:
        reminder_datetime = datetime.datetime.fromisoformat(reminder_time)
    except ValueError:
        return "Error: Invalid reminder time format. Please use ISO format (e.g., 2023-12-25T10:00:00)."

    system = platform.system()

    try:
        if system == "Darwin":  # macOS
            # Use AppleScript to create a reminder
            script = f"""
            tell application "Reminders"
                set newReminder to make new reminder with properties {{name:"{reminder_text}", remind me date:date "{reminder_datetime.strftime("%Y-%m-%d %H:%M:%S")}"}}
                tell newReminder to set body to "{reminder_text}"
            end tell
            """
            subprocess.run(["osascript", "-e", script], check=True)
            return "Reminder set successfully on macOS."
        elif system == "Linux":
            # Use notify-send to create a notification (not a persistent reminder)
            subprocess.run(["notify-send", "Reminder", reminder_text], check=True)
            return "Notification sent successfully on Linux (not a persistent reminder)."
        elif system == "Windows":
            # This is a placeholder. Windows reminder functionality is more complex and requires additional libraries or external tools.
            return "Error: Reminder functionality not yet implemented for Windows."
        else:
            return f"Error: Unsupported operating system: {system}."
    except Exception as e:
        return f"Error setting reminder: {e}"


Suggested Filename: reminder_tool.py