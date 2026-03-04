import datetime
import json

from ai_assistant.custom_tools import reminder_tool


def _read_saved(path):
    with open(path, 'r') as f:
        return json.load(f)


def test_set_reminder_supports_recurrence_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(reminder_tool, 'get_data_dir', lambda: str(tmp_path))

    result = reminder_tool.set_reminder('hydrate', 'every 2 hours')

    assert 'recurs every 2 hour' in result
    reminders = _read_saved(tmp_path / reminder_tool.REMINDERS_FILE)
    assert len(reminders) == 1
    saved = reminders[0]
    assert saved['status'] == 'pending'
    assert saved['recurrence'] == {'value': 2, 'unit': 'hour'}


def test_check_due_reminders_reschedules_recurring(tmp_path, monkeypatch):
    monkeypatch.setattr(reminder_tool, 'get_data_dir', lambda: str(tmp_path))

    now = datetime.datetime.now()
    due = now - datetime.timedelta(minutes=3)
    reminder_tool._save_reminders([
        {
            'id': 'abc12345',
            'message': 'stretch',
            'created_at': now.isoformat(),
            'target_time': due.isoformat(),
            'status': 'pending',
            'recurrence': {'value': 2, 'unit': 'minute'},
        }
    ])

    fired = reminder_tool.check_due_reminders()

    assert len(fired) == 1
    assert fired[0]['id'] == 'abc12345'
    reminders = reminder_tool._load_reminders()
    assert reminders[0]['status'] == 'pending'
    next_target = datetime.datetime.fromisoformat(reminders[0]['target_time'])
    assert next_target == due + datetime.timedelta(minutes=2)


def test_update_reminder_rejects_invalid_time(tmp_path, monkeypatch):
    monkeypatch.setattr(reminder_tool, 'get_data_dir', lambda: str(tmp_path))

    now = datetime.datetime.now().isoformat()
    reminder_tool._save_reminders([
        {
            'id': 'abc12345',
            'message': 'stretch',
            'created_at': now,
            'target_time': now,
            'status': 'pending',
            'recurrence': None,
        }
    ])

    result = reminder_tool.update_reminder('abc12345', new_time_str='nonsense value')

    assert result.startswith('Error: Could not parse new time string.')


def test_set_reminder_error_mentions_recurring_format(tmp_path, monkeypatch):
    monkeypatch.setattr(reminder_tool, 'get_data_dir', lambda: str(tmp_path))

    result = reminder_tool.set_reminder('test', 'tomorrow-ish')

    assert result.startswith('Error: Could not parse time string')
    assert 'every X minutes' in result
