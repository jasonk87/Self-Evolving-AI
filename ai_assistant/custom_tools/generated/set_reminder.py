import asyncio
import datetime
import time
from typing import Optional

async def set_reminder(reminder_time: str, task: str) -> str:
    """
    Sets a reminder to perform a specific task at a specified time.

    Args:
        reminder_time (str): The time at which to set the reminder, in HH:MM format (e.g., "14:30").
        task (str): The task to be reminded about.

    Returns:
        str: A message indicating whether the reminder was successfully set or if an error occurred.
    """
    try:
        now = datetime.datetime.now()
        reminder_hour, reminder_minute = map(int, reminder_time.split(':'))

        reminder_datetime = datetime.datetime(now.year, now.month, now.day, reminder_hour, reminder_minute)

        if reminder_datetime < now:
            reminder_datetime += datetime.timedelta(days=1)

        wait_time = (reminder_datetime - now).total_seconds()

        if wait_time > 0:
            await asyncio.sleep(wait_time)
            return f"Reminder: It's time to {task}!"
        else:
            return "Error: The specified time has already passed."

    except ValueError:
        return "Error: Invalid time format. Please use HH:MM format (e.g., 14:30)."
    except Exception as e:
        return f"Error: {e}"

async def main(reminder_time: str, task: str) -> str:
    """
    Main function to set the reminder.

    Args:
        reminder_time (str): The time at which to set the reminder, in HH:MM format (e.g., "14:30").
        task (str): The task to be reminded about.

    Returns:
        str: A message indicating whether the reminder was successfully set or if an error occurred.
    """
    return await set_reminder(reminder_time, task)

if __name__ == "__main__":
    asyncio.run(main("16:00", "take out the trash"))