import json
from datetime import datetime
import os
import logging

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

REMINDER_FILE = "reminders.json"

def set_reminder(reminder_time: str, reminder_message: str) -> str:
    """
    Sets a reminder for the user at a specific time with a specific message.
    It stores the reminder for later use, as Weebo does not currently have the ability to alert the user directly.

    Args:
        reminder_time (str): The time to set the reminder for, in ISO 8601 format (e.g., 2024-01-01T12:00:00Z).
        reminder_message (str): The message to remind the user about.

    Returns:
        str: A message indicating whether the reminder was successfully set or if an error occurred.
    """
    try:
        datetime.fromisoformat(reminder_time.replace('Z', '+00:00')) #validate ISO format

        # Load existing reminders
        if os.path.exists(REMINDER_FILE):
            with open(REMINDER_FILE, "r") as f:
                try:
                    reminders = json.load(f)
                except json.JSONDecodeError:
                    reminders = []
        else:
            reminders = []

        # Add the new reminder
        reminders.append({"time": reminder_time, "message": reminder_message})

        # Save the updated reminders
        with open(REMINDER_FILE, "w") as f:
            json.dump(reminders, f)

        return f"Reminder set for {reminder_time} with message: {reminder_message}"
    except ValueError as e:
        logger.error(f"Invalid reminder time format: {e}")
        return f"Error: Invalid reminder time format. Please use ISO 8601 format (e.g., 2024-01-01T12:00:00Z)."
    except Exception as e:
        logger.error(f"Could not set reminder: {e}")
        return f"Error: Could not set reminder. {e}"