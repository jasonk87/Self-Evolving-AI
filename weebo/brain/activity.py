"""Plain words for what Weebo is doing right now: "Running the tests", "Checking the weather".

What the user sees while Weebo works (the line under the avatar, an agent's progress) is a description of the
activity, never the command line, file path soup or raw reasoning text. Each description also names the kind of
work, which picks the avatar's animation: thinking, coding, reviewing, researching or delegating.
"""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any

WORK_KINDS = {"command", "file_change", "tool", "web_search", "reasoning", "subagent", "image_view", "review"}

# Codex wraps commands in the shell it runs them with: "C:\...\powershell.exe" -Command "git status".
_SHELL_WRAPPER = re.compile(
    r'^\s*(?:"[^"]*?(?:powershell|pwsh|cmd|bash|zsh|sh)(?:\.exe)?"|[^"\s]*?(?:powershell|pwsh|cmd|bash|zsh|sh)(?:\.exe)?)'
    r'\s+(?:-NoLogo\s+|-NoProfile\s+|-NonInteractive\s+)*(?:-Command|-c|-lc|/d\s+/c|/c)\s+', re.IGNORECASE)

_TESTS = re.compile(r"\b(pytest|unittest|jest|vitest|mocha|go test|cargo test|npm (?:run )?test|yarn test|pnpm test)\b")
_INSTALL = re.compile(r"\b(pip3?|npm|yarn|pnpm|uv|poetry|apt(?:-get)?|brew|choco|winget|cargo)\s+(?:\S+\s+)?(install|add|sync)\b")
_BUILD = re.compile(r"\b(make|cmake|tsc|webpack|vite build|npm run build|cargo build|go build|dotnet build|msbuild)\b")

_PROGRAMS: dict[str, tuple[str, str]] = {}
for _names, _what in (
    (("curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm"), ("Fetching a web page", "researching")),
    (("ls", "dir", "get-childitem", "gci", "tree", "find", "fd"), ("Looking through the files", "reviewing")),
    (("cat", "type", "get-content", "gc", "head", "tail", "less", "more", "bat", "sed", "awk", "nl", "wc"),
     ("Reading a file", "reviewing")),
    (("grep", "rg", "ag", "findstr", "select-string", "sls"), ("Searching the code", "reviewing")),
    (("mkdir", "md", "cp", "copy", "copy-item", "mv", "move", "move-item", "rm", "del", "remove-item", "rmdir",
      "new-item", "ni", "touch", "ren", "rename-item"), ("Organizing files", "coding")),
    (("python", "python3", "py", "node", "deno", "bun", "ruby", "perl", "php", "java", "dotnet"),
     ("Running a script", "coding")),
    (("get-date", "date", "time"), ("Checking the time", "thinking")),
    (("echo", "write-output", "write-host", "printf"), ("Working on it", "coding")),
    (("ping", "nslookup", "dig", "traceroute", "tracert", "test-netconnection"), ("Checking the network", "researching")),
    (("docker", "docker-compose", "kubectl"), ("Working with containers", "coding")),
):
    for _name in _names:
        _PROGRAMS[_name] = _what

_GIT = {
    **dict.fromkeys(("diff", "status", "log", "show", "blame", "shortlog"), ("Looking over the changes", "reviewing")),
    **dict.fromkeys(("add", "commit", "stash", "tag"), ("Saving the work", "coding")),
    **dict.fromkeys(("clone", "pull", "fetch"), ("Getting the latest code", "coding")),
    **dict.fromkeys(("checkout", "switch", "branch", "merge", "rebase", "reset", "restore", "worktree", "cherry-pick"),
                    ("Working with the code history", "coding")),
    **dict.fromkeys(("grep", "ls-files"), ("Searching the code", "reviewing")),
}

