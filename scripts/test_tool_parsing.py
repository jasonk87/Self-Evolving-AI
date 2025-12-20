
import re
import json
import sys

def parse_with_regex(text):
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return None

def parse_with_stack(text):
    start_index = text.find("{")
    if start_index != -1:
        balance = 0
        for i in range(start_index, len(text)):
            char = text[i]
            if char == "{":
                balance += 1
            elif char == "}":
                balance -= 1
                if balance == 0:
                    candidate_json = text[start_index:i+1]
                    try:
                        return json.loads(candidate_json)
                    except:
                        pass # Continue finding?
    return None

def _parse_tool_call(text):
    # 1. Regex
    res = parse_with_regex(text)
    if res: return res
    
    # 2. Stack
    res = parse_with_stack(text)
    if res: return res
    return None

test_cases = [
    ('```json\n{"action": "test"}\n```', {"action": "test"}),
    ('```\n{"action": "test"}\n```', {"action": "test"}),
    ('json {"action": "test"}', {"action": "test"}),
    ('Here is a tool:\n{"action": "test", "args": {"nested": [1, 2]}}', {"action": "test", "args": {"nested": [1, 2]}}),
    ('{"action": "test"}', {"action": "test"}),
    ('Some text output\n```json\n{"action": "test"}\n```\nMore text', {"action": "test"}),
]

failed = False
for inp, expected in test_cases:
    print(f"Testing input: {repr(inp)}")
    result = _parse_tool_call(inp)
    if result == expected:
        print("PASS")
    else:
        print(f"FAIL. Expected {expected}, got {result}")
        failed = True

if failed:
    sys.exit(1)
print("All tests passed.")
