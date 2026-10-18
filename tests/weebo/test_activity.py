"""What Weebo shows while it works is plain words, never a command line."""

import pytest

from weebo.brain import activity
from weebo.brain.conversation import item_to_message

COMMANDS = [
    ('"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "git status --short"',
     "Looking over the changes", "reviewing"),
    ('/bin/bash -lc "python -m pytest tests/weebo -q -p no:cacheprovider"', "Running the tests", "reviewing"),
    ("pip install fastembed==0.4.2", "Installing what it needs", "coding"),
    ("rg -n 'def merge' weebo/evolution", "Searching the code", "reviewing"),
    ("Get-ChildItem -Recurse -Filter *.py", "Looking through the files", "reviewing"),
    ("curl -s https://api.example.com/v1/forecast?city=Paris", "Fetching a web page", "researching"),
    ("python scripts/rebuild_index.py --force", "Running a script", "coding"),
    ("python -m weebo --selftest", "Checking that I still start up", "reviewing"),
    ("some-obscure-tool --frobnicate=yes ./data", "Working on it", "coding"),
]


@pytest.mark.parametrize("command,words,kind", COMMANDS)
def test_commands_are_described_never_echoed(command, words, kind):
    text, what = activity.describe_command(command)
    assert (text, what) == (words, kind)
    for token in ("git", "pytest", "pip", "rg", "Get-ChildItem", "curl", "python", "--", "powershell", "obscure"):
        assert token not in text


def test_running_items_carry_plain_words_and_an_activity():
    kind, _role, content, data, status = item_to_message({
        "type": "commandExecution", "status": "inProgress", "command": "bash -lc 'npm run build'",
        "commandActions": [{"type": "unknown", "command": "npm run build"}]})
    assert (kind, status) == ("command", "running")
    assert (data["doing"], data["activity"]) == ("Building it", "coding")
    _k, _r, _c, data, _s = item_to_message({"type": "commandExecution", "status": "inProgress", "command": "cat a.py",
                                            "commandActions": [{"type": "read", "name": "a.py", "path": "/x/a.py"}]})
    assert (data["doing"], data["activity"]) == ("Reading a.py", "reviewing")
    _k, _r, _c, data, _s = item_to_message({"type": "dynamicToolCall", "status": "inProgress", "tool": "use_integration",
                                            "arguments": {"name": "get_weather", "arguments": {"city": "Paris"}}})
    assert (data["doing"], data["activity"]) == ("Checking the weather", "researching")
    _k, _r, _c, data, _s = item_to_message({"type": "fileChange", "status": "inProgress",
                                            "changes": [{"path": "/repo/weebo/app.py", "kind": {"type": "update"}}]})
    assert (data["doing"], data["activity"]) == ("Editing app.py", "coding")
    text, _r, _c, data, _s = item_to_message({"type": "agentMessage", "text": "Hi"})
    assert text == "text" and "doing" not in data  # replies aren't work items


def test_reasoning_shows_only_a_plain_heading():
    assert activity.reasoning_heading("**Checking the forecast**\n\nI'll call get_weather for Paris.") == \
        "Checking the forecast"
    assert activity.reasoning_heading("**Running `pytest -x`**\n\nThen fix.") == ""  # code-ish: say nothing specific
    assert activity.reasoning_heading("**Planning**\n\nx\n\n**Editing weebo/app.py**\n\ny") == "Planning"
    assert activity.describe("reasoning", "no heading at all") == ("Thinking it through", "thinking")


@pytest.mark.asyncio
async def test_agent_progress_is_plain_words(app):
    from weebo.agents.manager import AgentRun

    run = AgentRun(task_id="t_1")
    seen = []
    app.bus.subscribe("task.progress", lambda topic, data: seen.append(data))
    app.agents._on_event(run, "item/started", {"item": {
        "type": "commandExecution", "command": "bash -lc 'python -m pytest -q'", "status": "inProgress"}})
    assert run.progress == "Running the tests" and run.activity == "reviewing"
    app.agents._on_event(run, "item/reasoning/summaryTextDelta", {"delta": "**Reading the failure**\n\nThe `x`"})
    assert run.progress == "Reading the failure"
    assert all("pytest" not in d["progress"] for d in seen)
