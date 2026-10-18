# Weebo 2.0

**Your proactive, self-evolving AI companion.** Weebo thinks with **Codex on your ChatGPT plan** (no pay-per-token
API billing), works on your computer, runs several agents at once, remembers what matters, checks in on its own,
and upgrades its own code safely.

<p align="center"><img src="weebo/web/icon.svg" width="96" alt="Weebo"></p>

## Weebo 3: a chief of staff with a team

Weebo is now a **chief of staff**: the one Weebo you talk to (the main chat). It delegates, keeps the overview,
decides what reaches you, and is the only one that ever interrupts you. The delivery ledger is
[weebo/WEEBO3_PLAN.md](weebo/WEEBO3_PLAN.md).

**Bots (the team).** Long-lived specialists, each with one continuous chat and one Codex thread, its own model tier
(fast, standard or most capable, mapped at runtime from the models your Codex offers; pin them in Settings if you
like), effort, tools, connectors, autonomy, daily budget and routines. Bots get jobs from the chief (or from you
directly in their chat) and report back with a three-line summary; only that summary reaches the chief, never the
transcript. Bots never talk to each other: the chief relays, with a hop limit so nothing ping-pongs. A bot that is
stuck, keeps failing or goes over budget is reported, never silent.
- Weebo **proposes** bots (with a write-up: job, goals, tier, tools, autonomy, budget, routines and why); a card in
  the chat lets you approve, edit or decline. A typed "yes" works too. Bots can never create bots. On first run it
  proposes a small starter team (Mail, Home, Research, Coder); nothing is created until you approve.
- The chief may change a bot freely (wording, routines, pausing, a cheaper tier, rules you stated). New tools or
  connectors, more autonomy, a bigger tier, effort or budget, creating and deleting all wait for your yes.
  Retiring archives by default (the chat stays readable, its thread is put away); deleting needs your explicit word.
- The bottom **Bots** tab is a vertical conversation list: tap a specialist to open its existing continuous chat.
  It shows the latest message, actual working/paused status, and unread messages. **Manage bots** in the sidebar or
  More holds profiles, tools, budgets and reporting rules; Profile in a bot chat opens the same controls.
  Unsent text and attachments stay with their original chat when switching, including delayed send failures.
  When you tell a bot how to work, it saves a standing rule and tells the chief. A bot can ask for a profile change;
  the chief reviews it under the same tiers.
- The morning brief folds in a team digest: reports, what was decided for you, what waits on you, and per-bot usage
  with a hint when a bot barely uses its tier. (The digest is built by code; it costs no extra model turns.)

**Approvals and "don't bug me".** Three tiers: a bot just does what its autonomy allows; the chief approves what
matches a standing rule you set ("decided for you", listed in the brief); you decide the hard gates (sending,
paying, deleting, contacting people, new tools, bigger caps). A bot never waits on you: what needs you goes onto one
bundled **needs you** card in the chat ("Allow once", "Always allow", "Deny"), and only urgent items reach your phone.
For sending, spending, contacting people and non-read connector requests, **Allow once** is required for the exact
action each time (recipient, body and arguments included); legacy "always" rules cannot approve these. The card
shows action details and hides **Always allow** for them. Other "Always allow" rules remain revocable. Reports go to **interrupt**, **brief** or **log** by rules
learned from what you say ("I don't care about nightly PR fixes" → log); log-only stays searchable, and trouble always
reaches at least the brief.

