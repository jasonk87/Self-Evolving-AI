import schedule
import time
import logging
import json

# Configure logging
logging.basicConfig(filename='notification_scheduler.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

TELEMETRY_FILE = "telemetry.json"

def load_state():
    """Loads state from telemetry.json."""
    try:
        with open(TELEMETRY_FILE, 'r') as f:
            state = json.load(f)
        return state
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logging.warning("telemetry.json is corrupt. Returning empty state.")
        return {}

def save_state(state):
    """Saves state to telemetry.json."""
    try:
        with open(TELEMETRY_FILE, 'w') as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving state to {TELEMETRY_FILE}: {e}")


def send_notification(message):
    """Sends a notification (currently prints to console)."""
    print(f"Notification: {message}")
    logging.info(f"Notification sent: {message}")

def schedule_notification(notification_time, message):
    """Schedules a notification to be sent at a specific time."""
    schedule.every().day.at(notification_time).do(send_notification, message)
    logging.info(f"Notification scheduled for {notification_time} with message: {message}")

def run_scheduler():
    """Runs the scheduler loop."""
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    state = load_state()
    logging.info(f"Loaded state: {state}")

    # Example: Schedule a notification if it's not already scheduled in the state
    if "scheduled_notifications" not in state:
        state["scheduled_notifications"] = []

    if not state["scheduled_notifications"]:
        schedule_notification("10:00", "Good morning!")
        state["scheduled_notifications"].append({"time": "10:00", "message": "Good morning!"})
        schedule_notification("18:00", "Time for a break!")
        state["scheduled_notifications"].append({"time": "18:00", "message": "Time for a break!"})
        save_state(state)  # Save the initial schedule to state
        logging.info("Initial notifications scheduled.")
    else:
        # Re-schedule notifications from state
        for notification in state["scheduled_notifications"]:
            schedule_notification(notification["time"], notification["message"])
        logging.info("Notifications re-scheduled from state.")

    run_scheduler()