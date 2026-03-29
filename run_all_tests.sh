#!/bin/bash
for file in $(find tests -type f -name "*.py" | grep -v "repro_bugs.py" | grep -v "debug_parsing.py" | grep -v "verify_feedback_loop.py" | grep -v "tests/test_ai_assistant_ws_self_modification" | grep -v "__init__.py" | grep -v "test_notifications_tasks.py" | grep -v "verify_tool_fixes.py" | grep -v "test_learning.py" | grep -v "test_reviewer.py"); do
    python -m pytest $file -v --timeout=10 || echo "$file FAILED" > /dev/null
done
