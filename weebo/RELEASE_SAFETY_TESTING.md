# Release safety fixes and acceptance record

## Restart/status regression checks (2026-10-18)

The three user-approved in-app upgrades through `4d65999` are preserved. Restart fixes are limited to status
reconciliation and UI lifecycle: expired captions release their timers, live engine/activity events supersede
in-flight bootstrap snapshots, reconnect greetings carry current state, and ready/error/stopped states have
accurate captions and moods. Repeated restart events reuse one screen with an independent syncing avatar.
The screen waits for the **replacement session**, not ten elapsed seconds or an old process answering HTTP.
Each probe has a five-second timeout and slow restarts show elapsed time instead of promising a few seconds.

`tests/weebo/test_restart_ui.py` covers these in a mobile-sized Chromium browser, including a temporary outage
followed by the old server returning, then a genuinely new session. `test_server.py` verifies live reconnect
greetings and the restart marker in status. Quiet hours are pinned in these fixtures, not changed in user settings.
All nine new browser cases were also run with the pre-fix JavaScript served from Git: all nine failed, then passed
with the fixes. The CI unit job now installs Chromium too, so those regressions execute in both required lanes.
Windows validation: selftest OK; all 13 JavaScript modules parse; Weebo lane 840 passed / 1 existing symlink skip;
repository unit lane 1,601 passed / 2 existing platform skips / 7 integration-smoke deselections (13 subtests passed).
For live acceptance after deployment: restart the existing supervised instance, verify a changed process/session,
wait for `/api/status` engine `ready`, and verify the page has no restart overlay or stale Connecting caption.
Native outward-action/Twilio acceptance below remains separate and is not enabled by these UI fixes.

Base: `33be7ca` (including its time-independent quiet-hours tests). Scope: Weebo only; no keys, existing model
settings, Weebo 1.x, user database or running application changed. Direct user-to-bot budget exemption is unchanged.

## Implemented

- Outward bot requests handled by Weebo require a one-use user approval bound to the complete request, not just
  the tool name. Old always-allow rules are ignored for sends, spending, contacting people and non-read connector
  requests. An approval for one recipient/body/command cannot authorize another. Read rules still work.
  Approval hashes include full arguments even when the display preview is capped. Network/permission escalations
  also need a fresh approval; bots do not inherit the chief's unrestricted Full-mode network sandbox.
- Native bot/owned-agent startup reads effective config, disables unassigned MCP servers and apps, and allows only
  declared read tools on assigned connectors. It reapplies controls on resume, replaces old-policy bot threads,
  and fails closed if controls are missing/rejected. Native outward tools are deliberately unavailable for now.
- Connector stdio children receive basic OS launch variables (PATH, Windows system/command paths, temporary paths,
  locale) plus exact manifest `env_names`, in onboarding and normal Codex runtime. No credential values are written
  to manifests or logs. The runtime launcher is the same filter as the test client, not a shell command string.
- Briefs and Ideas/drafts are buffered before publication, including deltas, commentary, history and completion
  notifications. Checks finish before release; mismatch/unverified warnings precede the answer. Stop discards it.
  Normal chat remains streaming, with advisory checks afterwards. The existing second-pass setting and budget apply.
- Twilio acceptance is not delivery: the scheduler polls the returned message ID after 60 seconds, up to five times.
  Pending checks are durable. Delivered/failure/unverified outcomes go to the chief's chat and journal, without a
  bot notification or automatic resend. Credentials/account mismatch and lookup errors end as unverified, not sent.

## Automated checks

`tests/weebo/test_release_safety.py` covers per-action consent and argument changes, old rules, actor restrictions,
environment filtering (including a real subprocess), manifest validation, native allowlists/resume, unsupported
Codex refusal, schema requirements, buffered publication, Stop, failed checks, normal streaming and bounded SMS reads.
`test_weebo3_ui.py::test_publication_gate_and_per_action_card_in_browser` reloads the page while review is pending,
checks the final warning and verifies that outward approval cards show details and have no Always allow button.
Quiet hours are pinned in browser tests, not inferred from the machine's wall clock.

Existing tests were adjusted only for intentionally changed contracts: outward user rules are now one-use/exact,
native subprocess execution goes through the environment-filtering launcher, and rejected connector config must
not trigger an unprotected retry. Their original safety assertions are retained or strengthened.

Required before push:

```powershell
python -m weebo --selftest
python -m pytest tests/weebo -q -p no:cacheprovider
$env:PYTHONPATH='.'
python -m pytest -q -m 'not integration and not smoke' -p no:cacheprovider
```

Also parse every `weebo/web/js/*.js` with `weebo.evolution.gates.check_js`.

Final local verification on Windows / Python 3.14, 2026-10-17:

- Selftest: **OK**.
- Weebo suite: **792 passed, 1 existing platform skip**.
- Repository unit lane: **1,553 passed, 2 existing platform skips, 7 integration/smoke tests deselected**;
  all 13 subtests passed. The skips are unavailable symlink support and `notify-send`.
- JavaScript: **all 13 modules parse**.
- Installed-Codex synthetic isolation probe: **PASS** (details below).

The only warning is ChromaDB's upstream use of deprecated `asyncio.iscoroutinefunction` on Python 3.14.
These results cover the final production code and regression tests; only this verification record was updated
afterwards. CI and the unchecked live acceptance items below are separate, unverified results.

## Local Codex diagnostic

```powershell
python -m weebo.connectors.check_isolation --probe
```