**Connectors.** Your ChatGPT apps (through Codex's apps feature, when your Codex offers `app/list`) and MCP servers,
including thin wrappers around your own programs ([template](weebo/connectors/template/README.md)). Every connector
is registered from a manifest, **tested with your authorization** (Weebo starts a separate process, lists its tools,
checks them against the manifest and makes declared read-only test calls), **approved** by you with a risk label per tool (tools that spend money or
contact people need a rule in your words), **assigned** to bots as a gated change, and **monitored** (one that keeps
failing is flagged to the chief). Onboarding is not an OS sandbox; only test trusted programs. Weebo scopes its own
dynamic tools and reads effective Codex config, installed plugins, and native tool inventory before bot/owned-agent
startup: inherited servers, plugin sidecars and unassigned apps are explicitly disabled, and assigned connectors
expose **declared read tools only**. Empty config tables are not treated as removing inherited grants. Native outward tools remain unavailable until
real-app pre-execution approval is accepted in live testing. If required controls are unavailable or rejected, bot
startup fails visibly instead of retrying without protection. Normal chief chat is unchanged.
Stdio testing and runtime pass only basic OS launch variables plus the manifest's exact `env_names`; no blanket
inheritance of Weebo's secrets. This is not a filesystem sandbox. See the
[release fixes and live acceptance checklist](weebo/RELEASE_SAFETY_TESTING.md), including the synthetic probe and
`python -m weebo.connectors.check_live_isolation` for your actual configuration. Add `--resume-turns` to spend two
harmless Codex turns verifying persisted resume and a diagnostic app-server restart; its test threads are archived.

**Feed: goals, tracking, and ideas.** A compact, expandable list brings together **Needs you**, **Goals**,
**Tracking**, and **Ideas for you**, with All/Goals/Ideas/Tracking filters. Needs you links to the existing approval
cards, never a bypass. Tracking is derived from real jobs, reminders, routines and open commitments; a reminder
is labeled as a reminder, not pretend monitoring. The Day sheet and completed/archived goals are expandable below.

Ideas can cite connected facts, saved memories, exact user messages, or chosen goals. Remembered goals and projects
produce local starter suggestions even with no calendar/email connected; richer suggestions from the chief use
the same source checks, dedupe, daily cap and feedback. A source chip opens the underlying record and its provenance.
Remembered statements are explicitly **not independent verification**. Edited/deleted local evidence invalidates
dependent ideas, just as changed connector facts do. **Tomorrow** snoozes for 24 hours; **Not useful** stays dismissed.

**Start a goal** records your chosen outcome, source idea/memory, next step, deadline and blocker in a forward-only
schema-6 migration. It launches nothing. Edit, pause, complete, archive or reopen it; history stays readable.
**Delegate** explicitly starts one existing bot job under its normal budget and approval rules, linking its result
back to the goal. Double taps cannot create duplicate goals or overlapping jobs. A completed job does not mark the
goal achieved: you review the result. **Link existing work** attaches an existing job, reminder or commitment without
copying or rescheduling it. Pausing/archiving a goal does not cancel linked work; those systems keep their own controls.
The chief reads goals with `goals_list`, exact local sources with `feed_source`, and records explicit user choices
with `goal_save`/`goal_link`. Background turns cannot choose personal goals on your behalf. No new execution engine,
model settings, connector grants, or autonomous spending are introduced.

**Day sheet.** For the next days, each event is enriched by code from the facts store and real sources:
weather at its time and place (OpenWeather forecast, your own key), travel time **only from a directions source**
(otherwise "unknown", never guessed, and a "maps/directions connector would help" gap appears on the Capabilities
page), time zones, conflicts and back-to-backs, related email threads and the people involved. A moved or cancelled
event rebuilds exactly the rows that depended on it. **Ideas for you** in the Feed shows suggestions with evidence chips
("Dentist starts 10 min after you leave the Office; I can't check the drive, so plan a buffer"), capped per day,
never repeated, ranked by your feedback, and dropped when their facts change. **Discuss** opens the chat with the idea
and its evidence; **Do it** runs only safe actions (a reminder, a held slot as a reminder, a draft) after re-reading
the evidence and verifying the result; **Not useful** teaches Weebo (and is remembered as a preference).
Draft ideas stay pending until the chief finishes an answer; failed, stopped or restart-interrupted drafts stay
retryable. Brief reports are acknowledged only after a completed, nonempty chief answer. Day-sheet rows refresh
when related email/people are added and when enrichment reaches the configured facts TTL; missing locations are
reported as unknown, never "same place".

**Growth.** Connectors are what Weebo can do, skills are procedures, workflows are repeatable multi-step jobs across
bots. Weebo proposes workflows when you ask for substantially the same thing a few times, runs approved ones step by
step through bots (each step gets the previous result), and turns recurring capability gaps into a proposal: an
existing ChatGPT app with its install link, or an MCP wrapper built by self-evolution (isolated worktree, tests,
gates) that then goes through onboarding. Idle bots, unused connectors and failing workflows are questioned, never
removed on their own. The **Capabilities** page lists all of it with who uses it, risk, use and status.

**Always know what it's doing.** One "now showing" line under the avatar shows the most important thing going on
(your reply being written, then anything waiting on you, then work you asked for, then background work), with
"+N more" for the rest. Every state comes from real events: when something goes quiet it says so ("Still running the
tests · 3 min"), idle reads as resting, quiet hours as sleeping. New animations: typing on a laptop (coding and
writing), a phone (calls), holding up a note (waiting on you), syncing arrows, plus the existing reading, research
and hand-off props. Reduced motion (the setting or your system preference) keeps the props and drops the motion.
Startup and reconnect refresh the live engine/activity state, so a stale snapshot cannot leave "Connecting"
on screen after Codex is ready. Upgrade restarts show one stable syncing pose and elapsed time until a **new server
session** answers; an old process still responding or a brief network outage does not count as a restart.

**Reliability.** Facts about events, email, people and commitments come from the facts store with provenance and
freshness, with instructions to look them up in the same turn (see the facts section below). Brief/idea responses
receive a deterministic check for supported timing and cancellation claims, and a fast-tier second pass can review
stored-fact claims (setting `facts.second_pass`). Briefs and Ideas/drafts are held out of the websocket and chat history
until checking finishes. Mismatches or incomplete checks are flagged **above** the answer before publication. Stop
discards the withheld answer. Normal chat still streams and receives advisory checks afterwards. This does not prove
every factual statement: unsupported claims and failed/disabled checks are not certified as correct.
Calendar bookings are read back and checked for matching identity, title, description and start/end instants;
ambiguous or nonexistent local booking times require an explicit offset. Texts are checked against Twilio's own
acceptance response, then the original message ID is polled after about a minute (up to five checks). Delivered,
failed and unverified outcomes appear in the chief's chat; pending checks survive restarts and never resend the text.
Email threads are recorded from a mail connector's raw tool results,
never from a model's summary; unread alone does not mean a reply is owed. The
reliability suite gates every upgrade, and every captured miss becomes a test.

**Layout.** Bottom tabs: Chat (the chief), Feed, Bots (conversations), Evolution, More (Manage bots,
Agents for one-off jobs, Memory, Capabilities, Schedule, Activity, Settings, older chats).

New settings: `bots.*`, `connectors.*` and `growth.*` (sensitive: only you can change them, journaled), `ideas.*` and
`activity.*` (everyday), `facts.second_pass`. Existing model settings and keys are unchanged.

**Not available yet, said plainly:** no directions source ships with Weebo (travel time stays unknown until you
connect one); ChatGPT apps and per-thread connector config work only when your installed Codex offers them (Weebo
reads its schema and says so otherwise); native outward connector tools remain disabled pending live acceptance.
Your own messages to bots retain the existing budget exemption; autonomous bot work still obeys its caps.

### Facts foundation (Phase 1)

- **Source-backed facts:** events, email-thread records, people and commitments
  have provenance, freshness metadata and a durable change log in SQLite. The
  migration preserves existing conversations, memories and settings.
- **Calendar sync:** an existing Google Calendar sign-in can feed incremental,
  paginated sync. Expired sync tokens trigger reconciliation without deleting
  change history. No new OAuth flow, credential adoption or scope grant is
  initiated by this service. An absent source is explicitly unavailable, not an
  empty inbox.
- **Fresh tools:** `facts_query`, `facts_changes_since`, `commitments_list`,
  `commitment_add`, `commitment_update` and `day_sheet`. Stale facts are re-fetched; a failure
  excludes them from verified results. Open-item pointers appear in turn context,
  and facts lookup receipts are isolated by turn. User-owned commitments cannot
  be created or changed by an autonomous turn.
- **Inspect the source:** Memory → Source-backed facts shows verified records,
  where they came from and when they were checked.
- **Deterministic time math:** DST-aware local days, ambiguous/nonexistent local
  times, overlaps, back-to-back meetings, due items and cross-timezone travel
  windows.
- **Change dependencies:** moved/cancelled/changed facts atomically invalidate
  dependent artifacts (day-sheet rows, ideas); builders regenerate them from the current records.
- **Mandatory reliability gate:** deterministic scenarios run independently
  of model budgets and the optional behavior-eval setting. The running version
  supplies the test source, so a candidate cannot pass by deleting its suite.
  Factual corrections captured as evals also create reliability incidents.

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
| **One chat** | Everything happens in a single conversation: talk, briefs, routines, agent results, upgrades that need you. Weebo keeps up with it all: each turn knows what agents are doing, what finished today and which upgrades are waiting, and `search_history` looks back through everything said (and older chats, kept to read under **More → Older chats**). A tab bar (Chat, Ideas, Bots, Evolution, More) gets you everywhere else; Agents, Memory and Capabilities live under More. |
| **Weebo delegates** | The chat talks, remembers, plans and keeps track; real work (commands, files, code, web research) goes to background Codex agents that run **in parallel** while you keep chatting. Each agent has a live card in the chat that says what it's doing in plain words ("Running the tests"), with a Stop button; when it finishes, Weebo tells you what came back. Follow-up work on the same job continues that agent's own session, so it remembers what it did. (Settings → `chat.delegate_work` turns this off and lets the chat work itself.) |
| **Tidy in Codex** | Your Codex app shows one Weebo thread, the chat. Weebo archives agent and upgrade-build threads in Codex once they finish (restorable from Codex's archive; Weebo brings one back itself when work continues it), and background thinking never saves a thread at all. |
| **Just ask** | Tell Weebo to change a setting, check on or redirect an agent, show you a build, build or reject an upgrade, or "merge them all and tell me when it's done": it merges them, restarts once, and reports back in the chat. Upgrade cards in the chat also have Merge / Rebuild / See changes buttons. Results come to you in the chat instead of an inbox to check. |
| **Reply chips** | When the next step is obvious, Weebo offers a few tap-to-send replies under its answer. |
| **Easy to read** | While Weebo works it says what it's doing in plain words, never the command it's running, and its avatar shows it: thinking, coding, reviewing, researching or handing work to an agent. `Esc` stops a turn; typing while it works adds to it. |
| **Memory** | Weebo remembers durable facts, preferences, goals and people, injects the relevant ones into every turn, and never stores secrets. Recall is hybrid: full-text search plus optional **semantic search** with a small embedding model on your computer (`pip install fastembed`), so "my kid" finds the memory about your daughter. When you correct Weebo, it saves the lesson right away. Your Weebo 1.x memories are imported on first launch. |
| **Proactive** | Morning brief, reminders and **routines** (scheduled instructions Weebo runs itself), agent reports, and **dreaming**: while you're away it consolidates memories, summarizes what happened, and leaves you ideas in the chat. |
| **Self-evolution** | Weebo proposes upgrades to itself (from your requests, its own failures, or self-audits that go where the code changed and the failures are), builds them in an isolated git worktree, verifies them, and merges with your approval. It then checks whether each fix actually held. See below. |
| **Behavior checks** | Moments Weebo got wrong (your corrections, failures its dreams notice) become replayable checks. They run nightly, and any upgrade to Weebo's brain or memory must pass at least as many as the running version. |
| **Skills** | Procedures Weebo figures out are saved as Codex-native `SKILL.md` files that every future thread can use. Weebo counts how often each is used, learns new ones while dreaming from agent work that repeats, and archives the ones it learned that way if nobody uses them; skills saved in a chat stay. |
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
* **Tests can't be gamed:** the build agent may edit tests, so the original version of every existing test file it
  changed is re-run against the new code and shown to you, and a change that touches existing tests (even by only
  adding lines, which can skip or replace them) never merges automatically. The checks run on exactly what would
  merge: files git ignores are deleted before they run.
* **Asking in the chat:** saying "merge them", "build it", "restart" or changing a safety setting (evolution mode,
  protected paths, autonomy level, server) works only in a turn you started with your own message, never in one
  Weebo started itself (an agent's report, a routine, a brief), so text in a report or a web page can't trigger it.
  Each is recorded in the journal.
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
                         ├─ the chat's thread (Weebo's tools as dynamicTools)
                         ├─ agent threads     (parallel background jobs; continued for follow-ups, archived when done)
                         ├─ ephemeral threads (self-audit, dreams, Council, evals, Codex code review: never saved)
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
(imported from `ai_assistant/core/data` on first launch; the Google Calendar `credentials.json` there is copied
into `weebo_data/google` at launch, and a 1.x calendar sign-in the first time you use the calendar). The 1.x code
can be deleted; its data files are untracked and stay on disk.