# Weebo's own tools, said as what is happening (present tense: these show while the tool runs).
_TOOLS: dict[str, tuple[str, str]] = {
    "remember": ("Saving that to memory", "thinking"), "recall": ("Checking my memory", "thinking"),
    "forget": ("Forgetting that", "thinking"), "update_memory": ("Updating a memory", "thinking"),
    "search_history": ("Looking back through our chat", "researching"),
    "set_reminder": ("Setting a reminder", "thinking"), "list_reminders": ("Checking your reminders", "thinking"),
    "cancel_reminder": ("Cancelling a reminder", "thinking"),
    "start_agent": ("Handing this to an agent", "delegating"), "agent_status": ("Checking on the agents", "delegating"),
    "message_agent": ("Updating an agent", "delegating"), "stop_agent": ("Stopping an agent", "delegating"),
    "propose_improvement": ("Writing up an improvement", "coding"), "evolution_status": ("Checking my upgrades", "reviewing"),
    "show_upgrade": ("Pulling up the upgrade", "reviewing"), "build_upgrade": ("Starting a build", "coding"),
    "reject_upgrade": ("Setting that upgrade aside", "thinking"), "merge_upgrades": ("Merging the upgrades", "coding"),
    "save_skill": ("Learning a new skill", "thinking"), "notify_user": ("Sending you a note", "thinking"),
    "screenshot": ("Looking at your screen", "reviewing"), "open_in_browser": ("Opening a page", "researching"),
    "weebo_status": ("Checking how I'm doing", "reviewing"), "update_settings": ("Changing a setting", "thinking"),
    "change_setting": ("Changing a setting", "thinking"), "restart_weebo": ("Getting ready to restart", "thinking"),
    "offer_replies": ("Thinking of replies", "thinking"),
}
_INTEGRATIONS: dict[str, tuple[str, str]] = {
    "get_weather": ("Checking the weather", "researching"), "get_user_location": ("Finding where you are", "researching"),
    "google_search": ("Searching the web", "researching"), "news_search": ("Checking the news", "researching"),
    "execute_deep_research": ("Researching", "researching"), "web_search_images": ("Looking for pictures", "researching"),
    "check_calendar": ("Checking your calendar", "researching"),
    "list_upcoming_events": ("Checking your calendar", "researching"),
    "schedule_event": ("Adding it to your calendar", "thinking"), "send_text_message": ("Sending a text", "thinking"),
}
_MCP_NAMES = {"gmail": "Gmail", "google_calendar": "your calendar", "calendar": "your calendar",
              "google_drive": "Google Drive", "drive": "Google Drive", "github": "GitHub", "slack": "Slack"}

_CODEY = re.compile(r"[`\\/;$|<>]|--|\(\)|==|=>|\$\{|\b\w+\.(py|js|ts|json|md|txt|ps1|sh|exe)\b|\b\w+_\w+\b")
_COMMANDY = re.compile(
    r"^(?:(?:running|using|executing)\s+)?(?:git|python\d*|py|node|npm|npx|pnpm|yarn|pip\d*|uv|"
    r"pytest|cargo|go|curl|wget|rg|grep|ls|cat|echo|rm|cmd|powershell|pwsh|bash|sh|"
    r"(?:get|set|remove|new|invoke|start|stop|write|test)-[a-z]+)(?:\s|$)", re.IGNORECASE)


def strip_shell(command: str) -> str:
    """The command without the shell that wraps it (what a person typing it would have written)."""
    text = (command or "").strip()
    if _SHELL_WRAPPER.match(text):
        text = _SHELL_WRAPPER.sub("", text, count=1).strip()
        if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
            text = text[1:-1]
    return text.replace('\\"', '"')


