import asyncio
import datetime
from typing import Callable, Coroutine

async def remind_at_3_40(callback: Callable[[], Coroutine[None, None, None]]) -> None:
    """
    Reminds the user when it is 3:40 AM/PM.

    Args:
        callback (Callable[[], Coroutine[None, None, None]]): An asynchronous function to be called when it's 3:40.
                                                                This function should take no arguments and return None.

    Returns:
        None: This function does not return any value. It runs indefinitely until 3:40 is reached.
    """
    while True:
        now = datetime.datetime.now()
        target_time = now.replace(hour=15, minute=40, second=0, microsecond=0)  # 3:40 PM
        if now > target_time:
            target_time += datetime.timedelta(days=1)  # Next day

        time_difference = (target_time - now).total_seconds()
        await asyncio.sleep(time_difference)

        await callback()
        break  # Exit after the reminder is triggered once.  Remove this line to make it run every day.

async def example_callback():
    """Example callback function."""
    print("It's 3:40!")

if __name__ == '__main__':
    asyncio.run(remind_at_3_40(example_callback))