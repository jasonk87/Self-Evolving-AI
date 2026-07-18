from types import SimpleNamespace

from ai_assistant.core.sandbox import SandboxManager


def test_zero_collected_tests_are_validation_infrastructure_failure(tmp_path, monkeypatch):
    process = SimpleNamespace(
        returncode=5,
        stdout="collected 0 items\n\n============================ no tests ran ============================",
        stderr="",
    )
    monkeypatch.setattr("ai_assistant.core.sandbox.subprocess.run", lambda *args, **kwargs: process)

    success, stdout, stderr = SandboxManager(str(tmp_path)).execute_test(
        "ai_assistant.custom_tools.calendar_tools",
        "def check_calendar(date_str):\n    return date_str\n",
        "# malformed generated test with no test functions\n",
    )

    assert success is False
    assert "no tests ran" in stdout
    assert "collected zero tests" in stderr
    assert "Production code was not proven defective" in stderr
