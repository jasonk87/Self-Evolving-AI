# Weebo 2.0

**Your proactive, self-evolving AI companion.** Weebo thinks with **Codex on your ChatGPT plan** (no pay-per-token
API billing), works on your computer, runs several agents at once, remembers what matters, checks in on its own,
and upgrades its own code safely.

<p align="center"><img src="weebo/web/icon.svg" width="96" alt="Weebo"></p>

## Quick start

1. **Install Codex and sign in with ChatGPT.** Either the Codex desktop app, or `npm i -g @openai/codex` then
   `codex login`. Weebo automatically uses the newest Codex it finds (the desktop app usually ships the newest).
2. **Install Python deps:** `pip install -r requirements.txt`
3. **Start Weebo:** double-click `Weebo.bat` (Windows), run `./weebo.sh` (macOS/Linux), or `python -m weebo`.
   It opens <http://127.0.0.1:5050>.

If Codex isn't signed in, Weebo shows a **Sign in with ChatGPT** button in Settings.

## What Weebo can do

| | |
|---|---|
| **Chat that actually does things** | Weebo is Codex with a personality: it runs commands, edits files, searches the web and uses your Codex plugins (Gmail, Calendar, GitHub, …). Work streams in live as compact "Worked for 12s · 3 commands · 2 edits" groups you can expand. |
| **Steer mid-turn** | Type while Weebo is working and your message joins the running turn. `Esc` stops it. |
| **Parallel agents** | For big jobs Weebo launches background Codex agents (`start_agent`) that run **simultaneously** while you keep chatting. Watch them live in **Agents**, message or stop them; when one finishes, Weebo tells you in the chat. |
| **Memory** | Weebo remembers durable facts, preferences, goals and people (SQLite + full-text search), injects the relevant ones into every turn, and never stores secrets. Your Weebo 1.x memories are imported on first launch. |
| **Proactive** | Morning brief, reminders and **routines** (scheduled instructions Weebo runs itself), agent reports, and **dreaming**: while you're away it consolidates memories, summarizes what happened, and leaves you ideas on **Weebo's Desk**. |
| **Self-evolution** | Weebo proposes upgrades to itself (from your requests, its own failures, or daily self-audits), builds them in an isolated git worktree, verifies them, and merges with your approval. See below. |
| **Skills** | Procedures Weebo figures out are saved as Codex-native `SKILL.md` files that every future thread can use. |
| **Weebo 1.x abilities** | The old tool registry is bridged in: Google search, deep research, image search, weather, location, Google Calendar, SMS and chart/table widgets, all now thinking with Codex too. |
| **Live widgets** | Weebo can reply with an interactive chart, calculator or mini game that renders in a sandboxed frame. |
| **Voice** | Talk to Weebo with the mic button; turn on *Speak replies* and its mouth moves while it talks. |
| **Approvals** | In Balanced mode Weebo asks before touching anything outside its workspace; approve once or for the whole chat. |

And it has a face: Weebo is an animated hover-bot that thinks, talks, works, sleeps, dreams, celebrates and
watches your cursor.

## Safety model

* **Autonomy levels** (Settings): *Cautious* (read-only, asks for every change), *Balanced* (writes freely only in
  its workspace and the chat's folder, asks before anything else), *Full trust* (no sandbox, like a
  `danger-full-access` Codex config).
* **Plan budget:** background work pauses above a usage ceiling (default 70% of your plan window), is capped per day,
  and respects quiet hours.
* **Local only:** the server binds to 127.0.0.1. Every API call needs a per-launch token sent in a header, Host
  headers are checked (no DNS rebinding), and cross-origin WebSockets are refused. Generated pages and widgets run
  in sandboxed, opaque-origin frames that can't reach the API.
* **Self-evolution gates:** every upgrade must pass Python/JS syntax checks, a boot self-test, the full test suite,
  a Codex code review (P0/P1 findings block), and Weebo 1.x's **Council** (Skeptic + Judge with a deterministic
  security veto). Failures go back to the build agent for up to two revisions. Weebo 1.x's **change policy**
  classifies every touched file: only UI, tests, docs and skills may ever merge without you; core, execution and
  governance code always wait for your approval. If an upgrade fails to boot, the supervisor reverts it
  automatically.

## How it works

```
 browser UI (weebo/web)  <-- WebSocket events / REST -->  aiohttp server (weebo/server)
                                                               |
                                    WeeboApp (weebo/app.py) ---+--- SQLite store, memory, scheduler, heartbeat
                                                               |
                         one long-lived `codex app-server` (JSON-RPC over stdio, weebo/codex)
                         ├─ chat threads      (one per conversation, Weebo's tools as dynamicTools)
                         ├─ agent threads     (parallel background jobs)
                         ├─ review/think threads (self-audit, dreams, Codex code review)
                         └─ calls back into Weebo: tool calls, approvals, questions
```

* `weebo/codex/` finds the newest Codex binary, speaks the app-server protocol, detects optional features from the
  binary's own schema, and restarts the engine if it dies.
* `weebo/brain/` holds the persona, the per-turn context (time, memories, running agents, reminders), Weebo's tools,
  conversations and approvals.
* `weebo/agents/`, `weebo/proactive/`, `weebo/evolution/`, `weebo/memory/` are what they sound like.
* `weebo/legacy/` bridges the Weebo 1.x tool registry; `ai_assistant/core/llm/codex_provider.py` routes every
  Weebo 1.x LLM call (router, DeepSeek/Gemini/Ollama clients) to Codex.

## Development

```bash
python -m pytest tests/weebo -q     # Weebo 2.0 test suite (offline, uses a fake Codex engine)
python -m weebo --selftest          # boot self-test (the same gate self-evolution uses)
python -m weebo --child --no-browser  # run the server without the supervisor
```

Runtime data lives in `weebo_data/` (git-ignored): database, logs, workspace, skills, worktrees, settings.

## Weebo 1.x

The original assistant (`web_app.py`, `ai_assistant/`) still runs. Weebo 2.0 kept and modified the parts that were
solid and rewrote the rest:

* **Kept and modified:** the tool registry (bridged into 2.0), the LLM provider interface (now with a Codex
  provider, and the default backend is Codex), the change policy (extended with Weebo 2.0 zones and used as the
  merge gate), the Council debate (callable on whole diffs, thinking with Codex), Google integrations, the
  HTML/chart widget tools, the voice module, and your memories.
* **Rewritten:** the orchestrator, planner and ReAct loop (Codex's agent loop replaces them), the Flask/SocketIO
  server with its mixed threading model, and the mission-control UI.
