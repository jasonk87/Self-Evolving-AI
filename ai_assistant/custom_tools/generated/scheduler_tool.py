import time
import threading
import datetime
from typing import Callable

def set_reminder(time_str: str, message: str) -> str:
    """Schedules a reminder to print a message at a specified time.

    Args:
        time_str (str): The time at which to print the message, in HH:MM format (e.g., "10:30").
        message (str): The message to print.

    Returns:
        str: A message indicating that the reminder has been scheduled.
    """
    def job():
        print(f"\nREMINDER: {message}\n")

    try:
        # Parse time_str to get HH:MM
        # This simple implementation schedulers it for the next occurrence of that time
        target_hour, target_minute = map(int, time_str.split(':'))
        
        now = datetime.datetime.now()
        target_time = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        
        if target_time <= now:
            target_time += datetime.timedelta(days=1)
            
        delay = (target_time - now).total_seconds()

        def run_timer():
            time.sleep(delay)
            job()

        t = threading.Thread(target=run_timer)
        t.daemon = True 
        t.start()

        return f"Reminder set for {time_str} with message: {message}"
    except Exception as e:
        return f"Error setting reminder: {e}"

