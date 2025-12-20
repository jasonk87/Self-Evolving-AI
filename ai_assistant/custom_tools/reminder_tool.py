
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
    target_time = None

    # Parse 'in X minutes'
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
        elif unit.startswith("day"):
            delta = datetime.timedelta(days=val)
        target_time = now + delta
    
    # Parse HH:MM
    if not target_time:
        try:
            # Try ISO first "YYYY-MM-DD HH:MM"
            target_time = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        except ValueError:
            pass
            
    if not target_time:
        try:
            # HH:MM
            t = datetime.datetime.strptime(time_str, "%H:%M").time()
            target_time = datetime.datetime.combine(now.date(), t)
            if target_time < now:
                target_time += datetime.timedelta(days=1)
        except ValueError:
            pass

    if not target_time:
        return f"Error: Could not parse time string '{time_str}'. Supported formats: 'in X minutes', 'HH:MM', 'YYYY-MM-DD HH:MM'."

    reminder = {
        "id": str(uuid.uuid4())[:8],
        "message": message,
        "created_at": now.isoformat(),
        "target_time": target_time.isoformat(),
        "status": "pending"
    }

    reminders = _load_reminders()
    reminders.append(reminder)
    _save_reminders(reminders)

    return f"Reminder set for {target_time.strftime('%Y-%m-%d %H:%M:%S')}: {message}"

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
                    r['status'] = 'fired'
                    fired.append(r)
                    updated = True
            except Exception:
                pass
                
    if updated:
        _save_reminders(reminders)
        
    return fired

def delete_reminder(reminder_id: str) -> str:
    """Deletes a reminder by ID."""
    reminders = _load_reminders()
    initial_len = len(reminders)
    reminders = [r for r in reminders if r['id'] != reminder_id]
    
    if len(reminders) < initial_len:
        _save_reminders(reminders)
        return f"Reminder {reminder_id} deleted."
    return f"Reminder {reminder_id} not found."
