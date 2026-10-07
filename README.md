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
| **Memory** | Weebo remembers durable facts, preferences, goals and people, injects the relevant ones into every turn, and never stores secrets. Recall is hybrid: full-text search plus optional **semantic search** with a small embedding model on your computer (`pip install fastembed`), so "my kid" finds the memory about your daughter. When you correct Weebo, it saves the lesson right away. Your Weebo 1.x memories are imported on first launch. |
| **Proactive** | Morning brief, reminders and **routines** (scheduled instructions Weebo runs itself), agent reports, and **dreaming**: while you're away it consolidates memories, summarizes what happened, and leaves you ideas on **Weebo's Desk**. |
| **Self-evolution** | Weebo proposes upgrades to itself (from your requests, its own failures, or self-audits that go where the code changed and the failures are), builds them in an isolated git worktree, verifies them, and merges with your approval. It then checks whether each fix actually held. See below. |
| **Behavior checks** | Moments Weebo got wrong (your corrections, failures its dreams notice) become replayable checks. They run nightly, and any upgrade to Weebo's brain or memory must pass at least as many as the running version. |
| **Skills** | Procedures Weebo figures out are saved as Codex-native `SKILL.md` files that every future thread can use. Weebo counts how often each is used, learns new ones from agent work that repeats, and archives auto-learned skills nobody uses. |
| **Integrations** | Web/news/image search, deep research, weather, location, Google Calendar, SMS (Twilio) and chart/table widgets, using your own keys from `.env`. Calendar writes and texts ask you first. |
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
* **Self-evolution gates:** ideas Weebo had on its own first go to the **Council** (a Skeptic argues against, a
  Judge decides), which sees how similar past upgrades turned out. Every build must then pass Python/JS syntax
  checks, a check that it doesn't hardcode who you are (your name or email), a boot self-test, the full test
  suite and a Codex code review (P0/P1 findings block). Changes to Weebo's brain or memory are also rehearsed
  against the behavior checks and must not do worse than the running version. Failures go back to the build agent
  for up to two revisions.
* **Tests can't be gamed:** the build agent may edit tests, so the original version of every test it rewrote is
  re-run against the new code and shown to you, and a change that rewrites or deletes existing tests never merges
  automatically.
* **Change policy** (`weebo/evolution/policy.py`) classifies every touched file: only UI, new tests, docs and
  skills may merge without you; core, execution and governance code (including CI, pytest config and dependency
  files) always wait for your approval, as does anything under *Protected paths* in Settings. If an upgrade fails
  to boot, the supervisor reverts it automatically.
* **Outcomes:** an upgrade that names the recorded failures it fixes is watched after merging. If a failure comes
  back, you're told and the next self-audit looks at it again; if not, the fix is marked as held.

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
* `weebo/agents/`, `weebo/proactive/`, `weebo/memory/` are what they sound like.
* `weebo/evolution/` builds and gates upgrades (`engine.py`, `gates.py`), decides what may merge without you
  (`policy.py`), vets Weebo's own ideas (`council.py`), runs behavior checks (`evals.py`) and tracks whether
  merged fixes held (`outcomes.py`).
* `weebo/integrations/` holds the abilities beyond Codex's own (search, research, calendar, SMS, widgets).

## Development

```bash
python -m pytest tests/weebo -q     # Weebo 2.0 test suite (offline, uses a fake Codex engine)
python -m weebo --selftest          # boot self-test (the same gate self-evolution uses)
python -m weebo --child --no-browser  # run the server without the supervisor
python -m playwright install chromium  # once, to run the browser UI tests too
```

Runtime data lives in `weebo_data/` (git-ignored): database, logs, workspace, skills, worktrees, settings.

## Weebo 1.x

Weebo 2.0 no longer imports anything from the original assistant (`web_app.py`, `ai_assistant/`, `weebo/legacy/`).
What was worth keeping moved into 2.0: the change policy (`weebo/evolution/policy.py`), the integrations
(`weebo/integrations/`, reimplemented without the 1.x tool registry), the Council, the voice flow and your memories
(imported from `ai_assistant/core/data` on first launch; Google Calendar credentials there are adopted too). The
1.x code can be deleted; its data files are untracked and stay on disk.
