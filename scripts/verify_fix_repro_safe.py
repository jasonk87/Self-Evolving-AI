
import sys
import os
import json
import re

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import just the parser if possible, or mock the class
# Since _parse_tool_call is an instance method but doesn't use 'self' state,
# we can just copy it or mock the instance.

class MockOrchestrator:
    def _parse_tool_call(self, text):
        try:
            # 1. Attempt refined regex for backticks
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))

            # 2. Attempt to find the first valid brace pair
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
                            return json.loads(candidate_json)
        except Exception:
            pass
        return None

def test_parsing():
    orchestrator = MockOrchestrator()
    
    # 1. The "Chat Bubble" Case
    malformed_input = """
    I will propose the update now.
    
    json
    {
      "action": "propose_project_file_update",
      "args": ["some_file.py", "content"],
      "kwargs": {},
      "thought": "This should be executed, not printed."
    }
    """
    
    print(f"Testing Malformed Input:\n{malformed_input}\n---")
    result = orchestrator._parse_tool_call(malformed_input)
    
    if result and result.get("action") == "propose_project_file_update":
        print(f"✅ SUCCESS: Parsed correctly! Result: {result}")
    else:
        print(f"❌ FAILED: Could not parse. Result: {result}")
        sys.exit(1)

    # 2. The "Loose Braces" Case
    loose_input = """
    {
      "action": "execute_sandboxed_python_script",
      "args": ["print('hello')"],
      "kwargs": {},
      "thought": "Just braces."
    }
    """
    print(f"\nTesting Loose Braces:\n{loose_input}\n---")
    result_loose = orchestrator._parse_tool_call(loose_input)
    
    if result_loose and result_loose.get("action") == "execute_sandboxed_python_script":
         print(f"✅ SUCCESS: Parsed correctly! Result: {result_loose}")
    else:
         print(f"❌ FAILED: Could not parse loose braces. Result: {result_loose}")
         sys.exit(1)

if __name__ == "__main__":
    test_parsing()
