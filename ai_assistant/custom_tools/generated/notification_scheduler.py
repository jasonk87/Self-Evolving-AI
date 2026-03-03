import time
import datetime
import platform
import subprocess
from typing import Optional

def schedule_notification(reminder_message: str, delay_minutes: int) -> str:
    """Schedules a desktop notification to be displayed after a specified delay.

    Args:
        reminder_message (str): The message to be displayed in the notification.
        delay_minutes (int): The delay in minutes before the notification is shown.

    Returns:
        str: A message indicating whether the notification was scheduled successfully or if an error occurred.
    """
    try:
        delay_seconds = delay_minutes * 60
        future_time = datetime.datetime.now() + datetime.timedelta(seconds=delay_seconds)
        timestamp = future_time.timestamp()
        system = platform.system()
        if system == 'Darwin':
            script = f'''\n            osascript -e 'display notification "{reminder_message}" with title "Reminder" sound name "default"'\n            '''
            time.sleep(delay_seconds)
            subprocess.run(script, shell=True, check=True)
            return 'Notification scheduled successfully (macOS).'
        elif system == 'Linux':
            try:
                subprocess.run(['which', 'notify-send'], check=True, capture_output=True)
                script = f'\n                sleep {delay_seconds}\n                notify-send "Reminder" "{reminder_message}"\n                '
                subprocess.run(script, shell=True, check=True)
                return 'Notification scheduled successfully (Linux).'
            except subprocess.CalledProcessError:
                return 'Error: notify-send is not installed. Please install it to use notifications on Linux.'
        elif system == 'Windows':
            import winsound
            import threading

            def show_notification():
                time.sleep(delay_seconds)
                try:
                    import win10toast
                    toaster = win10toast.ToastNotifier()
                    toaster.show_toast('Reminder', reminder_message, duration=10)
                except ImportError:
                    print('win10toast not installed. Falling back to winsound.')
                    winsound.PlaySound('SystemExclamation', winsound.SND_ALIAS)
                except Exception as e:
                    print(f'Error displaying notification: {e}')
            notification_thread = threading.Thread(target=show_notification)
            notification_thread.start()
            return 'Notification scheduled successfully (Windows).'
        else:
            return f'Unsupported operating system: {system}'
    except Exception as e:
        return f'Error scheduling notification: {e}'