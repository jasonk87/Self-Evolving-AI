import schedule
import time
import threading
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
        print(message)

    try:
        schedule.every().day.at(time_str).do(job)

        def run_scheduler():
            while True:
                schedule.run_pending()
                time.sleep(1)

        t = threading.Thread(target=run_scheduler)
        t.daemon = True  # Daemonize thread
        t.start()

        return f"Reminder set for {time_str} with message: {message}"
    except Exception as e:
        return f"Error setting reminder: {e}"

