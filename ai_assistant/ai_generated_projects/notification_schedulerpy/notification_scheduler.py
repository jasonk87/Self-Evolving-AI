import schedule
import time
import json
import threading
import os

TELEMETRY_FILE = "telemetry.json"

def load_scheduled_jobs():
    """Loads scheduled jobs from the telemetry file."""
    try:
        with open(TELEMETRY_FILE, "r") as f:
            data = json.load(f)
            jobs = data.get("scheduled_jobs", [])
            return jobs
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        print("Error decoding telemetry file. Starting with empty schedule.")
        return []
    except Exception as e:
        print(f"Error loading scheduled jobs: {e}")
        return []

def save_scheduled_jobs(jobs):
    """Saves scheduled jobs to the telemetry file."""
    try:
        data = {"scheduled_jobs": jobs}
        with open(TELEMETRY_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving scheduled jobs: {e}")

def run_notification(message):
    """Placeholder for the actual notification sending logic."""
    print(f"Sending notification: {message}")

def add_notification(time_str, message):
    """Adds a new notification to the scheduler."""
    try:
        schedule.every().day.at(time_str).do(run_notification, message=message)
        jobs = load_scheduled_jobs()
        jobs.append({"time": time_str, "message": message})
        save_scheduled_jobs(jobs)
        print(f"Notification scheduled for {time_str} with message: {message}")
        return True
    except Exception as e:
        print(f"Error adding notification: {e}")
        return False

def remove_notification(time_str, message):
    """Removes a notification from the scheduler."""
    jobs = load_scheduled_jobs()
    updated_jobs = []
    removed = False
    for job in jobs:
        if job["time"] == time_str and job["message"] == message:
            removed = True
            continue
        updated_jobs.append(job)

    if removed:
        save_scheduled_jobs(updated_jobs)
        schedule.clear() # Clear all scheduled jobs and re-schedule from file
        reschedule_jobs()
        print(f"Notification removed for {time_str} with message: {message}")
        return True
    else:
        print(f"No notification found for {time_str} with message: {message}")
        return False

def list_notifications():
    """Lists all scheduled notifications."""
    jobs = load_scheduled_jobs()
    if not jobs:
        print("No notifications scheduled.")
        return []
    else:
        print("Scheduled Notifications:")
        for job in jobs:
            print(f"Time: {job['time']}, Message: {job['message']}")
        return jobs

def reschedule_jobs():
    """Reschedules jobs from the telemetry file."""
    jobs = load_scheduled_jobs()
    for job in jobs:
        try:
            schedule.every().day.at(job["time"]).do(run_notification, message=job["message"])
        except Exception as e:
            print(f"Error rescheduling job: {e}")

def run_scheduler():
    """Runs the scheduler in a separate thread."""
    while True:
        schedule.run_pending()
        time.sleep(1)

def start_scheduler():
    """Starts the scheduler thread."""
    reschedule_jobs()
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

if __name__ == "__main__":
    start_scheduler()

    # Example usage:
    add_notification("10:30", "Wake up!")
    add_notification("18:00", "Dinner time!")
    list_notifications()
    time.sleep(10)
    remove_notification("10:30", "Wake up!")
    list_notifications()
    time.sleep(5)