The script selects the installed Codex, regenerates its own protocol schema, then starts a disposable app-server
with only two harmless synthetic MCP connectors. It uses the production per-thread configuration builder. No
credentials/config are copied from the user's Codex home, no model turn runs, and no real connector action occurs.
It verifies allowed-tool visibility and a successful harmless read, blocked-tool invisibility and rejection of a blocked direct call. Exit 0
means the synthetic checks passed; exit 2 is unsupported/inconclusive/failure, never a pass.

**Observed locally, 2026-10-17:** Codex **0.163.0**, required schema fields present; synthetic isolation **PASS**;
allowed tools visible, blocked tools hidden, blocked call rejected. This is NOT a real-app approval certification.

## Live acceptance still required before permitting native outward tools

### PC follow-up, 2026-10-17

The earlier synthetic PASS did **not** cover real configuration inheritance. Live checks found two actual defects:
empty `mcp_servers` / `plugins` tables merge with user/project layers rather than removing their grants, and
remote-installed plugins / plugin MCP sidecars can be absent from `config/read`. Bot profiles now explicitly deny
the inherited servers and plugins discovered through configuration **and** installed/runtime inventories, clear
inherited per-tool app grants, and disable the apps feature when no app is assigned. Existing bot and owned-agent
threads get a new policy hash so old-policy threads are replaced through the existing housekeeping path.
The hash also covers the effective native controls: a newly inherited server/plugin invalidates an already-loaded
bot thread or a continued owned-agent thread, not just threads resumed after a restart.

The synthetic probe now uses a real temporary `config.toml` layer and advertises a real synthetic outward tool
(which must remain hidden). The new live check uses this PC's installed Codex and existing sign-in without copying
credentials or changing global settings:

```powershell
python -m weebo.connectors.check_live_isolation
# Optional: two harmless model turns, persisted resume, and a diagnostic-only app-server restart:
python -m weebo.connectors.check_live_isolation --resume-turns
```

**Observed on Codex 0.163.0: PASS**, including both resume turns and the app-server restart. The effective inventory
contained 8 native server names and 37 local/remote plugin identifiers. Two synthetic bot scopes each exposed only
their assigned read tool, rejected the other bot's marker, and hid outward tools. Revocation produced an empty
scope. Two actual apps each reported only the assigned app as callable, with no plugin MCP sidecars escaping scope.
The diagnostic uses `app/installed` for effective app permissions: the raw `mcpServerStatus/list` discovery catalog
alone is **not** proof that an app is callable. A separate authorized read-only Calendar check succeeded while
assigned and was rejected after revocation; no calendar changes were made.

No real texts, emails, calls or bookings were sent. Twilio's account SID, auth token and sender number are absent
from Weebo's environment and its `.env` loading path. A live SMS test requires setup plus the user's chosen test
recipient and explicit send consent. No keys were moved or changed. Native outward tools remain disabled: these
read/isolation checks do not certify native per-action approvals, command/network escape resistance, every
connector, or real-source briefing accuracy. Those remaining checks below are intentionally still open.

Regression coverage: `tests/weebo/test_live_connector_controls.py` covers inherited configuration and remote plugin
discovery, runtime-only sidecars, credential-free inventories, stale per-tool grants, incomplete inventories, and
complete pagination, plus thread replacement for both loaded bots and continued owned agents after native scope
changes. The existing resume test now checks explicit deny entries instead of incorrectly assuming
an empty table replaces an inherited table.

Final checks for this follow-up: **804 Weebo tests passed** (1 existing platform skip), **1,565 repository unit
tests passed** (2 existing platform skips, 7 integration/smoke tests deselected, 13 subtests passed), selftest OK,
all 13 JavaScript modules parse, and both native diagnostics passed. Only the existing ChromaDB deprecation warning
remains. The final production code and tests were unchanged during these runs; this result record was added after.
A temporary copy of this PC's schema-3 database migrated to schema 5 with integrity OK and existing conversation,
message, memory, task and reminder counts preserved. A verified local backup was kept before launching the app.
This follow-up's CI status is not implied by the local results.

### Remaining acceptance checklist

- [ ] With the user's real connector configuration, verify two bots see only their assigned read tools, including
  after restart, connector revocation and continued owned-agent work. Include apps with existing per-tool overrides.
- [ ] Using a dedicated test connector/recipient, prove that an outward call pauses **before** its side effect;
  Deny produces zero effects, Allow once produces exactly one, and the next call asks again. Include direct bot
  chat, delegated agents and resumed threads. Do not test by emailing/calling a real person without explicit consent.
- [ ] Verify command/sandbox/permission escalation cannot bypass this rule. Command-name heuristics and tool risk
  labels are not a security boundary. Trusted connector code still runs with the user's filesystem permissions.
- [ ] If these fail, keep native outward tools disabled. Consider separate connector-only Codex environments as
  a separately reviewed change; this patch does not silently enable them or install an OS sandbox.
- [ ] Optional authorized Twilio smoke: send exactly one message to the user's chosen test number and confirm the
  later delivery record matches Twilio. Do not resend when status remains unknown. Automated tests use fake HTTP.
- [ ] Real-source brief: move/cancel a test event during generation, reload while checking, and verify a corrected
  or explicitly flagged output. A model checker is not proof of comprehensive factual coverage.

References used for protocol checks: [Codex app-server](https://developers.openai.com/codex/app-server),
[config reference](https://developers.openai.com/codex/config-reference), and
[Twilio Message resource](https://www.twilio.com/docs/messaging/api/message-resource). The installed binary's schema,
not a guessed API field, determines support.
