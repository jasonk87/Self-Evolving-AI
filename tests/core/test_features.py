
from ai_assistant.core.approval_manager import ApprovalManager
from ai_assistant.execution.action_executor import ActionExecutor
from ai_assistant.code_synthesis.linting import CodeLinter

def test_approval_manager_manual_resolution():
    am = ApprovalManager()
    req_id = am.add_request("test_type", {}, "desc")

    assert req_id in am.pending_requests

    # Test Manual Resolution
    assert am.resolve_request_manually(req_id) is True
    assert req_id not in am.pending_requests

def test_core_path_protection():
    # Mock LearningAgent and TaskManager
    class MockAgent: pass

    ae = ActionExecutor(learning_agent=MockAgent(), task_manager=None, notification_manager=None)

    assert ae._is_core_system_path("/ai_assistant/core/something.py") is True
    assert ae._is_core_system_path("ai_assistant/core/something.py") is True
    assert ae._is_core_system_path("ai_assistant/custom_tools/my_tool.py") is False
    assert ae._is_core_system_path("ai_assistant/code_synthesis/service.py") is True

def test_linter_syntax_error():
    code = "def foo()\n    return 1" # Missing colon
    passed, errors = CodeLinter.lint_code(code)
    assert not passed
    assert len(errors) > 0
    assert "SyntaxError" in errors[0].error_type

def test_linter_duplicate_import():
    code = """
import os
import sys
import os # Duplicate
def foo():
    pass
"""
    passed, errors = CodeLinter.lint_code(code)
    assert not passed
    assert any("Duplicate import" in e.message for e in errors)

if __name__ == "__main__":
    test_approval_manager_manual_resolution()
    test_core_path_protection()
    test_linter_syntax_error()
    test_linter_duplicate_import()
    print("All tests passed.")
