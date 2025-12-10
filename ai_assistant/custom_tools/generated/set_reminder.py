import asyncio
import datetime
import time
from typing import Optional

async def set_reminder(reminder_time: str, message: Optional[str] = None) -> str:
    """
    Sets a reminder for a specific time with an optional message.

    Args:
        reminder_time (str): The time for the reminder in HH:MM format (24-hour clock).
        message (Optional[str]): An optional message to display when the reminder goes off. Defaults to None.

    Returns:
        str: A message indicating whether the reminder was set successfully or if an error occurred.
    """
    try:
        now = datetime.datetime.now()
        reminder_hour, reminder_minute = map(int, reminder_time.split(':'))

        if not (0 <= reminder_hour <= 23 and 0 <= reminder_minute <= 59):
            return "Error: Invalid time format. Please use HH:MM (24-hour clock)."

        reminder_datetime = now.replace(hour=reminder_hour, minute=reminder_minute, second=0, microsecond=0)

        if reminder_datetime <= now:
            reminder_datetime += datetime.timedelta(days=1)  # Set for the next day if the time has already passed

        time_difference = (reminder_datetime - now).total_seconds()

        if time_difference <= 0:
            return "Error: Invalid time. Please provide a future time."

        await asyncio.sleep(time_difference)

        if message:
            return f"Reminder: {message}"
        else:
            return "Reminder: Time's up!"

    except ValueError:
        return "Error: Invalid time format. Please use HH:MM (24-hour clock)."
    except Exception as e:
        return f"Error: {e}"