import re
import asyncio
import datetime

async def set_reminder(reminder_time: str, task: str) -> str:
    """
    Sets a reminder to perform a specific task at a specified time.

    Args:
        reminder_time (str): The time at which to set the reminder, in HH:MM format (e.g., "14:30") or relative time (e.g., "in 5 minutes").
        task (str): The task to be reminded about.

    Returns:
        str: A message indicating whether the reminder was successfully set or if an error occurred.
    """
    try:
        now = datetime.datetime.now()
        reminder_datetime = None
        relative_match = re.match('in\\s+(\\d+)\\s*(minute|min|hour|hr|second|sec)s?', reminder_time.lower())
        if relative_match:
            try:
                amount = int(relative_match.group(1))
                unit = relative_match.group(2)
            except ValueError:
                return 'Error: Invalid relative time format. Please specify a valid number of minutes, hours, or seconds.'
            delta = datetime.timedelta()
            if 'minute' in unit or 'min' in unit:
                delta = datetime.timedelta(minutes=amount)
            elif 'hour' in unit or 'hr' in unit:
                delta = datetime.timedelta(hours=amount)
            elif 'second' in unit or 'sec' in unit:
                delta = datetime.timedelta(seconds=amount)
            reminder_datetime = now + delta
        else:
            try:
                parts = reminder_time.split(':')
                if len(parts) != 2:
                    raise ValueError('Invalid time format: Please use HH:MM')
                reminder_hour, reminder_minute = map(int, parts)
                if not (0 <= reminder_hour <= 23 and 0 <= reminder_minute <= 59):
                    raise ValueError('Invalid time: Hour must be between 0-23 and minute between 0-59')
                reminder_datetime = datetime.datetime(now.year, now.month, now.day, reminder_hour, reminder_minute)
                if reminder_datetime < now:
                    reminder_datetime += datetime.timedelta(days=1)
            except ValueError as e:
                return f"Error: Invalid time format. Please use HH:MM format (e.g., 14:30) or relative time (e.g., 'in 5 minutes'). Details: {e}"
            except Exception as e:
                return f'Error: An unexpected error occurred while parsing the absolute time. Details: {e}'
        if reminder_datetime:
            wait_time = (reminder_datetime - now).total_seconds()
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                return f"Reminder: It's time to {task}!"
            else:
                return 'Error: The calculated reminder time has already passed.'
        else:
            return "Error: Invalid time format. Please use HH:MM format (e.g., 14:30) or relative time (e.g., 'in 5 minutes')."
    except Exception as e:
        return f'Error: An unexpected error occurred: {e}'

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
if __name__ == '__main__':
    asyncio.run(main('16:00', 'take out the trash'))