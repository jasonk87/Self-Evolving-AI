import re

def deduce(response):
    if not response:
            return None

    # 1. Try strict match first (cleaning whitespace/quotes)
    cleaned = response.strip().strip("`'\"")
    if " " not in cleaned and "\n" not in cleaned:
            return None if cleaned == "None" else cleaned

    # 2. Fallback: Use Regex to find the tool name
    # Look for a python identifier pattern
    # Common patterns: "The tool is `tool_name`", "Tool: tool_name", or just "tool_name" at the end.
    # We look for the last valid identifier token in the string, assuming the LLM might end with the answer.
    
    # Pattern: snake_case word, possibly inside backticks or quotes
    matches = re.findall(r"[`'\"]?([a-zA-Z_][a-zA-Z0-9_]*)[`'\"]?", response)
    
    if matches:
        # Filter out common stopwords if necessary, but "None" is the main one.
        candidates = [m for m in matches if m.lower() not in ["none", "tool", "is", "the", "answer", "name", "function", "return", "output"]]
        if candidates:
            # Heuristic: The *last* candidate is often the conclusion ("Therefore is: tool_name")
            # But sometimes it's "tool_name is the answer".
            # Let's try to match the "Therefore..." pattern first.
            
            # Check for "Therefore, the answer is: <tool>"
            # Removed 'is' from triggers as it matches "is a..." too easily.
            final_answer_match = re.search(r"(?:answer|tool)[:\s]+[`'\"]?([a-zA-Z_][a-zA-Z0-9_]*)[`'\"]?", response, re.IGNORECASE)
            if final_answer_match:
                    return final_answer_match.group(1)
            
            return candidates[-1]

    return None

cases = [
    "get_weather",
    "'get_weather'",
    "The answer is get_weather",
    "I think it is a weather tool.\nTherefore, the answer is: get_weather",
    "The description indicates... \n\nTherefore, the answer is:\nset_reminder",
    "Based on the input, the tool name is `read_file`."
]

for i, case in enumerate(cases):
    print(f"Case {i+1}: {deduce(case)}")
