import asyncio
import datetime
import time
import threading
from typing import Optional

def schedule_reminder(reminder_time: Optional[str] = None, message: str = "", time: Optional[str] = None) -> str:
    # Support 'time' as alias for 'reminder_time' for robustness
    if reminder_time is None and time is not None:
        reminder_time = time
    
    if not reminder_time:
        return "Error: Missing required argument 'reminder_time' (or 'time')."
    """Schedules a reminder to be displayed at a specific time with a custom message.

    Args:
        reminder_time (str): The time at which the reminder should be displayed, in HH:MM format (e.g., "14:30").
        message (str): The message to be displayed when the reminder triggers.

    Returns:
        str: A message indicating whether the reminder was successfully scheduled or if an error occurred.
    """
    try:
        now = datetime.datetime.now()
        reminder_hour, reminder_minute = map(int, reminder_time.split(':'))

        reminder_datetime = now.replace(hour=reminder_hour, minute=reminder_minute, second=0, microsecond=0)

        if reminder_datetime <= now:
            reminder_datetime = reminder_datetime + datetime.timedelta(days=1)

        wait_time = (reminder_datetime - now).total_seconds()

        def display_reminder():
            time.sleep(wait_time)
            print(f"\nReminder: {message}\n")

        reminder_thread = threading.Thread(target=display_reminder)
        reminder_thread.daemon = True  # Allow the main program to exit even if the thread is running
        reminder_thread.start()

        return f"Reminder scheduled for {reminder_time} with message: {message}"

    except ValueError:
        return "Error: Invalid time format. Please use HH:MM format (e.g., 14:30)."
    except Exception as e:
        return f"Error: {e}"