def _name(path: Any) -> str:
    name = str(path or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name if name and not re.search(r"[\r\n`;$|<>]", name) else "a file"


def describe_command(command: str, actions: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    actions = [a for a in actions or [] if isinstance(a, dict)]
    if actions:
        kinds = {a.get("type") for a in actions}
        if kinds == {"read"}:
            names = [_name(a.get("name") or a.get("path")) for a in actions][:2]
            return (f"Reading {' and '.join(names)}" if len(actions) <= 2 else f"Reading {len(actions)} files"), "reviewing"
        if kinds == {"search"}:
            return "Searching the code", "reviewing"
        if kinds == {"listFiles"}:
            return "Looking through the files", "reviewing"
    text = strip_shell(command)
    lowered = text.lower()
    if _TESTS.search(lowered) or re.search(r"-m\s+(pytest|unittest)\b", lowered):
        return "Running the tests", "reviewing"
    if _INSTALL.search(lowered):
        return "Installing what it needs", "coding"
    if _BUILD.search(lowered):
        return "Building it", "coding"
    if "--selftest" in lowered:
        return "Checking that I still start up", "reviewing"
    words = re.split(r"[\s;&|]+", lowered.strip())
    program = PurePath(words[0].strip("\"'")).name if words and words[0] else ""
    program = re.sub(r"\.(exe|cmd|bat|ps1)$", "", program)
    if program == "git":
        sub = next((w for w in words[1:] if w and not w.startswith("-")), "")
        return _GIT.get(sub, ("Working with the code", "coding"))
    if program in _PROGRAMS:
        return _PROGRAMS[program]
    return "Working on it", "coding"


def describe_tool(source: str, tool: str, arguments: Any = None, server: str = "") -> tuple[str, str]:
    if source == "weebo":
        if tool in ("use_integration", "weebo1_tool"):
            name = (arguments or {}).get("name") if isinstance(arguments, dict) else ""
            if name in _INTEGRATIONS:
                return _INTEGRATIONS[name]
            if name and ("chart" in name or "table" in name or "widget" in name or "html" in name):
                return "Putting together a view for you", "coding"
            return "Looking that up", "researching"
        return _TOOLS.get(tool, ("Working on it", "thinking"))
    nice = _MCP_NAMES.get((server or "").lower(), "a connected app")
    return f"Using {nice}", "researching"


def reasoning_heading(summary: str) -> str:
    """The latest heading of a reasoning summary ("**Checking the forecast**"), if it reads as plain words."""
    headings = re.findall(r"\*\*([^*\n]{3,80})\*\*", summary or "")
    for heading in reversed(headings):
        heading = heading.strip().rstrip(".:")
        if heading and not _CODEY.search(heading) and not _COMMANDY.search(heading) and len(heading) <= 60:
            return heading
    return ""


def describe(kind: str, content: str = "", data: dict[str, Any] | None = None) -> tuple[str, str]:
    """(plain words, activity) for one work item. The activity is one of thinking, coding, reviewing,
    researching, delegating."""
    data = data or {}
    if kind == "command":
        return describe_command(content, data.get("actions"))
    if kind == "file_change":
        changes = data.get("changes") or []
        if len(changes) == 1:
            verb = {"add": "Creating", "delete": "Removing"}.get(changes[0].get("kind"), "Editing")
            return f"{verb} {_name(changes[0].get('path'))}", "coding"
        return (f"Editing {len(changes)} files" if changes else "Editing files"), "coding"
    if kind == "tool":
        return describe_tool(data.get("source") or "", data.get("tool") or content, data.get("arguments"),
                             data.get("server") or "")
    if kind == "web_search":
        query = (content or "").strip()
        if not query or re.match(r"^[a-z]+://", query, re.IGNORECASE):
            return "Reading a web page", "researching"
        return f"Searching the web for “{query[:60]}”", "researching"
    if kind == "subagent":
        return "Working with a helper", "delegating"
    if kind == "image_view":
        return "Looking at an image", "reviewing"
    if kind == "review":
        return "Reviewing the changes", "reviewing"
    if kind == "reasoning":
        return reasoning_heading(content) or "Thinking it through", "thinking"
    return "Working on it", "thinking"
