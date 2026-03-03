
import os
import json
import datetime
import re
import uuid
from typing import List, Dict, Optional
from ai_assistant.config import get_data_dir

REMINDERS_FILE = "reminders.json"

def _get_reminders_path() -> str:
    return os.path.join(get_data_dir(), REMINDERS_FILE)

def _load_reminders() -> List[Dict]:
    path = _get_reminders_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return []

def _save_reminders(reminders: List[Dict]):
    path = _get_reminders_path()
    with open(path, 'w') as f:
        json.dump(reminders, f, indent=2)

def _parse_target_time(time_str: str, now: datetime.datetime):
    target_time = None
    recurrence = None

    match_rel = re.match(r"in\s+(\d+)\s+(minute|second|hour|day)s?", time_str.lower())
    if match_rel:
        val = int(match_rel.group(1))
        unit = match_rel.group(2)
        if unit.startswith("minute"):
            delta = datetime.timedelta(minutes=val)
        elif unit.startswith("second"):
            delta = datetime.timedelta(seconds=val)
        elif unit.startswith("hour"):
            delta = datetime.timedelta(hours=val)
        else:
            delta = datetime.timedelta(days=val)
        target_time = now + delta

    match_every = re.match(r"every\s+(\d+)\s+(minute|hour|day)s?", time_str.lower())
    if match_every:
        val = int(match_every.group(1))
        unit = match_every.group(2)
        recurrence = {"value": val, "unit": unit}
        if unit.startswith("minute"):
            target_time = now + datetime.timedelta(minutes=val)
        elif unit.startswith("hour"):
            target_time = now + datetime.timedelta(hours=val)
        else:
            target_time = now + datetime.timedelta(days=val)

    if not target_time:
        try:
            target_time = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        except ValueError:
            pass

    if not target_time:
        try:
            t = datetime.datetime.strptime(time_str, "%H:%M").time()
            target_time = datetime.datetime.combine(now.date(), t)
            if target_time < now:
                target_time += datetime.timedelta(days=1)
        except ValueError:
            pass

    return target_time, recurrence


def set_reminder(message: str, time_str: str) -> str:
    """
    Sets a reminder.
    Args:
        message: The text to remind about.
        time_str: When to remind. Supports:
                  - "in X minutes/hours/seconds"
                  - "HH:MM" (24-hour, assumes today or tomorrow if passed)
                  - "YYYY-MM-DD HH:MM"
    """
    now = datetime.datetime.now()
    target_time, recurrence = _parse_target_time(time_str, now)

    if not target_time:
        return f"Error: Could not parse time string '{time_str}'. Supported formats: 'in X minutes', 'HH:MM', 'YYYY-MM-DD HH:MM'."

    reminder = {
        "id": str(uuid.uuid4())[:8],
        "message": message,
        "created_at": now.isoformat(),
        "target_time": target_time.isoformat(),
        "status": "pending",
        "recurrence": recurrence
    }

    reminders = _load_reminders()
    reminders.append(reminder)
    _save_reminders(reminders)

    recur_text = f" (recurs every {recurrence['value']} {recurrence['unit']}(s))" if recurrence else ''
    return f"Reminder set for {target_time.strftime('%Y-%m-%d %H:%M:%S')}: {message}{recur_text}"

def list_reminders(status: str = "pending") -> str:
    """Lists reminders."""
    reminders = _load_reminders()
    filtered = [r for r in reminders if r['status'] == status] if status != "all" else reminders
    
    if not filtered:
        return "No reminders found."
        
    out = "Reminders:\n"
    for r in filtered:
        out += f"- [{r['id']}] {r['target_time']}: {r['message']} ({r['status']})\n"
    return out

def check_due_reminders() -> List[Dict]:
    """
    Checks for pending reminders that are due.
    Marks them as 'fired' and returns them.
    """
    reminders = _load_reminders()
    now = datetime.datetime.now()
    fired = []
    
    updated = False
    for r in reminders:
        if r['status'] == 'pending':
            try:
                target = datetime.datetime.fromisoformat(r['target_time'])
                if target <= now:
                    recurrence = r.get('recurrence')
                    if recurrence and isinstance(recurrence, dict):
                        value = int(recurrence.get('value', 1))
                        unit = str(recurrence.get('unit', 'minute')).lower()
                        if unit.startswith('hour'):
                            next_target = target + datetime.timedelta(hours=value)
                        elif unit.startswith('day'):
                            next_target = target + datetime.timedelta(days=value)
                        else:
                            next_target = target + datetime.timedelta(minutes=value)
                        r['target_time'] = next_target.isoformat()
                    else:
                        r['status'] = 'fired'
                    fired.append(r)
                    updated = True
            except Exception:
                pass
                
    if updated:
        _save_reminders(reminders)
        
    return fired

def update_reminder(reminder_id: str, new_time_str: Optional[str] = None, new_message: Optional[str] = None) -> str:
    """Updates an existing reminder's time and/or message."""
    reminders = _load_reminders()
    now = datetime.datetime.now()

    for reminder in reminders:
        if reminder.get('id') != reminder_id:
            continue

        if new_time_str:
            target_time, recurrence = _parse_target_time(new_time_str, now)
            if not target_time:
                return "Error: Could not parse new time string."
            reminder['target_time'] = target_time.isoformat()
            reminder['recurrence'] = recurrence
            reminder['status'] = 'pending'

        if new_message:
            reminder['message'] = new_message

        _save_reminders(reminders)
        return f"Reminder {reminder_id} updated."

    return f"Reminder {reminder_id} not found."


def delete_reminder(reminder_id: str) -> str:
    """Deletes a reminder by ID."""
    reminders = _load_reminders()
    initial_len = len(reminders)
    reminders = [r for r in reminders if r['id'] != reminder_id]
    
    if len(reminders) < initial_len:
        _save_reminders(reminders)
        return f"Reminder {reminder_id} deleted."
    return f"Reminder {reminder_id} not found."
