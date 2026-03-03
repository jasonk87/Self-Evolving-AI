import json
import os

log_path = r"ai_assistant\core\data\reflection_log.json"
if os.path.exists(log_path):
    with open(log_path, 'r') as f:
        try:
            data = json.load(f)
            if isinstance(data, list) and data:
                last_entry = data[-1]
                print(json.dumps(last_entry, indent=2))
            else:
                print("Log is empty or not a list.")
        except json.JSONDecodeError:
            print("Log is invalid JSON.")
else:
    print("Log file not found.")
