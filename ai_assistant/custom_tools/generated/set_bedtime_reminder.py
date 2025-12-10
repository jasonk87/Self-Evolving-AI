import asyncio
import datetime
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_assistant.core.action_executor import ActionExecutor


async def set_bedtime_reminder(action_executor: "ActionExecutor") -> str:
    """
    Sets a reminder to go to bed at 8:00 PM today.

    Args:
        action_executor: The action executor.

    Returns:
        str: A message indicating whether the reminder was successfully set or if an error occurred.
    """
    try:
        now = datetime.datetime.now()
        bedtime = now.replace(hour=20, minute=0, second=0, microsecond=0)  # 8:00 PM

        if bedtime < now:
            bedtime += datetime.timedelta(days=1)  # If it's already past 8 PM, set for tomorrow

        sleep_seconds = (bedtime - now).total_seconds()

        async def bedtime_alert():
            await asyncio.sleep(sleep_seconds)
            print("Time to go to bed!")  # Replace with a more sophisticated alert if needed
            # You could potentially use action_executor to trigger another tool here,
            # like sending a notification.

        asyncio.create_task(bedtime_alert())

        return "Bedtime reminder set for 8:00 PM."
    except Exception as e:
        # import logging; logger = logging.getLogger(__name__); logger.error(f"Error: {e}")
        return f"Error setting bedtime reminder: {e}"